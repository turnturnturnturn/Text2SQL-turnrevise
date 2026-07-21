from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from vanna.servers.base import ChatStreamChunk

from app.harness.clarification import (
    ClarificationAlreadyAnswered,
    ClarificationExpired,
    ClarificationService,
    ResumeExpired,
    ResumeInvalid,
    ResumeNotFound,
    ResumeReplay,
)
from app.harness.store import RunStore
from app.security.jwt_resolver import JwtUserResolver
from app.state.conversation_store import PostgresConversationStore
from app.state.memory_service import MemoryService
from app.grounding.plan_store import QueryPlanStore
from app.context_v2.store import ContextStore
from app.evidence import EvidenceService


class ClarificationAnswer(BaseModel):
    option_id: str


class ResumeRequest(BaseModel):
    token: str


def create_state_router(
    *,
    resolver: JwtUserResolver,
    memory_service: MemoryService,
    conversation_store: PostgresConversationStore,
    run_store: RunStore,
    query_plan_store: QueryPlanStore | None = None,
    clarification_service: ClarificationService | None = None,
    context_store: ContextStore | None = None,
    evidence_service: EvidenceService | None = None,
    clarification_resume_mode: str = "off",
    resume_handler=None,
    trace_service=None,
) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["agent-state"])

    def user_from_header(authorization: str | None):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Missing Bearer token")
        try:
            return resolver.resolve_token(authorization.removeprefix("Bearer ").strip())
        except ValueError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @router.get("/memories")
    async def list_memories(authorization: str | None = Header(default=None)):
        user = user_from_header(authorization)
        memories = await memory_service.list_for_user(str(user.id))
        return [asdict(memory) for memory in memories]

    async def set_memory_status(memory_id: str, authorization: str | None, action: str):
        user = user_from_header(authorization)
        method = memory_service.confirm if action == "confirm" else memory_service.reject
        memory = await method(str(user.id), memory_id)
        if memory is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return asdict(memory)

    @router.post("/memories/{memory_id}/confirm")
    async def confirm_memory(memory_id: str, authorization: str | None = Header(default=None)):
        return await set_memory_status(memory_id, authorization, "confirm")

    @router.post("/memories/{memory_id}/reject")
    async def reject_memory(memory_id: str, authorization: str | None = Header(default=None)):
        return await set_memory_status(memory_id, authorization, "reject")

    @router.delete("/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_id: str, authorization: str | None = Header(default=None)):
        user = user_from_header(authorization)
        if not await memory_service.delete(str(user.id), memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(
        conversation_id: str, authorization: str | None = Header(default=None)
    ):
        user = user_from_header(authorization)
        if not await conversation_store.delete_conversation(conversation_id, user):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/runs/{run_id}")
    async def get_run(run_id: str, authorization: str | None = Header(default=None)):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        if run is None or run.user_id != str(user.id):
            raise HTTPException(status_code=404, detail="Run not found")
        steps = await run_store.list_steps(run_id)
        return {"run": asdict(run), "steps": [asdict(step) for step in steps]}

    @router.get("/runs/{run_id}/evidence")
    async def get_run_evidence(
        run_id: str, authorization: str | None = Header(default=None)
    ):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        is_admin = user.metadata.get("role") == "admin"
        if run is None or (run.user_id != str(user.id) and not is_admin):
            raise HTTPException(status_code=404, detail="Run not found")
        plans = (
            []
            if query_plan_store is None
            else await query_plan_store.list_for_run(run_id)
        )
        if evidence_service is None:
            return {"run_id": run_id, "query_plans": plans}
        view = await evidence_service.build(run, is_admin=is_admin)
        # Keep the old field during Phase D rollout; its records are already redacted.
        view["query_plans"] = plans
        return view

    @router.post("/runs/{run_id}/clarify")
    async def clarify_run(
        run_id: str,
        answer: ClarificationAnswer,
        authorization: str | None = Header(default=None),
    ):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        if run is None or run.user_id != str(user.id):
            raise HTTPException(status_code=404, detail="Run not found")
        if clarification_service is None:
            raise HTTPException(status_code=404, detail="Clarification not available")
        try:
            if clarification_resume_mode == "off":
                child = await clarification_service.answer(
                    run_id, str(user.id), answer.option_id
                )
            else:
                child = await clarification_service.answer_with_resume(
                    run_id, str(user.id), answer.option_id
                )
        except ClarificationAlreadyAnswered as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ClarificationExpired as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(child)

    @router.post("/runs/{child_run_id}/resume")
    async def resume_run(
        child_run_id: str,
        request: ResumeRequest,
        authorization: str | None = Header(default=None),
    ):
        user = user_from_header(authorization)
        if (
            clarification_resume_mode == "off"
            or clarification_service is None
            or resume_handler is None
        ):
            raise HTTPException(status_code=404, detail="Resume not available")
        child = await run_store.get(child_run_id)
        if child is None or child.user_id != str(user.id):
            raise HTTPException(status_code=404, detail="Run not found")
        parent = await run_store.get(child.parent_run_id or "")
        if parent is None or not child.conversation_id:
            raise HTTPException(status_code=409, detail="Original conversation unavailable")
        finder = getattr(conversation_store, "find_instruction_by_hash", None)
        instruction = (
            await finder(
                child.conversation_id, str(user.id), parent.instruction_hash
            )
            if finder is not None
            else None
        )
        if instruction is None:
            raise HTTPException(status_code=409, detail="Original instruction hash mismatch")
        selection = await clarification_service.selected_evidence(parent.run_id)
        if selection is None:
            raise HTTPException(status_code=400, detail="Clarification evidence unavailable")
        try:
            claimed = await clarification_service.claim_resume(
                child_run_id, str(user.id), request.token
            )
        except ResumeNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ResumeReplay as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ResumeExpired as exc:
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        except ResumeInvalid as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        async def generate():
            try:
                async for component in resume_handler(
                    claimed, instruction, selection, authorization
                ):
                    chunk = ChatStreamChunk.from_component(
                        component, child.conversation_id, child_run_id
                    )
                    yield f"data: {chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as exc:
                yield f'data: {{"type":"error","data":{{"message":"{type(exc).__name__}"}},"conversation_id":"{child.conversation_id}","request_id":"{child_run_id}"}}\n\n'

        return StreamingResponse(generate(), media_type="text/event-stream")

    @router.get("/runs/{run_id}/context-manifest")
    async def get_context_manifest(
        run_id: str, authorization: str | None = Header(default=None)
    ):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        is_admin = user.metadata.get("role") == "admin"
        if run is None or (run.user_id != str(user.id) and not is_admin):
            raise HTTPException(status_code=404, detail="Run not found")
        if context_store is None:
            raise HTTPException(status_code=404, detail="Context manifest not found")
        manifest = await context_store.get_manifest(run_id)
        if manifest is None:
            raise HTTPException(status_code=404, detail="Context manifest not found")
        return manifest

    @router.get("/runs/{run_id}/trace")
    async def get_run_trace(
        run_id: str, authorization: str | None = Header(default=None)
    ):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        is_admin = user.metadata.get("role") == "admin"
        if run is None or (run.user_id != str(user.id) and not is_admin):
            raise HTTPException(status_code=404, detail="Run not found")
        if trace_service is None:
            raise HTTPException(status_code=404, detail="Trace not available")
        return {
            "run_id": run_id,
            "correlation_id": run.correlation_id,
            "events": await trace_service.list_events(run_id),
        }

    @router.post("/runs/{run_id}/cancel")
    async def cancel_run(
        run_id: str, authorization: str | None = Header(default=None)
    ):
        user = user_from_header(authorization)
        run = await run_store.get(run_id)
        is_admin = user.metadata.get("role") == "admin"
        if run is None or (run.user_id != str(user.id) and not is_admin):
            raise HTTPException(status_code=404, detail="Run not found")
        cancelled = await run_store.cancel(run_id, run.user_id)
        return asdict(cancelled)

    return router

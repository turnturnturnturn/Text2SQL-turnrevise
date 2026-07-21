from __future__ import annotations

import logging
from contextvars import Token
from typing import AsyncGenerator
from typing import Any

from vanna import Agent
from vanna.components import UiComponent
from vanna.core.audit import AuditEvent, AuditLogger
from vanna.core.audit import AuditEventType
from vanna.core.user import RequestContext

from app.business_client import BusinessServiceClient
from app.harness.context import (
    get_instruction_hash,
    get_instruction_text,
    get_run_id,
    reset_instruction_hash as _reset_instruction_hash,
    set_instruction_hash as _set_instruction_hash,
)
from app.harness.request import RequestHarness
from app.harness.request import classify_operation_kind
from app.evidence import build_evidence_artifact
from app.runtime.correlation import current_run_id
from app.harness.context import reset_resume_evidence, set_resume_evidence
from app.harness.models import RunRecord


logger = logging.getLogger(__name__)


def set_instruction_hash(value: str) -> Token[str | None]:
    """Bind an instruction hash to the current asynchronous request context."""
    return _set_instruction_hash(value)


def reset_instruction_hash(token: Token[str | None]) -> None:
    _reset_instruction_hash(token)


def get_current_instruction() -> str | None:
    """Return request-local text without persisting it in audit payloads."""
    return get_instruction_text()


class BusinessAuditLogger(AuditLogger):
    """Persist Vanna audit events through the write-side business service."""

    def __init__(self, client: BusinessServiceClient, *, fail_closed: bool = True):
        self.client = client
        self.fail_closed = fail_closed

    async def log_event(self, event: AuditEvent) -> None:
        event_data = event.model_dump(mode="json", exclude_none=True)
        # Email is not needed for traceability; user_id is the stable subject.
        event_data.pop("user_email", None)

        generated_sql = None
        parameters = event_data.get("parameters")
        if event_data.get("tool_name") == "safe_read_sql" and isinstance(
            parameters, dict
        ):
            sql = parameters.get("sql")
            if isinstance(sql, str):
                generated_sql = sql

        details = event_data.get("details", {})
        event_instruction_hash = (
            details.get("instruction_hash") if isinstance(details, dict) else None
        ) or get_instruction_hash()
        request_id = get_run_id() or event.request_id
        event_data["request_id"] = request_id

        payload: dict[str, Any] = {
            "eventType": str(event.event_type.value),
            "userId": event.user_id,
            "requestId": request_id,
            "generatedSql": generated_sql,
            "originalInstructionHash": event_instruction_hash,
            "success": bool(event_data.get("success", True)),
            "durationMs": round(float(event_data.get("execution_time_ms", 0))),
            "details": event_data,
        }
        try:
            await self.client.record_audit_event(payload)
        except Exception:
            logger.exception("Unable to persist agent audit event")
            if self.fail_closed:
                raise


class AuditedAgent(Agent):
    """Add the user-instruction audit event missing from Vanna 2.0.2."""

    def __init__(
        self,
        *args: Any,
        request_harness: RequestHarness | None = None,
        evidence_drawer_mode: str = "off",
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self.request_harness = request_harness or RequestHarness()
        self.evidence_drawer_mode = evidence_drawer_mode

    async def _send_message(
        self,
        request_context: RequestContext,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[UiComponent, None]:
        user = await self.user_resolver.resolve_user(request_context)
        parent_send_message = super()._send_message

        async def operation(_run):
            instruction_hash = get_instruction_hash()
            run_id = get_run_id()
            assert instruction_hash is not None and run_id is not None
            await self.audit_logger.log_event(
                AuditEvent(
                    event_type=AuditEventType.MESSAGE_RECEIVED,
                    user_id=user.id,
                    username=user.username,
                    user_groups=user.group_memberships,
                    conversation_id=conversation_id or "",
                    request_id=run_id,
                    remote_addr=request_context.remote_addr,
                    details={
                        "instruction_hash": instruction_hash,
                        "message_length": len(message),
                    },
                )
            )
            async for component in parent_send_message(
                request_context, message, conversation_id=conversation_id
            ):
                yield component

        async for component in self.request_harness.execute_stream(
            instruction=message,
            user_id=user.id,
            conversation_id=conversation_id,
            operation=operation,
            operation_kind=classify_operation_kind(message),
        ):
            yield component
        run_id = current_run_id()
        if self.evidence_drawer_mode != "off" and run_id:
            yield build_evidence_artifact(run_id)

    async def resume_message(
        self,
        request_context: RequestContext,
        instruction: str,
        child: RunRecord,
        selection: dict[str, str],
    ) -> AsyncGenerator[UiComponent, None]:
        user = await self.user_resolver.resolve_user(request_context)
        if str(user.id) != child.user_id:
            raise PermissionError("resume belongs to another user")
        parent_send_message = super()._send_message
        evidence_token = set_resume_evidence(selection)

        async def operation(_run):
            async for component in parent_send_message(
                request_context,
                instruction,
                conversation_id=child.conversation_id,
            ):
                yield component

        try:
            async for component in self.request_harness.execute_resumed_stream(
                child=child,
                instruction=instruction,
                operation=operation,
            ):
                yield component
            if self.evidence_drawer_mode != "off":
                yield build_evidence_artifact(child.run_id)
        finally:
            reset_resume_evidence(evidence_token)

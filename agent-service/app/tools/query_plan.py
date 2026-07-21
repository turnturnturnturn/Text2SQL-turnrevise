from __future__ import annotations

import json
from typing import Type

from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from app.grounding.context import (
    get_grounding_state,
    record_validated_plan,
)
from app.grounding.plan_store import QueryPlanStore
from app.grounding.query_plan import (
    QueryPlanDraft,
    QueryPlanStatus,
    QueryPlanValidator,
)
from app.harness.context import get_run_id


class ValidateQueryPlanTool(Tool[QueryPlanDraft]):
    def __init__(
        self,
        validator: QueryPlanValidator,
        *,
        store: QueryPlanStore | None = None,
        grounding_mode: str = "off",
        trace_service=None,
    ) -> None:
        if grounding_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("GROUNDING_V2_MODE must be off, shadow or enforce")
        self.validator = validator
        self.store = store
        self.grounding_mode = grounding_mode
        self.trace_service = trace_service

    @property
    def name(self) -> str:
        return "validate_query_plan"

    @property
    def description(self) -> str:
        return "Validate a structured database QueryPlan against the latest approved grounding evidence"

    def get_args_schema(self) -> Type[QueryPlanDraft]:
        return QueryPlanDraft

    async def execute(self, context: ToolContext, args: QueryPlanDraft) -> ToolResult:
        del context
        state = get_grounding_state()
        if state is None or state.bundle is None:
            blocking = self.grounding_mode == "enforce"
            return ToolResult(
                success=not blocking,
                result_for_llm=(
                    "QueryPlan validation requires search_schema_knowledge first."
                    if blocking
                    else "Shadow QueryPlan observation skipped because grounding evidence is missing; continue the legacy SQL path."
                ),
                error="grounding evidence is missing" if blocking else None,
                metadata={
                    "error_type": "semantic_validation" if blocking else None,
                    "shadow_warning": None if blocking else "GROUNDING_EVIDENCE_MISSING",
                },
            )

        validated = self.validator.validate(
            args, state.bundle.snapshot, state.bundle.catalog
        )
        # A new validation attempt invalidates any earlier authorization in the
        # same request until the new plan has passed the persistence gate.
        record_validated_plan(None)
        run_id = get_run_id()
        persistence_warning = None
        if self.grounding_mode == "enforce" and (
            self.store is None or run_id is None
        ):
            return ToolResult(
                success=False,
                result_for_llm="QueryPlan evidence persistence is unavailable; execution is blocked.",
                error="query plan persistence is unavailable",
                metadata={"error_type": "semantic_validation"},
            )
        if self.store is not None and run_id is not None:
            try:
                await self.store.save(run_id, validated, state.bundle.snapshot)
            except Exception as exc:
                if self.grounding_mode == "enforce":
                    return ToolResult(
                        success=False,
                        result_for_llm="QueryPlan evidence could not be persisted; execution is blocked.",
                        error=type(exc).__name__,
                        metadata={"error_type": "semantic_validation"},
                    )
                persistence_warning = type(exc).__name__
        record_validated_plan(validated)
        if run_id is not None and self.trace_service is not None:
            await self.trace_service.append(run_id, "plan_validated", {
                "grounding_mode": self.grounding_mode,
                "status": validated.status.value,
                "db_copilot.grounding_coverage": validated.confidence,
            })

        payload = {
            "status": validated.status.value,
            "plan_hash": validated.plan_hash,
            "confidence": validated.confidence,
            "evidence_ids": validated.evidence_ids,
            "errors": validated.errors,
            "persistence_warning": persistence_warning,
        }
        rendered = "```json\n" + json.dumps(payload, ensure_ascii=False, indent=2) + "\n```"
        plan_valid = validated.status == QueryPlanStatus.VALID
        success = plan_valid or self.grounding_mode != "enforce"
        return ToolResult(
            success=success,
            result_for_llm=(
                "QueryPlan is valid and may be used to generate SQL.\n" + rendered
                if plan_valid
                else (
                    "QueryPlan cannot be executed. Resolve the listed issues.\n" + rendered
                    if self.grounding_mode == "enforce"
                    else "Shadow QueryPlan recorded differences; continue the legacy SQL path.\n" + rendered
                )
            ),
            error=(
                "; ".join(validated.errors)
                if self.grounding_mode == "enforce" and not plan_valid
                else None
            ),
            ui_component=UiComponent(
                rich_component=CardComponent(
                    title="QueryPlan 验证",
                    content=rendered,
                    icon="🧭",
                    status="success" if plan_valid else "warning",
                    collapsible=True,
                    collapsed=True,
                    markdown=True,
                ),
                simple_component=SimpleTextComponent(text=json.dumps(payload, ensure_ascii=False)),
            ),
            metadata={
                **payload,
                "error_type": (
                    "semantic_validation"
                    if self.grounding_mode == "enforce" and not plan_valid
                    else None
                ),
            },
        )

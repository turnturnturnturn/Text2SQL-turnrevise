from __future__ import annotations

from typing import Type

from vanna.capabilities.sql_runner import RunSqlToolArgs
from vanna.components import DataFrameComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from app.db import SafePostgresRunner
from app.audit import get_current_instruction
from app.security import (
    QueryIntentError,
    QueryResultIntentError,
    SqlPolicyError,
    validate_query_intent,
    validate_result_intent,
    validate_read_query,
    QueryPlanAlignmentError,
    query_plan_alignment_errors,
    validate_sql_against_query_plan,
)
from app.grounding.context import get_grounding_state
from app.harness.context import get_run_id
from app.harness.models import RunStatus, TERMINAL_STATUSES
from app.harness.store import RunStore


class SafeReadSqlTool(Tool[RunSqlToolArgs]):
    def __init__(
        self,
        runner: SafePostgresRunner,
        max_rows: int = 200,
        *,
        grounding_mode: str = "off",
        run_store: RunStore | None = None,
    ):
        if grounding_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("GROUNDING_V2_MODE must be off, shadow or enforce")
        self.runner = runner
        self.max_rows = max_rows
        self.grounding_mode = grounding_mode
        self.run_store = run_store

    @property
    def name(self) -> str:
        return "safe_read_sql"

    @property
    def description(self) -> str:
        return "Execute one read-only PostgreSQL SELECT query against approved commerce tables"

    def get_args_schema(self) -> Type[RunSqlToolArgs]:
        return RunSqlToolArgs

    async def execute(self, context: ToolContext, args: RunSqlToolArgs) -> ToolResult:
        try:
            run_id = get_run_id()
            if run_id is not None and self.run_store is not None:
                run = await self.run_store.get(run_id)
                if run is not None and run.status in TERMINAL_STATUSES:
                    reason = (
                        "clarification is required before SQL execution"
                        if run.status == RunStatus.NEEDS_CLARIFICATION
                        else f"run is already terminal: {run.status}"
                    )
                    return ToolResult(
                        success=False,
                        result_for_llm=reason,
                        error=reason,
                        metadata={"error_type": "harness_state"},
                    )
            guarded = validate_read_query(args.sql, max_rows=self.max_rows)
            validate_query_intent(guarded.sql, get_current_instruction())
            state = get_grounding_state()
            validated_plan = state.validated_plan if state else None
            bundle = state.bundle if state else None
            shadow_errors: list[str] = []
            if self.grounding_mode == "enforce":
                validate_sql_against_query_plan(
                    guarded.sql,
                    validated_plan,
                    bundle.snapshot if bundle else None,
                    bundle.catalog if bundle else None,
                )
            elif self.grounding_mode == "shadow" and bundle is not None:
                shadow_errors = query_plan_alignment_errors(
                    guarded.sql,
                    validated_plan,
                    bundle.snapshot,
                    bundle.catalog,
                )
            frame = await self.runner.run(guarded.sql)
            records = frame.to_dict("records")
            validate_result_intent(records, get_current_instruction())
            columns = frame.columns.tolist()
            text = frame.to_csv(index=False)
            return ToolResult(
                success=True,
                result_for_llm=text[:4000] or "Query returned no rows.",
                ui_component=UiComponent(
                    rich_component=DataFrameComponent.from_records(
                        records=records,
                        title="安全查询结果",
                        description=f"返回 {len(records)} 行；已通过只读策略校验",
                    ),
                    simple_component=SimpleTextComponent(
                        text=text or "No rows returned"
                    ),
                ),
                metadata={
                    "sql": guarded.sql,
                    "tables": sorted(guarded.tables),
                    "row_count": len(records),
                    "columns": columns,
                    "grounding_v2_mode": self.grounding_mode,
                    "query_plan_hash": (
                        validated_plan.plan_hash if validated_plan else None
                    ),
                    "query_plan_shadow_errors": shadow_errors,
                },
            )
        except (
            QueryIntentError,
            QueryResultIntentError,
            QueryPlanAlignmentError,
        ) as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"Execution-guided semantic validation rejected the result: {exc}",
                error=str(exc),
                metadata={"error_type": "semantic_validation"},
            )
        except SqlPolicyError as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"SQL policy rejected the query: {exc}",
                error=str(exc),
                metadata={"error_type": "sql_policy"},
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"Read query failed: {exc}",
                error=str(exc),
                metadata={"error_type": "database"},
            )

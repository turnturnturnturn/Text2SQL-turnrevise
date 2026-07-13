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
)


class SafeReadSqlTool(Tool[RunSqlToolArgs]):
    def __init__(self, runner: SafePostgresRunner, max_rows: int = 200):
        self.runner = runner
        self.max_rows = max_rows

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
            guarded = validate_read_query(args.sql, max_rows=self.max_rows)
            validate_query_intent(guarded.sql, get_current_instruction())
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
                    simple_component=SimpleTextComponent(text=text or "No rows returned"),
                ),
                metadata={
                    "sql": guarded.sql,
                    "tables": sorted(guarded.tables),
                    "row_count": len(records),
                    "columns": columns,
                },
            )
        except (QueryIntentError, QueryResultIntentError) as exc:
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

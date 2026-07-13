from __future__ import annotations

from typing import Any, Callable, Type

from pydantic import BaseModel, Field
from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from app.retrieval import HybridKnowledgeRetriever, PostgresKnowledgeStore


JOIN_HINTS = (
    (("客户", "用户"), "客户维度：orders.customer_id = customers.id；客户名称使用 customers.name。"),
    (("商品", "产品", "品类", "销量"), "商品维度：order_items.product_id = products.id，并通过 order_items.order_id = orders.id 过滤 orders.deleted_at。"),
    (("退款", "退货"), "退款维度：refunds.order_id = orders.id；按区域分析时使用 orders.region。"),
)


def join_hints_for_query(query: str) -> list[str]:
    return [hint for terms, hint in JOIN_HINTS if any(term in query for term in terms)]


class SearchSchemaKnowledgeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200, description="Business term or schema concept")
    limit: int = Field(default=8, ge=1, le=20)


class SearchSchemaKnowledgeTool(Tool[SearchSchemaKnowledgeArgs]):
    def __init__(
        self,
        connection_string: str,
        *,
        retrieval_mode: str = "hybrid",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        candidate_limit: int = 12,
        embedder_loader: Callable[[str], Any] | None = None,
    ):
        retriever_kwargs = {}
        if embedder_loader is not None:
            retriever_kwargs["embedder_loader"] = embedder_loader
        self.retriever = HybridKnowledgeRetriever(
            PostgresKnowledgeStore(connection_string),
            mode=retrieval_mode,
            embedding_model=embedding_model,
            candidate_limit=candidate_limit,
            **retriever_kwargs,
        )

    @property
    def name(self) -> str:
        return "search_schema_knowledge"

    @property
    def description(self) -> str:
        return "Search approved schema descriptions, metric definitions and correct SQL examples"

    def get_args_schema(self) -> Type[SearchSchemaKnowledgeArgs]:
        return SearchSchemaKnowledgeArgs

    async def execute(
        self, context: ToolContext, args: SearchSchemaKnowledgeArgs
    ) -> ToolResult:
        try:
            outcome = await self.retriever.search(args.query, args.limit)
            content = "\n\n".join(document.render() for document in outcome.documents)
            if not content:
                content = "No matching schema knowledge found."
            if outcome.warning:
                content = f"{content}\n\n> {outcome.warning}"
            join_hints = join_hints_for_query(args.query)
            if join_hints:
                content = f"{content}\n\n**Schema Graph / 推荐 Join Path**\n" + "\n".join(
                    f"- {hint}" for hint in join_hints
                )
            return ToolResult(
                success=True,
                result_for_llm=content,
                ui_component=UiComponent(
                    rich_component=CardComponent(
                        title="Schema 与指标知识",
                        content=content,
                        icon="📚",
                        status="info",
                        collapsible=True,
                        collapsed=True,
                        markdown=True,
                    ),
                    simple_component=SimpleTextComponent(text=content),
                ),
                metadata={
                    "result_count": len(outcome.documents),
                    "retrieval_mode": outcome.mode,
                    "keyword_hits": outcome.keyword_hits,
                    "vector_hits": outcome.vector_hits,
                    "source_ids": [document.document_id for document in outcome.documents],
                    "join_hints": join_hints,
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"Knowledge search failed: {exc}",
                error=str(exc),
            )

from __future__ import annotations

import asyncio
from typing import Any, Callable, Type

from pydantic import BaseModel, Field
from vanna.components import CardComponent, SimpleTextComponent, UiComponent
from vanna.core.tool import Tool, ToolContext, ToolResult

from app.retrieval import HybridKnowledgeRetriever, PostgresKnowledgeStore
from app.grounding.context import record_grounding
from app.grounding.linker import GroundingBundle, GroundingService
from app.harness.clarification import (
    ClarificationOption,
    ClarificationService,
    MaterialAmbiguity,
)
from app.harness.context import get_run_id


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
        grounding_service: GroundingService | None = None,
        grounding_mode: str = "off",
        context_harness_mode: str = "shadow",
        clarification_service: ClarificationService | None = None,
    ):
        if grounding_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("GROUNDING_V2_MODE must be off, shadow or enforce")
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
        self.grounding_service = grounding_service
        self.grounding_mode = grounding_mode
        if context_harness_mode not in {"off", "shadow", "enforce"}:
            raise ValueError("CONTEXT_HARNESS_V2_MODE must be off, shadow or enforce")
        self.context_harness_mode = context_harness_mode
        self.clarification_service = clarification_service

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
            bundle = None
            grounding_warning = None
            clarification_card = None
            if self.grounding_mode == "enforce":
                if self.grounding_service is None:
                    raise RuntimeError("Grounding v2 service is not configured")
                bundle = await asyncio.to_thread(self.grounding_service.search, args.query)
                record_grounding(bundle)
                content = self._render_grounding(bundle)
                source_ids = list(bundle.snapshot.evidence_ids)
                outcome = None
                join_hints = []
                if (
                    bundle.snapshot.ambiguities
                    and self.context_harness_mode == "enforce"
                    and self.clarification_service is not None
                    and get_run_id() is not None
                ):
                    options = self._clarification_options(bundle)
                    if len(options) >= 2:
                        clarification_card = await self.clarification_service.create(
                            parent_run_id=get_run_id(),
                            user_id=str(context.user.id),
                            ambiguity=MaterialAmbiguity(
                                ambiguity_id=f"grounding:{bundle.snapshot.query_hash[:16]}",
                                question="请选择本次查询应使用的字段或受控值：",
                                options=tuple(options[:3]),
                                reason="; ".join(bundle.snapshot.ambiguities),
                            ),
                        )
                        content += "\n\n**需要澄清**\n" + "\n".join(
                            f"- `{entry.option_id}`: {entry.label}"
                            for entry in clarification_card.options
                        )
            else:
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
                source_ids = [document.document_id for document in outcome.documents]
                if self.grounding_mode == "shadow" and self.grounding_service is not None:
                    try:
                        bundle = await asyncio.to_thread(self.grounding_service.search, args.query)
                        record_grounding(bundle)
                    except Exception as exc:
                        grounding_warning = type(exc).__name__

            grounding_metadata = bundle.snapshot.to_safe_dict() if bundle else None
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
                    "result_count": len(source_ids),
                    "retrieval_mode": (
                        "grounding_v2" if outcome is None else outcome.mode
                    ),
                    "keyword_hits": 0 if outcome is None else outcome.keyword_hits,
                    "vector_hits": 0 if outcome is None else outcome.vector_hits,
                    "source_ids": source_ids,
                    "join_hints": join_hints,
                    "grounding_v2_mode": self.grounding_mode,
                    "grounding_v2": grounding_metadata,
                    "grounding_v2_warning": grounding_warning,
                    "clarification_required": clarification_card is not None,
                    "clarification_card": (
                        None
                        if clarification_card is None
                        else {
                            "parent_run_id": clarification_card.parent_run_id,
                            "question": clarification_card.question,
                            "expires_at": clarification_card.expires_at.isoformat(),
                            "options": [
                                {
                                    "option_id": entry.option_id,
                                    "label": entry.label,
                                    "evidence_id": entry.evidence_id,
                                    "source_hash": entry.source_hash,
                                }
                                for entry in clarification_card.options
                            ],
                        }
                    ),
                },
            )
        except Exception as exc:
            return ToolResult(
                success=False,
                result_for_llm=f"Knowledge search failed: {exc}",
                error=str(exc),
            )

    @staticmethod
    def _render_grounding(bundle: GroundingBundle) -> str:
        snapshot = bundle.snapshot
        assets = {item.asset_id: item for item in bundle.catalog.assets}
        relations = {item.relation_id: item for item in bundle.catalog.relations}
        sections = ["**Grounding v2 / 已批准语义证据**"]

        tables = [item for item in snapshot.table_candidates if item.score > 0]
        if tables:
            sections.append(
                "**候选表**\n"
                + "\n".join(
                    f"- `{item.asset_id}` score={item.score:.3f}：{assets[item.asset_id].description}"
                    for item in tables
                )
            )
        columns = [item for item in snapshot.column_candidates if item.score > 0]
        if columns:
            sections.append(
                "**候选字段**\n"
                + "\n".join(
                    f"- `{item.asset_id}` score={item.score:.3f}：{assets[item.asset_id].description}"
                    for item in columns
                )
            )
        if snapshot.knowledge_candidates:
            sections.append(
                "**指标与示例**\n"
                + "\n".join(
                    f"- `{item.asset_id}`：{assets[item.asset_id].description}"
                    for item in snapshot.knowledge_candidates
                )
            )
        if snapshot.value_candidates:
            sections.append(
                "**受控值**\n"
                + "\n".join(
                    f"- `{item.value_id}` → `{item.canonical_value}` ({item.column_asset_id}, score={item.score:.3f})"
                    for item in snapshot.value_candidates
                )
            )
        if snapshot.join_paths:
            path_lines = []
            for path in snapshot.join_paths:
                expressions = [
                    relations[item].join_expression
                    for item in path.relation_ids
                    if item in relations and relations[item].join_expression
                ]
                path_lines.append(
                    f"- `{path.path_id}`：" + "；".join(expressions)
                )
            sections.append("**Confirmed Join Paths**\n" + "\n".join(path_lines))
        if snapshot.ambiguities:
            sections.append(
                "**必须解决的歧义**\n"
                + "\n".join(f"- `{item}`" for item in snapshot.ambiguities)
            )
        sections.append(f"Grounding confidence: {snapshot.confidence:.4f}")
        return "\n\n".join(sections)

    @staticmethod
    def _clarification_options(bundle: GroundingBundle) -> list[ClarificationOption]:
        snapshot = bundle.snapshot
        options = [
            ClarificationOption(
                option_id=item.value_id,
                label=f"{item.canonical_value} ({item.column_asset_id})",
                value_id=item.value_id,
                evidence_id=item.value_id,
                source_hash=item.source_hash,
            )
            for item in snapshot.value_candidates
        ]
        if len(options) >= 2:
            return options
        return [
            ClarificationOption(
                option_id=item.asset_id,
                label=item.asset_id,
                value_id=item.asset_id,
                evidence_id=item.asset_id,
                source_hash=item.source_hash,
            )
            for item in snapshot.column_candidates
            if item.score > 0
        ]

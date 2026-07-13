import sys

import pytest

from app.retrieval import (
    HybridKnowledgeRetriever,
    KnowledgeDocument,
    _default_embedder_loader,
)
from app.tools.knowledge import join_hints_for_query


GMV = KnowledgeDocument(
    "metric:gmv",
    "metric",
    "GMV",
    "已支付或已发货且未软删除订单的金额总和 aliases=销售额,成交额",
)
REFUND = KnowledgeDocument(
    "metric:退款率",
    "metric",
    "退款率",
    "成功退款订单数除以支付或发货订单数 aliases=退货比例",
)
ORDERS = KnowledgeDocument(
    "schema:orders.total_amount",
    "schema",
    "orders.total_amount",
    "订单总金额 aliases=销售额,GMV",
)


class FakeStore:
    def __init__(self, keyword_results):
        self.documents = [GMV, REFUND, ORDERS]
        self.keyword_results = keyword_results

    def load_documents(self):
        return self.documents

    def search_keyword(self, query, limit):
        return self.keyword_results.get(query, [])[:limit]


class FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True):
        del normalize_embeddings
        vectors = {
            GMV.text: (1.0, 0.0),
            REFUND.text: (0.0, 1.0),
            ORDERS.text: (0.8, 0.2),
            "成交额": (1.0, 0.0),
            "退货比例": (0.0, 1.0),
        }
        return [vectors[text] for text in texts]


@pytest.mark.asyncio
async def test_hybrid_retrieval_recovers_chinese_synonym_when_ilike_has_no_hit():
    retriever = HybridKnowledgeRetriever(
        FakeStore({"成交额": []}),
        embedder_loader=lambda _: FakeEmbedder(),
    )

    outcome = await retriever.search("成交额", limit=2)

    assert outcome.mode == "hybrid"
    assert outcome.keyword_hits == 0
    assert outcome.vector_hits == 3
    assert outcome.documents[0].document_id == "metric:gmv"


@pytest.mark.asyncio
async def test_hybrid_fuses_keyword_and_vector_sources():
    retriever = HybridKnowledgeRetriever(
        FakeStore({"成交额": [ORDERS]}),
        embedder_loader=lambda _: FakeEmbedder(),
    )

    outcome = await retriever.search("成交额", limit=3)

    assert outcome.mode == "hybrid"
    assert {document.document_id for document in outcome.documents} >= {
        "metric:gmv",
        "schema:orders.total_amount",
    }


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_ilike_when_embedding_model_is_unavailable():
    retriever = HybridKnowledgeRetriever(
        FakeStore({"销售额": [ORDERS]}),
        embedder_loader=lambda _: (_ for _ in ()).throw(RuntimeError("model missing")),
    )

    outcome = await retriever.search("销售额", limit=3)

    assert outcome.mode == "keyword_fallback"
    assert outcome.warning
    assert outcome.documents == (ORDERS,)


def test_rrf_rewards_documents_seen_by_multiple_retrievers():
    retriever = HybridKnowledgeRetriever(FakeStore({}), embedder_loader=lambda _: FakeEmbedder())

    ranked = retriever._reciprocal_rank_fusion([ORDERS, GMV], [ORDERS, REFUND], limit=3)

    assert ranked[0] == ORDERS


def test_default_embedder_loader_prefers_local_cache(monkeypatch):
    calls = []

    class LocalModel:
        pass

    class FakeSentenceTransformer:
        def __new__(cls, model_name, *, local_files_only=False):
            calls.append((model_name, local_files_only))
            return LocalModel()

    module = type("SentenceTransformerModule", (), {"SentenceTransformer": FakeSentenceTransformer})
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)

    assert isinstance(_default_embedder_loader("BAAI/bge-small-zh-v1.5"), LocalModel)
    assert calls == [("BAAI/bge-small-zh-v1.5", True)]


def test_join_graph_expands_customer_and_refund_paths():
    hints = join_hints_for_query("每个客户各区域退款金额")
    assert any("customers.id" in hint for hint in hints)
    assert any("refunds.order_id" in hint for hint in hints)

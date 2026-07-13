from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import psycopg
from psycopg.rows import dict_row


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    kind: str
    title: str
    body: str

    @property
    def text(self) -> str:
        return f"{self.kind}\n{self.title}\n{self.body}"

    def render(self) -> str:
        return f"**{self.kind} / {self.title}**\n{self.body}"


@dataclass(frozen=True)
class RetrievalOutcome:
    documents: tuple[KnowledgeDocument, ...]
    mode: str
    warning: str | None = None
    keyword_hits: int = 0
    vector_hits: int = 0


class PostgresKnowledgeStore:
    """Read-only access to the approved schema and metric knowledge corpus."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def load_documents(self) -> list[KnowledgeDocument]:
        sql = """
            SELECT document_id, kind, title, body
            FROM (
                SELECT 'schema:' || table_name || '.' || COALESCE(column_name, '_table') AS document_id,
                       'schema' AS kind,
                       table_name || COALESCE('.' || column_name, '') AS title,
                       description || ' aliases=' || array_to_string(aliases, ',') AS body
                FROM schema_catalog
                UNION ALL
                SELECT 'metric:' || lower(metric_name) AS document_id,
                       'metric' AS kind,
                       metric_name AS title,
                       description || ' formula=' || formula || ' example=' || example_sql AS body
                FROM metric_definitions
                UNION ALL
                SELECT 'example:' || id::text AS document_id,
                       'example' AS kind,
                       question AS title,
                       sql_text AS body
                FROM query_examples
            ) knowledge
            ORDER BY document_id
        """
        return self._fetch_documents(sql)

    def search_keyword(self, query: str, limit: int) -> list[KnowledgeDocument]:
        pattern = f"%{query}%"
        sql = """
            SELECT document_id, kind, title, body
            FROM (
                SELECT 'schema:' || table_name || '.' || COALESCE(column_name, '_table') AS document_id,
                       'schema' AS kind,
                       table_name || COALESCE('.' || column_name, '') AS title,
                       description || ' aliases=' || array_to_string(aliases, ',') AS body
                FROM schema_catalog
                WHERE description ILIKE %s OR table_name ILIKE %s
                   OR COALESCE(column_name, '') ILIKE %s OR array_to_string(aliases, ',') ILIKE %s
                UNION ALL
                SELECT 'metric:' || lower(metric_name) AS document_id,
                       'metric' AS kind,
                       metric_name AS title,
                       description || ' formula=' || formula || ' example=' || example_sql AS body
                FROM metric_definitions
                WHERE metric_name ILIKE %s OR description ILIKE %s
                UNION ALL
                SELECT 'example:' || id::text AS document_id,
                       'example' AS kind,
                       question AS title,
                       sql_text AS body
                FROM query_examples
                WHERE question ILIKE %s OR array_to_string(tags, ',') ILIKE %s
            ) knowledge
            ORDER BY kind, title
            LIMIT %s
        """
        return self._fetch_documents(sql, (pattern,) * 8 + (limit,))

    def _fetch_documents(
        self, sql: str, params: tuple[Any, ...] = ()
    ) -> list[KnowledgeDocument]:
        with psycopg.connect(
            self.connection_string,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=3000",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                return [KnowledgeDocument(**row) for row in cursor.fetchall()]


EmbedderLoader = Callable[[str], Any]


def _default_embedder_loader(model_name: str) -> Any:
    from sentence_transformers import SentenceTransformer

    # Prefer the persisted Docker volume without making Hugging Face metadata
    # requests on every container start. If this is a genuinely fresh volume,
    # retry online once so the first developer run can populate the cache.
    try:
        return SentenceTransformer(model_name, local_files_only=True)
    except OSError:
        return SentenceTransformer(model_name)


class HybridKnowledgeRetriever:
    """Fuses existing PostgreSQL ILIKE search with local embedding similarity."""

    def __init__(
        self,
        store: PostgresKnowledgeStore,
        *,
        mode: str = "hybrid",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        candidate_limit: int = 12,
        rrf_k: int = 60,
        embedder_loader: EmbedderLoader = _default_embedder_loader,
    ):
        if mode not in {"hybrid", "keyword"}:
            raise ValueError("RETRIEVAL_MODE must be 'hybrid' or 'keyword'")
        self.store = store
        self.mode = mode
        self.embedding_model = embedding_model
        self.candidate_limit = candidate_limit
        self.rrf_k = rrf_k
        self.embedder_loader = embedder_loader
        self._documents: tuple[KnowledgeDocument, ...] | None = None
        self._document_vectors: tuple[tuple[float, ...], ...] | None = None
        self._embedder: Any | None = None
        self._index_lock = asyncio.Lock()

    async def search(self, query: str, limit: int) -> RetrievalOutcome:
        keyword_documents = await asyncio.to_thread(
            self.store.search_keyword, query, self.candidate_limit
        )
        if self.mode == "keyword":
            return RetrievalOutcome(tuple(keyword_documents[:limit]), "keyword", keyword_hits=len(keyword_documents))

        try:
            documents, vectors = await self._ensure_index()
            vector_documents = await asyncio.to_thread(
                self._vector_search, query, documents, vectors, self.candidate_limit
            )
            fused = self._reciprocal_rank_fusion(keyword_documents, vector_documents, limit)
            return RetrievalOutcome(
                tuple(fused),
                "hybrid",
                keyword_hits=len(keyword_documents),
                vector_hits=len(vector_documents),
            )
        except Exception as exc:
            logger.warning("Hybrid retrieval unavailable; using keyword fallback: %s", exc)
            return RetrievalOutcome(
                tuple(keyword_documents[:limit]),
                "keyword_fallback",
                warning="向量检索暂不可用，已回退到关键词检索。",
                keyword_hits=len(keyword_documents),
            )

    async def _ensure_index(
        self,
    ) -> tuple[tuple[KnowledgeDocument, ...], tuple[tuple[float, ...], ...]]:
        if self._documents is not None and self._document_vectors is not None:
            return self._documents, self._document_vectors
        async with self._index_lock:
            if self._documents is None or self._document_vectors is None:
                documents, vectors, embedder = await asyncio.to_thread(self._build_index)
                self._documents = tuple(documents)
                self._document_vectors = tuple(vectors)
                self._embedder = embedder
        return self._documents, self._document_vectors

    def _build_index(
        self,
    ) -> tuple[list[KnowledgeDocument], list[tuple[float, ...]], Any]:
        documents = self.store.load_documents()
        if not documents:
            raise RuntimeError("Knowledge corpus is empty")
        embedder = self.embedder_loader(self.embedding_model)
        vectors = self._encode(embedder, [document.text for document in documents])
        return documents, vectors, embedder

    def _vector_search(
        self,
        query: str,
        documents: Iterable[KnowledgeDocument],
        vectors: Iterable[tuple[float, ...]],
        limit: int,
    ) -> list[KnowledgeDocument]:
        if self._embedder is None:
            raise RuntimeError("Embedding model is not initialized")
        query_vector = self._encode(self._embedder, [query])[0]
        scored = [
            (self._dot(query_vector, vector), document)
            for document, vector in zip(documents, vectors, strict=True)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].document_id))
        return [document for _, document in scored[:limit]]

    def _reciprocal_rank_fusion(
        self,
        keyword_documents: Iterable[KnowledgeDocument],
        vector_documents: Iterable[KnowledgeDocument],
        limit: int,
    ) -> list[KnowledgeDocument]:
        scores: dict[str, float] = {}
        documents: dict[str, KnowledgeDocument] = {}
        for ranking in (keyword_documents, vector_documents):
            for rank, document in enumerate(ranking, start=1):
                documents[document.document_id] = document
                scores[document.document_id] = scores.get(document.document_id, 0.0) + 1 / (
                    self.rrf_k + rank
                )
        ranked_ids = sorted(scores, key=lambda doc_id: (-scores[doc_id], doc_id))
        return [documents[doc_id] for doc_id in ranked_ids[:limit]]

    @staticmethod
    def _encode(embedder: Any, texts: list[str]) -> list[tuple[float, ...]]:
        raw_vectors = embedder.encode(texts, normalize_embeddings=True)
        return [tuple(float(value) for value in vector) for vector in raw_vectors]

    @staticmethod
    def _dot(left: tuple[float, ...], right: tuple[float, ...]) -> float:
        return sum(a * b for a, b in zip(left, right, strict=True))

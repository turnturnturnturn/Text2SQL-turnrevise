from __future__ import annotations

import hashlib
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlglot import exp, parse_one

from app.grounding.catalog import CatalogSnapshot, SemanticCatalogStore
from app.grounding.models import (
    AssetType,
    AssetStatus,
    GroundingCandidate,
    GroundingSnapshot,
    JoinPath,
    RelationStatus,
    SchemaRelation,
    SemanticAsset,
    Sensitivity,
    ValueCandidate,
    ValueDictionaryEntry,
)


_NON_WORD = re.compile(r"[^0-9a-zA-Z\u3400-\u9fff]+")


def normalize_term(value: str) -> str:
    return _NON_WORD.sub("", value.casefold())


def _bigrams(value: str) -> set[str]:
    return {value[index : index + 2] for index in range(max(0, len(value) - 1))}


def _lexical_score(query: str, texts: Iterable[str]) -> float:
    normalized_query = normalize_term(query)
    if not normalized_query:
        return 0.0
    query_bigrams = _bigrams(normalized_query)
    best = 0.0
    for text in texts:
        candidate = normalize_term(text)
        if not candidate:
            continue
        if candidate == normalized_query:
            score = 1.0
        elif candidate in normalized_query:
            score = min(0.98, 0.72 + len(candidate) / max(len(normalized_query), 1) * 0.24)
        elif len(normalized_query) >= 2 and normalized_query in candidate:
            score = min(0.9, 0.52 + len(normalized_query) / len(candidate) * 0.3)
        else:
            candidate_bigrams = _bigrams(candidate)
            union = query_bigrams | candidate_bigrams
            overlap = 0.0 if not union else len(query_bigrams & candidate_bigrams) / len(union)
            score = overlap * 0.68
        best = max(best, score)
    return round(best, 6)


def _asset_texts(asset: SemanticAsset) -> tuple[str, ...]:
    return (asset.title, asset.description, asset.asset_id, *asset.aliases)


def _value_texts(value: ValueDictionaryEntry) -> tuple[str, ...]:
    return (value.canonical_value, value.value_id, *value.aliases)


def _rrf_scores(
    lexical: dict[str, float], vector: dict[str, float], *, rank_constant: int = 60
) -> dict[str, float]:
    """Merge independent retrieval rankings with normalized reciprocal rank fusion."""
    if not vector:
        return lexical

    def ranks(scores: dict[str, float]) -> dict[str, int]:
        ordered = sorted(
            ((key, score) for key, score in scores.items() if score > 0),
            key=lambda item: (-item[1], item[0]),
        )
        return {key: index for index, (key, _) in enumerate(ordered, start=1)}

    lexical_ranks = ranks(lexical)
    vector_ranks = ranks(vector)
    maximum = 2 / (rank_constant + 1)
    merged = {}
    for key in lexical_ranks.keys() | vector_ranks.keys():
        score = 0.0
        if key in lexical_ranks:
            score += 1 / (rank_constant + lexical_ranks[key])
        if key in vector_ranks:
            score += 1 / (rank_constant + vector_ranks[key])
        merged[key] = round(min(1.0, score / maximum), 6)
    return merged


def _table_id(asset: SemanticAsset) -> str | None:
    if asset.asset_type == AssetType.TABLE:
        return asset.asset_id
    table_name = asset.payload.get("table_name")
    if isinstance(table_name, str) and table_name:
        schema = asset.payload.get("database_schema", "public")
        return f"table:{schema}.{table_name}"
    if asset.asset_id.startswith("column:"):
        pieces = asset.asset_id.split(".")
        if len(pieces) >= 2:
            return ".".join(pieces[:-1]).replace("column:", "table:", 1)
    return None


def _column_table_id(asset_id: str) -> str | None:
    if not asset_id.startswith("column:") or "." not in asset_id:
        return None
    return ".".join(asset_id.split(".")[:-1]).replace("column:", "table:", 1)


@dataclass(frozen=True, slots=True)
class GroundingBundle:
    snapshot: GroundingSnapshot
    catalog: CatalogSnapshot


class BidirectionalGroundingLinker:
    """Deterministic table/column/value linking over curated catalog assets."""

    def __init__(
        self,
        *,
        embedder: Any | None = None,
        table_limit: int = 5,
        column_limit: int = 10,
        value_limit: int = 3,
        max_join_hops: int = 5,
        min_vector_similarity: float = 0.45,
        min_candidate_score: float = 0.05,
    ) -> None:
        if not 0 <= min_vector_similarity <= 1:
            raise ValueError("min_vector_similarity must be between 0 and 1")
        if not 0 <= min_candidate_score <= 1:
            raise ValueError("min_candidate_score must be between 0 and 1")
        self.embedder = embedder
        self.table_limit = table_limit
        self.column_limit = column_limit
        self.value_limit = value_limit
        self.max_join_hops = max_join_hops
        self.min_vector_similarity = min_vector_similarity
        self.min_candidate_score = min_candidate_score

    def link(self, query: str, catalog: CatalogSnapshot, *, tenant_id: str = "default") -> GroundingSnapshot:
        effective_at = datetime.now(timezone.utc)

        def current(start, end) -> bool:
            return (start is None or start <= effective_at) and (
                end is None or end > effective_at
            )

        eligible_assets = tuple(
            asset
            for asset in catalog.assets
            if asset.status == AssetStatus.PUBLISHED
            and asset.sensitivity != Sensitivity.RESTRICTED
            and current(asset.effective_from, asset.effective_to)
        )
        assets = {asset.asset_id: asset for asset in eligible_assets}
        tables = [asset for asset in eligible_assets if asset.asset_type == AssetType.TABLE]
        columns = [asset for asset in eligible_assets if asset.asset_type == AssetType.COLUMN]
        knowledge = [
            asset
            for asset in eligible_assets
            if asset.asset_type in {AssetType.METRIC, AssetType.QUERY_EXAMPLE, AssetType.BUSINESS_RULE}
        ]

        lexical = {
            asset.asset_id: _lexical_score(query, _asset_texts(asset))
            for asset in eligible_assets
        }
        try:
            vector = self._vector_scores(query, eligible_assets)
        except Exception:
            vector = {}

        fused = _rrf_scores(lexical, vector)

        def combined(asset_id: str) -> float:
            return fused.get(asset_id, 0.0)

        column_scores = {asset.asset_id: combined(asset.asset_id) for asset in columns}
        knowledge_table_scores: dict[str, float] = {}
        knowledge_column_scores: dict[str, float] = {}
        for item in knowledge:
            score = combined(item.asset_id)
            if score < 0.35:
                continue
            example_sql = item.payload.get("example_sql")
            if not isinstance(example_sql, str):
                continue
            try:
                expression = parse_one(example_sql, read="postgres")
            except Exception:
                continue
            sql_tables = list(expression.find_all(exp.Table))
            aliases = {
                (table.alias_or_name or table.name).lower(): table.name.lower()
                for table in sql_tables
            }
            table_names = {table.name.lower() for table in sql_tables}
            for table in sql_tables:
                table_id = f"table:public.{table.name.lower()}"
                knowledge_table_scores[table_id] = max(
                    knowledge_table_scores.get(table_id, 0.0), score * 0.9
                )
            for column in expression.find_all(exp.Column):
                table_name = (
                    aliases.get(column.table.lower(), column.table.lower())
                    if column.table
                    else next(iter(table_names), None) if len(table_names) == 1 else None
                )
                if table_name is None:
                    continue
                column_id = f"column:public.{table_name}.{column.name.lower()}"
                if column_id in assets:
                    knowledge_column_scores[column_id] = max(
                        knowledge_column_scores.get(column_id, 0.0), score * 0.9
                    )
        for column_id, score in knowledge_column_scores.items():
            column_scores[column_id] = max(column_scores.get(column_id, 0.0), score)
        table_scores: dict[str, float] = {}
        table_reasons: dict[str, tuple[str, ...]] = {}
        for table in tables:
            child_scores = [
                score
                for asset_id, score in column_scores.items()
                if _column_table_id(asset_id) == table.asset_id
            ]
            direct = combined(table.asset_id)
            reverse = max(child_scores, default=0.0) * 0.92
            knowledge_score = knowledge_table_scores.get(table.asset_id, 0.0)
            table_scores[table.asset_id] = round(max(direct, reverse, knowledge_score), 6)
            reasons = []
            if direct > 0:
                reasons.append("table_to_column")
            if reverse > direct:
                reasons.append("column_to_table")
            if knowledge_score > max(direct, reverse):
                reasons.append("knowledge_to_table")
            table_reasons[table.asset_id] = tuple(reasons)

        ranked_tables = sorted(tables, key=lambda item: (-table_scores[item.asset_id], item.asset_id))
        selected_tables = [
            table
            for table in ranked_tables
            if table_scores[table.asset_id] >= self.min_candidate_score
        ][: self.table_limit]
        selected_table_ids = {item.asset_id for item in selected_tables}
        table_candidates = tuple(
            GroundingCandidate(
                asset_id=asset.asset_id,
                score=table_scores[asset.asset_id],
                source_hash=asset.source_hash,
                reasons=table_reasons[asset.asset_id],
            )
            for asset in selected_tables
        )

        def scoped_column_score(asset: SemanticAsset) -> float:
            owner = _table_id(asset)
            owner_score = table_scores.get(owner or "", 0.0)
            scope_bonus = 0.08 if owner in selected_table_ids else 0.0
            return round(min(1.0, column_scores[asset.asset_id] + owner_score * 0.12 + scope_bonus), 6)

        scoped_columns = [
            asset
            for asset in columns
            if _table_id(asset) in selected_table_ids
            and column_scores[asset.asset_id] >= self.min_candidate_score
        ]
        ranked_columns = sorted(scoped_columns, key=lambda item: (-scoped_column_score(item), item.asset_id))
        column_candidates = tuple(
            GroundingCandidate(
                asset_id=asset.asset_id,
                score=scoped_column_score(asset),
                source_hash=asset.source_hash,
                reasons=("within_candidate_table",),
            )
            for asset in ranked_columns[: self.column_limit]
        )

        ranked_knowledge = sorted(knowledge, key=lambda item: (-combined(item.asset_id), item.asset_id))
        knowledge_candidates = tuple(
            GroundingCandidate(
                asset_id=asset.asset_id,
                score=combined(asset.asset_id),
                source_hash=asset.source_hash,
                reasons=("approved_knowledge",),
            )
            for asset in ranked_knowledge[:5]
            if combined(asset.asset_id) >= self.min_candidate_score
        )

        eligible_values = tuple(
            value
            for value in catalog.values
            if value.column_asset_id in assets
            and value.status == AssetStatus.PUBLISHED
            and value.sensitivity != Sensitivity.RESTRICTED
            and current(value.effective_from, value.effective_to)
        )
        value_candidates = self._link_values(query, eligible_values)
        relevant_tables = [
            item.asset_id for item in table_candidates if item.score >= 0.35
        ][:4]
        eligible_relations = tuple(
            relation
            for relation in catalog.relations
            if relation.left_asset_id in assets and relation.right_asset_id in assets
            and relation.sensitivity != Sensitivity.RESTRICTED
        )
        join_paths, join_ambiguities = self._join_paths(
            relevant_tables, eligible_relations
        )

        ambiguities = list(join_ambiguities)
        if len(value_candidates) > 1 and value_candidates[0].score - value_candidates[1].score < 0.08:
            ambiguities.append("VALUE_CANDIDATES_TOO_CLOSE")

        evidence = {
            *(item.asset_id for item in table_candidates),
            *(item.asset_id for item in column_candidates),
            *(item.asset_id for item in knowledge_candidates),
            *(item.value_id for item in value_candidates),
            *(relation_id for path in join_paths for relation_id in path.relation_ids),
            *(
                table_id
                for path in join_paths
                for table_id in path.table_asset_ids
                if table_id in assets
            ),
        }
        positive_scores = [
            item.score
            for item in (*table_candidates[:2], *column_candidates[:3], *value_candidates[:1])
            if item.score > 0
        ]
        coverage = min(1.0, len(positive_scores) / 4)
        relevance = sum(positive_scores) / len(positive_scores) if positive_scores else 0.0
        join_factor = 1.0 if len(relevant_tables) < 2 or join_paths else 0.0

        def candidate_gap(items) -> float:
            scores = [item.score for item in items if item.score > 0]
            if len(scores) < 2:
                return 1.0 if scores else 0.0
            return min(1.0, max(0.0, (scores[0] - scores[1]) / 0.2))

        gap_factor = (
            candidate_gap(table_candidates)
            + candidate_gap(column_candidates)
            + candidate_gap(value_candidates)
        ) / 3
        confidence = round(
            max(
                0.0,
                min(
                    1.0,
                    0.35 * coverage
                    + 0.3 * relevance
                    + 0.15 * join_factor
                    + 0.2 * gap_factor,
                ),
            ),
            4,
        )

        return GroundingSnapshot(
            query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            tenant_id=tenant_id,
            table_candidates=table_candidates,
            column_candidates=column_candidates,
            knowledge_candidates=knowledge_candidates,
            value_candidates=value_candidates,
            join_paths=join_paths,
            evidence_ids=tuple(sorted(evidence)),
            confidence=confidence,
            ambiguities=tuple(sorted(set(ambiguities))),
        )

    def _vector_scores(
        self, query: str, assets: list[SemanticAsset] | tuple[SemanticAsset, ...]
    ) -> dict[str, float]:
        if self.embedder is None or not assets:
            return {}
        texts = ["\n".join(_asset_texts(asset)) for asset in assets]
        vectors = self.embedder.encode([query, *texts], normalize_embeddings=True)
        query_vector = tuple(float(value) for value in vectors[0])
        scores = {}
        for asset, raw_vector in zip(assets, vectors[1:], strict=True):
            vector = tuple(float(value) for value in raw_vector)
            dot = sum(left * right for left, right in zip(query_vector, vector, strict=True))
            if dot >= self.min_vector_similarity:
                scores[asset.asset_id] = round(min(1.0, dot), 6)
        return scores

    def _link_values(
        self, query: str, values: tuple[ValueDictionaryEntry, ...]
    ) -> tuple[ValueCandidate, ...]:
        lexical = {
            value.value_id: _lexical_score(query, _value_texts(value))
            for value in values
        }
        try:
            vector = self._value_vector_scores(query, values)
        except Exception:
            vector = {}
        fused = _rrf_scores(lexical, vector)
        scored = []
        for value in values:
            score = fused.get(value.value_id, 0.0)
            if score < self.min_candidate_score:
                continue
            scored.append(
                ValueCandidate(
                    value_id=value.value_id,
                    column_asset_id=value.column_asset_id,
                    canonical_value=value.canonical_value,
                    score=score,
                    source_hash=value.source_hash,
                )
            )
        scored.sort(key=lambda item: (-item.score, item.value_id))
        return tuple(scored[: self.value_limit])

    def _value_vector_scores(
        self, query: str, values: tuple[ValueDictionaryEntry, ...]
    ) -> dict[str, float]:
        if self.embedder is None or not values:
            return {}
        texts = ["\n".join(_value_texts(value)) for value in values]
        vectors = self.embedder.encode([query, *texts], normalize_embeddings=True)
        query_vector = tuple(float(item) for item in vectors[0])
        scores = {}
        for value, raw_vector in zip(values, vectors[1:], strict=True):
            vector = tuple(float(item) for item in raw_vector)
            dot = sum(
                left * right
                for left, right in zip(query_vector, vector, strict=True)
            )
            if dot >= self.min_vector_similarity:
                scores[value.value_id] = round(min(1.0, dot), 6)
        return scores

    def _join_paths(
        self,
        relevant_tables: list[str],
        relations: tuple[SchemaRelation, ...],
    ) -> tuple[tuple[JoinPath, ...], tuple[str, ...]]:
        confirmed = [item for item in relations if item.status == RelationStatus.CONFIRMED]
        candidates = [item for item in relations if item.status == RelationStatus.CANDIDATE]
        graph: dict[str, list[tuple[str, SchemaRelation]]] = {}
        for relation in confirmed:
            left = _column_table_id(relation.left_asset_id)
            right = _column_table_id(relation.right_asset_id)
            if left is None or right is None:
                continue
            graph.setdefault(left, []).append((right, relation))
            graph.setdefault(right, []).append((left, relation))

        paths: dict[tuple[str, ...], JoinPath] = {}
        for index, start in enumerate(relevant_tables):
            for target in relevant_tables[index + 1 :]:
                found = self._shortest_path(start, target, graph)
                if found is None:
                    continue
                table_ids, relation_ids = found
                path_key = tuple(relation_ids)
                path_hash = hashlib.sha256("|".join(relation_ids).encode("utf-8")).hexdigest()[:20]
                paths[path_key] = JoinPath(
                    path_id=f"joinpath:{path_hash}",
                    relation_ids=tuple(relation_ids),
                    table_asset_ids=tuple(table_ids),
                    score=round(1 / len(relation_ids), 6),
                )

        ambiguities = []
        relevant = set(relevant_tables)
        for relation in candidates:
            endpoints = {
                _column_table_id(relation.left_asset_id),
                _column_table_id(relation.right_asset_id),
            }
            if None not in endpoints and endpoints <= relevant:
                ambiguities.append(f"CANDIDATE_JOIN:{relation.relation_id}")
        return (
            tuple(sorted(paths.values(), key=lambda item: (-item.score, item.path_id))),
            tuple(sorted(ambiguities)),
        )

    def _shortest_path(
        self,
        start: str,
        target: str,
        graph: dict[str, list[tuple[str, SchemaRelation]]],
    ) -> tuple[list[str], list[str]] | None:
        queue = deque([(start, [start], [])])
        seen = {start}
        while queue:
            node, tables, relations = queue.popleft()
            if node == target:
                return tables, relations
            if len(relations) >= self.max_join_hops:
                continue
            for neighbor, relation in sorted(graph.get(node, []), key=lambda item: item[1].relation_id):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                queue.append((neighbor, [*tables, neighbor], [*relations, relation.relation_id]))
        return None


class GroundingService:
    def __init__(
        self,
        store: SemanticCatalogStore,
        linker: BidirectionalGroundingLinker,
        *,
        tenant_id: str = "default",
    ) -> None:
        self.store = store
        self.linker = linker
        self.tenant_id = tenant_id

    def search(self, query: str) -> GroundingBundle:
        catalog = self.store.load_snapshot(self.tenant_id)
        snapshot = self.linker.link(query, catalog, tenant_id=self.tenant_id)
        return GroundingBundle(snapshot=snapshot, catalog=catalog)

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import psycopg
from psycopg.rows import dict_row

from app.grounding.models import (
    AssetStatus,
    AssetType,
    RelationStatus,
    RelationType,
    SchemaRelation,
    SemanticAsset,
    Sensitivity,
    TrustLevel,
    ValueDictionaryEntry,
)


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    assets: tuple[SemanticAsset, ...]
    relations: tuple[SchemaRelation, ...]
    values: tuple[ValueDictionaryEntry, ...]


class SemanticCatalogStore(Protocol):
    def load_snapshot(
        self, tenant_id: str = "default", *, at_time: datetime | None = None
    ) -> CatalogSnapshot: ...


class PostgresSemanticCatalogStore:
    """Read only published semantic metadata; never scan business rows."""

    def __init__(self, connection_string: str):
        self.connection_string = connection_string

    def load_snapshot(
        self, tenant_id: str = "default", *, at_time: datetime | None = None
    ) -> CatalogSnapshot:
        effective_at = at_time or datetime.now(timezone.utc)
        with psycopg.connect(
            self.connection_string,
            row_factory=dict_row,
            options="-c default_transaction_read_only=on -c statement_timeout=3000",
        ) as connection:
            assets = self._assets(connection, tenant_id, effective_at)
            relations = self._relations(connection, tenant_id, effective_at)
            values = self._values(connection, tenant_id, effective_at)
        return CatalogSnapshot(tuple(assets), tuple(relations), tuple(values))

    @staticmethod
    def _assets(connection, tenant_id: str, effective_at: datetime) -> list[SemanticAsset]:
        rows = connection.execute(
            """SELECT tenant_id,asset_id,asset_type,title,description,aliases,
                      schema_version,content_version,source_uri,source_hash,
                      trust_level,sensitivity,status,effective_from,effective_to,
                      owner_name,payload
               FROM semantic_catalog.semantic_assets
               WHERE tenant_id=%s AND status='PUBLISHED'
                 AND sensitivity <> 'RESTRICTED'
                 AND (effective_from IS NULL OR effective_from <= %s)
                 AND (effective_to IS NULL OR effective_to > %s)
               ORDER BY asset_id""",
            (tenant_id, effective_at, effective_at),
        ).fetchall()
        return [
            SemanticAsset(
                tenant_id=row["tenant_id"],
                asset_id=row["asset_id"],
                asset_type=AssetType(row["asset_type"]),
                title=row["title"],
                description=row["description"],
                aliases=tuple(row["aliases"] or ()),
                schema_version=row["schema_version"],
                content_version=row["content_version"],
                source_uri=row["source_uri"],
                source_hash=row["source_hash"].strip(),
                trust_level=TrustLevel(row["trust_level"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                status=AssetStatus(row["status"]),
                effective_from=row["effective_from"],
                effective_to=row["effective_to"],
                owner=row["owner_name"],
                payload=row["payload"] or {},
            )
            for row in rows
        ]

    @staticmethod
    def _relations(
        connection, tenant_id: str, effective_at: datetime
    ) -> list[SchemaRelation]:
        rows = connection.execute(
            """SELECT relation.tenant_id,relation.relation_id,
                      relation.left_asset_id,relation.right_asset_id,
                      relation.relation_type,relation.relation_status,
                      relation.cardinality,relation.join_expression,
                      relation.schema_version,relation.source_uri,
                      relation.source_hash,relation.trust_level,
                      relation.sensitivity,relation.left_nullable,
                      relation.right_unique,relation.historical_success_count,
                      relation.historical_failure_count,relation.last_verified_at,
                      relation.metadata
               FROM semantic_catalog.schema_relations AS relation
               JOIN semantic_catalog.semantic_assets AS left_asset
                 ON left_asset.tenant_id=relation.tenant_id
                AND left_asset.asset_id=relation.left_asset_id
               JOIN semantic_catalog.semantic_assets AS right_asset
                 ON right_asset.tenant_id=relation.tenant_id
                AND right_asset.asset_id=relation.right_asset_id
               WHERE relation.tenant_id=%s
                 AND relation.relation_status <> 'REJECTED'
                 AND relation.sensitivity <> 'RESTRICTED'
                 AND left_asset.status='PUBLISHED'
                 AND left_asset.sensitivity <> 'RESTRICTED'
                 AND (left_asset.effective_from IS NULL OR left_asset.effective_from <= %s)
                 AND (left_asset.effective_to IS NULL OR left_asset.effective_to > %s)
                 AND right_asset.status='PUBLISHED'
                 AND right_asset.sensitivity <> 'RESTRICTED'
                 AND (right_asset.effective_from IS NULL OR right_asset.effective_from <= %s)
                 AND (right_asset.effective_to IS NULL OR right_asset.effective_to > %s)
               ORDER BY relation.relation_id""",
            (tenant_id, effective_at, effective_at, effective_at, effective_at),
        ).fetchall()
        return [
            SchemaRelation(
                tenant_id=row["tenant_id"],
                relation_id=row["relation_id"],
                left_asset_id=row["left_asset_id"],
                right_asset_id=row["right_asset_id"],
                relation_type=RelationType(row["relation_type"]),
                status=RelationStatus(row["relation_status"]),
                cardinality=row["cardinality"],
                join_expression=row["join_expression"],
                schema_version=row["schema_version"],
                source_uri=row["source_uri"],
                source_hash=row["source_hash"].strip(),
                trust_level=TrustLevel(row["trust_level"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                left_nullable=row["left_nullable"],
                right_unique=row["right_unique"],
                historical_success_count=row["historical_success_count"],
                historical_failure_count=row["historical_failure_count"],
                last_verified_at=row["last_verified_at"],
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]

    @staticmethod
    def _values(connection, tenant_id: str, effective_at: datetime) -> list[ValueDictionaryEntry]:
        rows = connection.execute(
            """SELECT value.tenant_id,value.value_id,value.column_asset_id,
                      value.canonical_value,value.aliases,value.content_version,
                      value.source_uri,value.source_hash,value.trust_level,
                      value.sensitivity,value.status,value.effective_from,
                      value.effective_to,value.metadata
               FROM semantic_catalog.value_dictionary AS value
               JOIN semantic_catalog.semantic_assets AS parent
                 ON parent.tenant_id=value.tenant_id
                AND parent.asset_id=value.column_asset_id
               WHERE value.tenant_id=%s AND value.status='PUBLISHED'
                 AND value.sensitivity <> 'RESTRICTED'
                 AND (value.effective_from IS NULL OR value.effective_from <= %s)
                 AND (value.effective_to IS NULL OR value.effective_to > %s)
                 AND parent.status='PUBLISHED'
                 AND parent.sensitivity <> 'RESTRICTED'
                 AND (parent.effective_from IS NULL OR parent.effective_from <= %s)
                 AND (parent.effective_to IS NULL OR parent.effective_to > %s)
               ORDER BY value.value_id""",
            (tenant_id, effective_at, effective_at, effective_at, effective_at),
        ).fetchall()
        return [
            ValueDictionaryEntry(
                tenant_id=row["tenant_id"],
                value_id=row["value_id"],
                column_asset_id=row["column_asset_id"],
                canonical_value=row["canonical_value"],
                aliases=tuple(row["aliases"] or ()),
                content_version=row["content_version"],
                source_uri=row["source_uri"],
                source_hash=row["source_hash"].strip(),
                trust_level=TrustLevel(row["trust_level"]),
                sensitivity=Sensitivity(row["sensitivity"]),
                status=AssetStatus(row["status"]),
                effective_from=row["effective_from"],
                effective_to=row["effective_to"],
                metadata=row["metadata"] or {},
            )
            for row in rows
        ]

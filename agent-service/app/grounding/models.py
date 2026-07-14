from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class AssetType(StrEnum):
    DATABASE = "DATABASE"
    SCHEMA = "SCHEMA"
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    METRIC = "METRIC"
    QUERY_EXAMPLE = "QUERY_EXAMPLE"
    BUSINESS_RULE = "BUSINESS_RULE"


class AssetStatus(StrEnum):
    DRAFT = "DRAFT"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class TrustLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Sensitivity(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


class RelationType(StrEnum):
    FOREIGN_KEY = "FOREIGN_KEY"
    LOGICAL_JOIN = "LOGICAL_JOIN"


class RelationStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"


def _validate_version(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")


def _validate_identity(value: str, name: str) -> None:
    if not value or value.strip() != value or any(char.isspace() for char in value):
        raise ValueError(f"{name} must be a non-empty stable identifier")


def _validate_effective_range(
    effective_from: datetime | None, effective_to: datetime | None
) -> None:
    if effective_from and effective_from.tzinfo is None:
        raise ValueError("effective_from must be timezone-aware")
    if effective_to and effective_to.tzinfo is None:
        raise ValueError("effective_to must be timezone-aware")
    if effective_from and effective_to and effective_to <= effective_from:
        raise ValueError("effective_to must be later than effective_from")


@dataclass(frozen=True, slots=True)
class SemanticAsset:
    asset_id: str
    asset_type: AssetType
    title: str
    description: str
    source_uri: str
    source_hash: str
    tenant_id: str = "default"
    schema_version: int = 1
    content_version: int = 1
    trust_level: TrustLevel = TrustLevel.HIGH
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    status: AssetStatus = AssetStatus.PUBLISHED
    aliases: tuple[str, ...] = ()
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    owner: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identity(self.asset_id, "asset_id")
        _validate_identity(self.tenant_id, "tenant_id")
        _validate_version(self.schema_version, "schema_version")
        _validate_version(self.content_version, "content_version")
        if not self.title.strip() or not self.description.strip():
            raise ValueError("title and description must not be empty")
        if not self.source_uri.strip():
            raise ValueError("source_uri must not be empty")
        if not _SHA256.fullmatch(self.source_hash):
            raise ValueError("source_hash must be a lowercase SHA-256 hex digest")
        _validate_effective_range(self.effective_from, self.effective_to)


@dataclass(frozen=True, slots=True)
class SchemaRelation:
    relation_id: str
    left_asset_id: str
    right_asset_id: str
    relation_type: RelationType
    status: RelationStatus
    source_uri: str
    source_hash: str
    tenant_id: str = "default"
    schema_version: int = 1
    cardinality: str | None = None
    join_expression: str | None = None
    trust_level: TrustLevel = TrustLevel.HIGH
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identity(self.relation_id, "relation_id")
        _validate_identity(self.left_asset_id, "left_asset_id")
        _validate_identity(self.right_asset_id, "right_asset_id")
        _validate_identity(self.tenant_id, "tenant_id")
        _validate_version(self.schema_version, "schema_version")
        if self.left_asset_id == self.right_asset_id:
            raise ValueError("a schema relation must connect two different assets")
        if not self.source_uri.strip() or not _SHA256.fullmatch(self.source_hash):
            raise ValueError("relation source provenance is invalid")
        if self.status == RelationStatus.CANDIDATE and self.trust_level == TrustLevel.HIGH:
            raise ValueError("candidate relations cannot have HIGH trust")


@dataclass(frozen=True, slots=True)
class ValueDictionaryEntry:
    value_id: str
    column_asset_id: str
    canonical_value: str
    source_uri: str
    source_hash: str
    tenant_id: str = "default"
    aliases: tuple[str, ...] = ()
    content_version: int = 1
    status: AssetStatus = AssetStatus.PUBLISHED
    trust_level: TrustLevel = TrustLevel.HIGH
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identity(self.value_id, "value_id")
        _validate_identity(self.column_asset_id, "column_asset_id")
        _validate_identity(self.tenant_id, "tenant_id")
        _validate_version(self.content_version, "content_version")
        if not self.canonical_value.strip():
            raise ValueError("canonical_value must not be empty")
        if not self.source_uri.strip() or not _SHA256.fullmatch(self.source_hash):
            raise ValueError("value source provenance is invalid")
        _validate_effective_range(self.effective_from, self.effective_to)

from datetime import UTC, datetime

import pytest

from app.grounding import (
    AssetType,
    RelationStatus,
    RelationType,
    SchemaRelation,
    SemanticAsset,
    TrustLevel,
    ValueDictionaryEntry,
)


DIGEST = "a" * 64


def test_semantic_asset_requires_stable_provenance_and_versions():
    asset = SemanticAsset(
        asset_id="column:public.orders.status",
        asset_type=AssetType.COLUMN,
        title="orders.status",
        description="订单状态",
        source_uri="db://public/schema_catalog/1",
        source_hash=DIGEST,
    )
    assert asset.schema_version == 1

    with pytest.raises(ValueError, match="SHA-256"):
        SemanticAsset(
            asset_id="column:public.orders.status",
            asset_type=AssetType.COLUMN,
            title="orders.status",
            description="订单状态",
            source_uri="db://public/schema_catalog/1",
            source_hash="not-a-digest",
        )


def test_candidate_relation_cannot_be_high_trust():
    with pytest.raises(ValueError, match="candidate"):
        SchemaRelation(
            relation_id="join:orders-customers",
            left_asset_id="column:public.orders.customer_id",
            right_asset_id="column:public.customers.id",
            relation_type=RelationType.LOGICAL_JOIN,
            status=RelationStatus.CANDIDATE,
            source_uri="manual://candidate",
            source_hash=DIGEST,
            trust_level=TrustLevel.HIGH,
        )


def test_value_dictionary_rejects_invalid_effective_range():
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="later"):
        ValueDictionaryEntry(
            value_id="value:orders.status:PAID",
            column_asset_id="column:public.orders.status",
            canonical_value="PAID",
            source_uri="manual://orders-status",
            source_hash=DIGEST,
            effective_from=now,
            effective_to=now,
        )

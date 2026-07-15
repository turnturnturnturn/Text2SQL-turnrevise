from __future__ import annotations

from dataclasses import replace

from app.grounding import (
    AssetStatus,
    AssetType,
    BidirectionalGroundingLinker,
    CatalogSnapshot,
    RelationStatus,
    RelationType,
    SchemaRelation,
    SemanticAsset,
    Sensitivity,
    TrustLevel,
    ValueDictionaryEntry,
)


DIGEST = "a" * 64


def asset(asset_id, asset_type, title, description, aliases=(), payload=None):
    return SemanticAsset(
        asset_id=asset_id,
        asset_type=asset_type,
        title=title,
        description=description,
        aliases=tuple(aliases),
        payload=payload or {},
        source_uri=f"fixture://{asset_id}",
        source_hash=DIGEST,
    )


def relation(relation_id, left, right, status=RelationStatus.CONFIRMED):
    return SchemaRelation(
        relation_id=relation_id,
        left_asset_id=left,
        right_asset_id=right,
        relation_type=(
            RelationType.FOREIGN_KEY
            if status == RelationStatus.CONFIRMED
            else RelationType.LOGICAL_JOIN
        ),
        status=status,
        source_uri=f"fixture://{relation_id}",
        source_hash=DIGEST,
        trust_level=TrustLevel.HIGH if status == RelationStatus.CONFIRMED else TrustLevel.LOW,
        join_expression=f"{left} = {right}",
    )


def catalog() -> CatalogSnapshot:
    assets = []
    for table, description, aliases in (
        ("customers", "客户名称区域城市", ("客户", "用户")),
        ("orders", "订单状态销售额区域支付时间", ("订单", "销售额")),
        ("order_items", "订单明细商品销量成交价", ("订单明细", "销量")),
        ("products", "商品名称品类库存", ("商品", "产品", "品类")),
        ("refunds", "退款状态退款金额", ("退款", "退货")),
    ):
        assets.append(
            asset(
                f"table:public.{table}",
                AssetType.TABLE,
                table,
                description,
                aliases,
                {"database_schema": "public", "table_name": table},
            )
        )
    for table, column, description, aliases in (
        ("orders", "id", "订单ID", ()),
        ("orders", "status", "订单状态", ("已支付", "支付成功")),
        ("orders", "total_amount", "订单销售额", ("GMV", "成交额")),
        ("orders", "region", "订单区域", ("区域", "大区")),
        ("orders", "customer_id", "订单客户ID", ("客户",)),
        ("orders", "created_at", "订单创建下单时间", ("下单时间", "最近30天")),
        ("order_items", "order_id", "明细订单ID", ()),
        ("order_items", "product_id", "明细商品ID", ()),
        ("order_items", "quantity", "商品销量件数", ("销量",)),
        ("products", "id", "商品ID", ()),
        ("products", "category", "商品品类", ("品类", "类别")),
        ("customers", "id", "客户ID", ()),
        ("customers", "name", "客户名称", ("客户",)),
        ("refunds", "order_id", "退款订单ID", ()),
        ("refunds", "status", "退款状态", ("退款成功", "退款拒绝")),
    ):
        assets.append(
            asset(
                f"column:public.{table}.{column}",
                AssetType.COLUMN,
                f"{table}.{column}",
                description,
                aliases,
                {"database_schema": "public", "table_name": table, "column_name": column},
            )
        )
    assets.append(
        asset(
            "metric:gmv",
            AssetType.METRIC,
            "GMV",
            "已支付或已发货订单销售额",
            ("销售额", "成交额"),
            {"example_sql": "SELECT SUM(total_amount) FROM orders WHERE status IN ('PAID','SHIPPED')"},
        )
    )
    assets.append(
        asset(
            "metric:商品销售额",
            AssetType.METRIC,
            "商品销售额",
            "订单明细数量乘以成交价",
            ("品类销售额",),
            {"example_sql": "SELECT SUM(quantity * unit_price) FROM order_items"},
        )
    )
    relations = (
        relation("fk:orders.customer", "column:public.orders.customer_id", "column:public.customers.id"),
        relation("fk:items.order", "column:public.order_items.order_id", "column:public.orders.id"),
        relation("fk:items.product", "column:public.order_items.product_id", "column:public.products.id"),
        relation("fk:refund.order", "column:public.refunds.order_id", "column:public.orders.id"),
        relation(
            "candidate:customer-product",
            "column:public.customers.id",
            "column:public.products.id",
            RelationStatus.CANDIDATE,
        ),
    )
    values = (
        ValueDictionaryEntry(
            value_id="value:public.orders.status:PAID",
            column_asset_id="column:public.orders.status",
            canonical_value="PAID",
            aliases=("已支付", "支付成功"),
            source_uri="fixture://paid",
            source_hash=DIGEST,
        ),
        ValueDictionaryEntry(
            value_id="value:public.refunds.status:REJECTED",
            column_asset_id="column:public.refunds.status",
            canonical_value="REJECTED",
            aliases=("退款被拒绝", "已拒绝"),
            source_uri="fixture://rejected",
            source_hash=DIGEST,
        ),
    )
    return CatalogSnapshot(tuple(assets), relations, values)


def test_bidirectional_linking_recovers_tables_columns_values_and_confirmed_path():
    result = BidirectionalGroundingLinker().link(
        "各商品品类的已支付订单销量", catalog()
    )

    table_ids = {item.asset_id for item in result.table_candidates}
    column_ids = {item.asset_id for item in result.column_candidates}
    assert {"table:public.orders", "table:public.order_items", "table:public.products"} <= table_ids
    assert "column:public.products.category" in column_ids
    assert "column:public.order_items.quantity" in column_ids
    assert result.value_candidates[0].canonical_value == "PAID"
    assert any(len(path.relation_ids) >= 2 for path in result.join_paths)
    assert all(
        "candidate:customer-product" not in path.relation_ids
        for path in result.join_paths
    )


def test_candidate_join_is_reported_as_ambiguity_but_never_executable_path():
    result = BidirectionalGroundingLinker().link("客户购买的商品", catalog())
    assert "CANDIDATE_JOIN:candidate:customer-product" in result.ambiguities
    assert all(
        "candidate:customer-product" not in path.relation_ids
        for path in result.join_paths
    )


def test_value_linking_uses_curated_aliases():
    result = BidirectionalGroundingLinker().link("退款被拒绝的订单", catalog())
    assert result.value_candidates[0].value_id.endswith(":REJECTED")


def test_embedding_failure_falls_back_to_lexical_catalog():
    class BrokenEmbedder:
        def encode(self, texts, normalize_embeddings=True):
            raise RuntimeError("offline")

    result = BidirectionalGroundingLinker(embedder=BrokenEmbedder()).link(
        "订单销售额", catalog()
    )
    assert result.table_candidates[0].asset_id == "table:public.orders"


def test_value_linking_uses_semantic_recall_when_aliases_do_not_match():
    class SemanticValueEmbedder:
        def encode(self, texts, normalize_embeddings=True):
            vectors = []
            for index, text in enumerate(texts):
                if index == 0 or "REJECTED" in text:
                    vectors.append([1.0, 0.0])
                else:
                    vectors.append([0.0, 1.0])
            return vectors

    result = BidirectionalGroundingLinker(embedder=SemanticValueEmbedder()).link(
        "拒付申请", catalog()
    )
    assert result.value_candidates[0].value_id.endswith(":REJECTED")


def test_confirmed_path_cannot_traverse_an_asset_absent_from_approved_catalog():
    approved_assets = tuple(
        item
        for item in catalog().assets
        if item.asset_id
        in {
            "table:public.customers",
            "column:public.customers.id",
            "table:public.products",
            "column:public.products.id",
        }
    )
    relations = (
        relation(
            "fk:customers.secret",
            "column:public.customers.id",
            "column:restricted.secret.customer_id",
        ),
        relation(
            "fk:secret.products",
            "column:restricted.secret.product_id",
            "column:public.products.id",
        ),
    )
    result = BidirectionalGroundingLinker().link(
        "客户购买商品", CatalogSnapshot(approved_assets, relations, ())
    )
    assert result.join_paths == ()


def test_unrelated_question_does_not_authorize_zero_relevance_assets_or_values():
    result = BidirectionalGroundingLinker().link("明天会不会下雨", catalog())
    assert result.table_candidates == ()
    assert result.column_candidates == ()
    assert result.value_candidates == ()
    assert result.evidence_ids == ()


def test_restricted_parent_column_excludes_column_values_and_relations():
    base = catalog()
    restricted_assets = tuple(
        replace(item, sensitivity=Sensitivity.RESTRICTED)
        if item.asset_id == "column:public.orders.status"
        else item
        for item in base.assets
    )
    result = BidirectionalGroundingLinker().link(
        "已支付订单状态",
        CatalogSnapshot(restricted_assets, base.relations, base.values),
    )
    assert all(
        item.asset_id != "column:public.orders.status"
        for item in result.column_candidates
    )
    assert all(
        item.column_asset_id != "column:public.orders.status"
        for item in result.value_candidates
    )

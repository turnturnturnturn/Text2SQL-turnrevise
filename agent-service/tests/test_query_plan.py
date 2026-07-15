from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.grounding import AssetType, BidirectionalGroundingLinker, CatalogSnapshot
from app.grounding.plan_store import InMemoryQueryPlanStore
from app.grounding.query_plan import QueryPlanDraft, QueryPlanStatus, QueryPlanValidator
from app.security.query_plan_guard import query_plan_alignment_errors
from tests.test_grounding_linker import asset, catalog


def valid_draft(snapshot):
    path = next(
        path
        for path in snapshot.join_paths
        if "table:public.orders" in path.table_asset_ids
        and "table:public.products" in path.table_asset_ids
    )
    relation_evidence = list(path.relation_ids)
    return QueryPlanDraft.model_validate(
        {
            "metrics": [{"id": "metric:商品销售额", "alias": "sales"}],
            "dimensions": [
                {"asset_id": "column:public.products.category", "alias": "category"}
            ],
            "filters": [
                {
                    "asset_id": "column:public.orders.status",
                    "op": "IN",
                    "value_ids": ["value:public.orders.status:PAID"],
                }
            ],
            "grain": ["column:public.products.category"],
            "sort": [{"field": "sales", "direction": "DESC"}],
            "limit": 200,
            "join_path_ids": [path.path_id],
            "evidence_ids": [
                "metric:商品销售额",
                "column:public.products.category",
                "column:public.orders.status",
                "value:public.orders.status:PAID",
                *relation_evidence,
            ],
        }
    )


def linked():
    snapshot = BidirectionalGroundingLinker().link(
        "各商品品类的已支付订单销售额", catalog()
    )
    return snapshot, catalog()


def test_query_plan_validates_grounded_assets_values_and_confirmed_path():
    snapshot, semantic_catalog = linked()
    validated = QueryPlanValidator().validate(
        valid_draft(snapshot), snapshot, semantic_catalog
    )
    assert validated.status == QueryPlanStatus.VALID
    assert len(validated.plan_hash) == 64
    assert validated.confidence > 0


def test_query_plan_blocks_tampered_evidence_and_value_column_mismatch():
    snapshot, semantic_catalog = linked()
    raw = valid_draft(snapshot).model_dump(mode="json")
    raw["filters"][0]["asset_id"] = "column:public.refunds.status"
    raw["evidence_ids"].append("column:public.refunds.status")
    validated = QueryPlanValidator().validate(
        QueryPlanDraft.model_validate(raw), snapshot, semantic_catalog
    )
    assert validated.status == QueryPlanStatus.BLOCKED
    assert any("VALUE_COLUMN_MISMATCH" in item for item in validated.errors)


def test_query_plan_requires_clarification_for_unresolved_ambiguity():
    snapshot, semantic_catalog = linked()
    raw = valid_draft(snapshot).model_dump(mode="json")
    raw["ambiguities"] = [
        {"code": "TIME_FIELD", "message": "按支付还是下单时间", "option_ids": ["a", "b"]}
    ]
    validated = QueryPlanValidator().validate(
        QueryPlanDraft.model_validate(raw), snapshot, semantic_catalog
    )
    assert validated.status == QueryPlanStatus.NEEDS_CLARIFICATION
    assert validated.confidence <= 0.49


def test_query_plan_blocks_raw_identifier_literals():
    semantic_catalog = catalog()
    snapshot = BidirectionalGroundingLinker().link("订单ID", semantic_catalog)
    draft = QueryPlanDraft.model_validate(
        {
            "filters": [
                {
                    "asset_id": "column:public.orders.id",
                    "op": "EQ",
                    "literal_values": [123456],
                }
            ],
            "evidence_ids": ["column:public.orders.id"],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    assert validated.status == QueryPlanStatus.BLOCKED
    assert "IDENTIFIER_LITERAL_FORBIDDEN:column:public.orders.id" in validated.errors


def test_query_plan_rejects_disconnected_confirmed_path_components():
    semantic_catalog = catalog()
    snapshot = BidirectionalGroundingLinker().link(
        "客户 商品 订单 已支付 销量", semantic_catalog
    )
    customer_orders = next(
        path
        for path in snapshot.join_paths
        if set(path.table_asset_ids)
        == {"table:public.customers", "table:public.orders"}
    )
    items_products = next(
        path
        for path in snapshot.join_paths
        if set(path.table_asset_ids)
        == {"table:public.order_items", "table:public.products"}
    )
    draft = QueryPlanDraft.model_validate(
        {
            "metrics": [{"id": "metric:商品销售额", "alias": "sales"}],
            "dimensions": [
                {"asset_id": "column:public.customers.name", "alias": "name"},
                {
                    "asset_id": "column:public.products.category",
                    "alias": "category",
                },
            ],
            "filters": [
                {
                    "asset_id": "column:public.orders.status",
                    "op": "IN",
                    "value_ids": ["value:public.orders.status:PAID"],
                }
            ],
            "grain": [
                "column:public.customers.name",
                "column:public.products.category",
            ],
            "join_path_ids": [customer_orders.path_id, items_products.path_id],
            "evidence_ids": [
                "metric:商品销售额",
                "column:public.customers.name",
                "column:public.products.category",
                "column:public.orders.status",
                "value:public.orders.status:PAID",
                *customer_orders.relation_ids,
                *items_products.relation_ids,
            ],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    assert any("DISCONNECTED_JOIN_PATHS" in item for item in validated.errors)


def test_query_plan_schema_rejects_duplicate_alias_and_invalid_custom_time():
    with pytest.raises(ValidationError, match="aliases must be unique"):
        QueryPlanDraft.model_validate(
            {
                "metrics": [{"id": "metric:gmv", "alias": "same"}],
                "dimensions": [{"asset_id": "column:public.orders.region", "alias": "same"}],
                "evidence_ids": ["metric:gmv"],
            }
        )

    with pytest.raises(ValidationError):
        QueryPlanDraft.model_validate(
            {
                "filters": [
                    {
                        "asset_id": "column:public.customers.name",
                        "op": "EQ",
                        "literal_values": ["raw customer name"],
                    }
                ],
                "evidence_ids": ["column:public.customers.name"],
            }
        )
    with pytest.raises(ValidationError, match="later than start"):
        QueryPlanDraft.model_validate(
            {
                "time_range": {
                    "field_id": "column:public.orders.created_at",
                    "preset": "CUSTOM",
                    "start": datetime(2026, 1, 2, tzinfo=UTC),
                    "end": datetime(2026, 1, 1, tzinfo=UTC),
                },
                "evidence_ids": ["column:public.orders.created_at"],
            }
        )


def test_sql_alignment_accepts_plan_and_rejects_missing_value_group_and_unplanned_table():
    snapshot, semantic_catalog = linked()
    validated = QueryPlanValidator().validate(
        valid_draft(snapshot), snapshot, semantic_catalog
    )
    sql = """SELECT p.category, SUM(oi.quantity * oi.unit_price) AS sales
             FROM order_items oi
             JOIN products p ON p.id=oi.product_id
             JOIN orders o ON o.id=oi.order_id
             WHERE o.status IN ('PAID')
             GROUP BY p.category ORDER BY sales DESC LIMIT 200"""
    assert query_plan_alignment_errors(sql, validated, snapshot, semantic_catalog) == []
    bad = sql.replace("o.status IN ('PAID')", "o.status IN ('SHIPPED')").replace(
        "GROUP BY p.category", ""
    ).replace("FROM order_items oi", "FROM order_items oi JOIN refunds r ON r.order_id=oi.order_id")
    errors = query_plan_alignment_errors(bad, validated, snapshot, semantic_catalog)
    assert any("PLANNED_VALUE_MISSING" in item for item in errors)
    assert any("GRAIN_NOT_GROUPED" in item for item in errors)
    assert any("UNPLANNED_TABLES" in item for item in errors)

    wrong_operator = sql.replace("o.status IN ('PAID')", "o.status <> 'PAID'")
    assert any(
        "PLANNED_OPERATOR_MISSING" in item
        for item in query_plan_alignment_errors(
            wrong_operator, validated, snapshot, semantic_catalog
        )
    )

    misplaced_value = sql.replace(
        "o.status IN ('PAID')",
        "o.status IN ('SHIPPED') AND p.category <> 'PAID'",
    )
    assert any(
        "PLANNED_VALUE_MISSING" in item
        for item in query_plan_alignment_errors(
            misplaced_value, validated, snapshot, semantic_catalog
        )
    )

    widened_filter = sql.replace("IN ('PAID')", "IN ('PAID','SHIPPED')")
    assert any(
        "UNPLANNED_FILTER_VALUES" in item
        for item in query_plan_alignment_errors(
            widened_filter, validated, snapshot, semantic_catalog
        )
    )

    wrong_metric = sql.replace(
        "SUM(oi.quantity * oi.unit_price)",
        "COUNT(oi.quantity * oi.unit_price)",
    )
    assert any(
        "METRIC_AGGREGATE_MISMATCH" in item
        for item in query_plan_alignment_errors(
            wrong_metric, validated, snapshot, semantic_catalog
        )
    )
    wrong_formula = sql.replace(
        "SUM(oi.quantity * oi.unit_price)",
        "SUM(oi.quantity + oi.unit_price)",
    )
    assert any(
        "METRIC_AGGREGATE_MISMATCH" in item
        for item in query_plan_alignment_errors(
            wrong_formula, validated, snapshot, semantic_catalog
        )
    )
    decoy_metric = sql.replace(
        "SUM(oi.quantity * oi.unit_price) AS sales",
        "SUM(oi.quantity * oi.unit_price) AS ignored, "
        "COUNT(oi.quantity * oi.unit_price) AS sales",
    )
    assert any(
        "METRIC_AGGREGATE_MISMATCH" in item
        for item in query_plan_alignment_errors(
            decoy_metric, validated, snapshot, semantic_catalog
        )
    )
    wrapped_metric = sql.replace(
        "SUM(oi.quantity * oi.unit_price) AS sales",
        "SUM(oi.quantity * oi.unit_price) * 0 AS sales",
    )
    assert any(
        "METRIC_OUTPUT_EXPRESSION_MISMATCH" in item
        for item in query_plan_alignment_errors(
            wrapped_metric, validated, snapshot, semantic_catalog
        )
    )

    wrong_sort = sql.replace("ORDER BY sales DESC", "ORDER BY sales ASC")
    assert any(
        "QUERY_SORT_MISMATCH" in item
        for item in query_plan_alignment_errors(
            wrong_sort, validated, snapshot, semantic_catalog
        )
    )
    extra_sort = sql.replace(
        "ORDER BY sales DESC", "ORDER BY p.category ASC, sales DESC"
    )
    assert any(
        "QUERY_SORT_MISMATCH" in item
        for item in query_plan_alignment_errors(
            extra_sort, validated, snapshot, semantic_catalog
        )
    )

    assert "QUERY_LIMIT_REQUIRED" in query_plan_alignment_errors(
        sql.replace(" LIMIT 200", ""), validated, snapshot, semantic_catalog
    )
    union_sql = sql + " UNION ALL SELECT MIN(oi.order_id) AS sales FROM order_items oi LIMIT 200"
    assert "QUERY_PLAN_MULTISCOPE_UNSUPPORTED" in query_plan_alignment_errors(
        union_sql, validated, snapshot, semantic_catalog
    )

    ineffective_joins = sql.replace(
        "JOIN products p ON p.id=oi.product_id\n             JOIN orders o ON o.id=oi.order_id",
        "CROSS JOIN products p\n             CROSS JOIN orders o",
    ).replace(
        "WHERE o.status IN ('PAID')",
        "WHERE (p.id=oi.product_id OR TRUE) "
        "AND (o.id=oi.order_id OR TRUE) AND o.status IN ('PAID')",
    )
    assert any(
        "CONFIRMED_JOIN_MISSING" in item
        for item in query_plan_alignment_errors(
            ineffective_joins, validated, snapshot, semantic_catalog
        )
    )

    extra_output = sql.replace(
        "SUM(oi.quantity * oi.unit_price) AS sales",
        "SUM(oi.quantity * oi.unit_price) AS sales, "
        "MIN(oi.order_id) AS leaked_order_id",
    )
    assert any(
        "QUERY_OUTPUT_MISMATCH" in item
        for item in query_plan_alignment_errors(
            extra_output, validated, snapshot, semantic_catalog
        )
    )

    extra_filter = sql.replace(
        "WHERE o.status IN ('PAID')",
        "WHERE o.status IN ('PAID') AND o.region = '华东'",
    )
    assert any(
        "UNPLANNED_PREDICATE_COLUMNS" in item
        for item in query_plan_alignment_errors(
            extra_filter, validated, snapshot, semantic_catalog
        )
    )

    outer_join_filter = """SELECT p.category,
                           SUM(oi.quantity * oi.unit_price) AS sales
                           FROM order_items oi
                           JOIN products p ON p.id=oi.product_id
                           LEFT JOIN orders o
                             ON o.id=oi.order_id AND o.status IN ('PAID')
                           GROUP BY p.category ORDER BY sales DESC LIMIT 200"""
    outer_errors = query_plan_alignment_errors(
        outer_join_filter, validated, snapshot, semantic_catalog
    )
    assert any("CONFIRMED_JOIN_MISSING" in item for item in outer_errors)
    assert any("PLANNED_FILTER_MISSING" in item for item in outer_errors)

    ineffective_filter = sql.replace(
        "o.status IN ('PAID')", "(o.status IN ('PAID') OR TRUE)"
    )
    assert any(
        "PLANNED_OPERATOR_MISSING" in item
        for item in query_plan_alignment_errors(
            ineffective_filter, validated, snapshot, semantic_catalog
        )
    )


def test_sql_alignment_checks_time_preset_not_only_time_column():
    semantic_catalog = catalog()
    snapshot = BidirectionalGroundingLinker().link(
        "最近30天订单销售额", semantic_catalog
    )
    draft = QueryPlanDraft.model_validate(
        {
            "metrics": [{"id": "metric:gmv", "alias": "sales"}],
            "time_range": {
                "field_id": "column:public.orders.created_at",
                "preset": "LAST_30_DAYS",
            },
            "evidence_ids": ["metric:gmv", "column:public.orders.created_at"],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    assert validated.status == QueryPlanStatus.VALID
    sql = """SELECT SUM(o.total_amount) AS sales FROM orders o
             WHERE o.status IN ('PAID','SHIPPED')
               AND o.created_at >= now() - interval '30 days'
               AND o.created_at < now() LIMIT 200"""
    assert query_plan_alignment_errors(sql, validated, snapshot, semantic_catalog) == []
    reordered_metric_values = sql.replace(
        "('PAID','SHIPPED')", "('SHIPPED','PAID')"
    )
    assert query_plan_alignment_errors(
        reordered_metric_values, validated, snapshot, semantic_catalog
    ) == []
    errors = query_plan_alignment_errors(
        sql.replace("30 days", "7 days"), validated, snapshot, semantic_catalog
    )
    assert "TIME_PRESET_MISMATCH:LAST_30_DAYS" in errors
    wrong_direction = sql.replace("o.created_at >=", "o.created_at <")
    assert "TIME_PRESET_MISMATCH:LAST_30_DAYS" in query_plan_alignment_errors(
        wrong_direction, validated, snapshot, semantic_catalog
    )
    wrong_sign = sql.replace("now() - interval '30 days'", "now() + interval '30 days'")
    assert "TIME_PRESET_MISMATCH:LAST_30_DAYS" in query_plan_alignment_errors(
        wrong_sign, validated, snapshot, semantic_catalog
    )

    unrelated_interval = sql.replace(
        "o.created_at >= now() - interval '30 days'",
        "o.created_at >= DATE '2020-01-01' "
        "AND CURRENT_DATE >= CURRENT_DATE - interval '30 days'",
    )
    assert "TIME_PRESET_MISMATCH:LAST_30_DAYS" in query_plan_alignment_errors(
        unrelated_interval, validated, snapshot, semantic_catalog
    )

    today_raw = draft.model_dump(mode="json")
    today_raw["time_range"]["preset"] = "TODAY"
    today_plan = QueryPlanValidator().validate(
        QueryPlanDraft.model_validate(today_raw), snapshot, semantic_catalog
    )
    valid_today = """SELECT SUM(o.total_amount) AS sales FROM orders o
                     WHERE o.status IN ('PAID','SHIPPED')
                       AND o.created_at >= CURRENT_DATE
                       AND o.created_at < CURRENT_DATE + interval '1 day'
                     LIMIT 200"""
    assert query_plan_alignment_errors(
        valid_today, today_plan, snapshot, semantic_catalog
    ) == []
    midnight_only = valid_today.replace(
        "o.created_at >= CURRENT_DATE\n                       AND o.created_at < CURRENT_DATE + interval '1 day'",
        "o.created_at = CURRENT_DATE",
    )
    assert "TIME_PRESET_MISMATCH:TODAY" in query_plan_alignment_errors(
        midnight_only, today_plan, snapshot, semantic_catalog
    )
    empty_today = valid_today.replace(
        "CURRENT_DATE + interval '1 day'", "CURRENT_DATE - interval '1 day'"
    )
    assert "TIME_PRESET_MISMATCH:TODAY" in query_plan_alignment_errors(
        empty_today, today_plan, snapshot, semantic_catalog
    )


def test_sql_alignment_does_not_hide_physical_table_behind_same_named_cte():
    semantic_catalog = catalog()
    snapshot = BidirectionalGroundingLinker().link("订单GMV", semantic_catalog)
    draft = QueryPlanDraft.model_validate(
        {
            "metrics": [{"id": "metric:gmv", "alias": "sales"}],
            "evidence_ids": ["metric:gmv"],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    sql = """WITH refunds AS (SELECT amount FROM refunds)
             SELECT SUM(o.total_amount) AS sales FROM orders o
             WHERE o.status IN ('PAID','SHIPPED') LIMIT 200"""
    assert "UNPLANNED_TABLES:['refunds']" in query_plan_alignment_errors(
        sql, validated, snapshot, semantic_catalog
    )


def test_metric_predicates_in_join_on_are_part_of_the_required_definition():
    base = catalog()
    refund_metric = asset(
        "metric:refund_amount",
        AssetType.METRIC,
        "退款成功金额",
        "仅成功退款的金额",
        ("退款金额",),
        {
            "example_sql": "SELECT SUM(r.amount) FROM refunds r "
            "JOIN orders o ON o.id=r.order_id AND r.status='SUCCESS'"
        },
    )
    semantic_catalog = CatalogSnapshot(
        (*base.assets, refund_metric), base.relations, base.values
    )
    snapshot = BidirectionalGroundingLinker().link("退款金额指标", semantic_catalog)
    path = next(
        item
        for item in snapshot.join_paths
        if "table:public.refunds" in item.table_asset_ids
        and "table:public.orders" in item.table_asset_ids
    )
    draft = QueryPlanDraft.model_validate(
        {
            "metrics": [{"id": "metric:refund_amount", "alias": "refund_amount"}],
            "join_path_ids": [path.path_id],
            "evidence_ids": ["metric:refund_amount", *path.relation_ids],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    assert validated.status == QueryPlanStatus.VALID
    wrong_status = """SELECT SUM(r.amount) AS refund_amount
                      FROM refunds r JOIN orders o
                        ON o.id=r.order_id AND r.status='REJECTED'
                      LIMIT 200"""
    assert "METRIC_PREDICATE_MISMATCH:metric:refund_amount" in (
        query_plan_alignment_errors(
            wrong_status, validated, snapshot, semantic_catalog
        )
    )


def test_metric_output_binding_preserves_physical_source_table():
    base = catalog()
    item_metric = asset(
        "metric:item_order_total",
        AssetType.METRIC,
        "明细订单标识合计",
        "订单明细来源的订单标识合计",
        (),
        {"example_sql": "SELECT SUM(oi.order_id) FROM order_items oi"},
    )
    refund_metric = asset(
        "metric:refund_order_total",
        AssetType.METRIC,
        "退款订单标识合计",
        "退款来源的订单标识合计",
        (),
        {"example_sql": "SELECT SUM(r.order_id) FROM refunds r"},
    )
    semantic_catalog = CatalogSnapshot(
        (*base.assets, item_metric, refund_metric), base.relations, base.values
    )
    snapshot = BidirectionalGroundingLinker().link(
        "明细订单标识合计和退款订单标识合计", semantic_catalog
    )
    path = next(
        item
        for item in snapshot.join_paths
        if item.table_asset_ids[0] == "table:public.order_items"
        and item.table_asset_ids[-1] == "table:public.refunds"
    )
    draft = QueryPlanDraft.model_validate(
        {
            "metrics": [
                {"id": "metric:item_order_total", "alias": "item_total"},
                {"id": "metric:refund_order_total", "alias": "refund_total"},
            ],
            "join_path_ids": [path.path_id],
            "evidence_ids": [
                "metric:item_order_total",
                "metric:refund_order_total",
                *path.relation_ids,
            ],
        }
    )
    validated = QueryPlanValidator().validate(draft, snapshot, semantic_catalog)
    assert validated.status == QueryPlanStatus.VALID
    swapped = """SELECT SUM(r.order_id) AS item_total,
                        SUM(oi.order_id) AS refund_total
                 FROM order_items oi
                 JOIN orders o ON o.id=oi.order_id
                 JOIN refunds r ON r.order_id=o.id
                 LIMIT 200"""
    errors = query_plan_alignment_errors(
        swapped, validated, snapshot, semantic_catalog
    )
    assert any("METRIC_OUTPUT_EXPRESSION_MISMATCH" in item for item in errors)


@pytest.mark.asyncio
async def test_query_plan_store_deduplicates_plan_hash():
    snapshot, semantic_catalog = linked()
    validated = QueryPlanValidator().validate(
        valid_draft(snapshot), snapshot, semantic_catalog
    )
    store = InMemoryQueryPlanStore()
    first = await store.save("run", validated, snapshot)
    second = await store.save("run", validated, snapshot)
    assert first == second
    assert len(await store.list_for_run("run")) == 1

    raw = valid_draft(snapshot).model_dump(mode="json")
    raw["ambiguities"] = [
        {"code": "CHOICE", "message": "potentially sensitive free text", "option_ids": ["a", "b"]}
    ]
    ambiguous = QueryPlanValidator().validate(
        QueryPlanDraft.model_validate(raw), snapshot, semantic_catalog
    )
    saved = await store.save("run", ambiguous, snapshot)
    assert "message" not in saved["plan"]["ambiguities"][0]

    sensitive_draft = QueryPlanDraft.model_validate(
        {
            "filters": [
                {
                    "asset_id": "column:public.orders.id",
                    "op": "EQ",
                    "literal_values": [123456],
                }
            ],
            "time_range": {
                "field_id": "column:public.orders.created_at",
                "preset": "CUSTOM",
                "start": datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
                "end": datetime(2026, 1, 31, 18, 45, tzinfo=UTC),
            },
            "evidence_ids": [
                "column:public.orders.id",
                "column:public.orders.created_at",
            ],
        }
    )
    sensitive = validated.model_copy(
        update={"plan": sensitive_draft, "plan_hash": "b" * 64}
    )
    redacted = await store.save("run", sensitive, snapshot)
    assert "literal_values" not in redacted["plan"]["filters"][0]
    assert redacted["plan"]["filters"][0]["literal_value_count"] == 1
    assert redacted["plan"]["time_range"]["start_date"] == "2026-01-01"
    assert redacted["plan"]["time_range"]["end_date"] == "2026-01-31"
    assert "start" not in redacted["plan"]["time_range"]
    assert "end" not in redacted["plan"]["time_range"]

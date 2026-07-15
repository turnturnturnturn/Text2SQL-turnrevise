#!/usr/bin/env python3
"""Run deterministic Phase B Schema/Value Linking evaluation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent-service"
VENV_PYTHON = AGENT_ROOT / ".venv" / "bin" / "python"
if sys.version_info < (3, 11):
    if VENV_PYTHON.is_file():
        os.execv(
            str(VENV_PYTHON),
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    raise SystemExit("Python 3.11+ is required; create agent-service/.venv first")
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from datetime import UTC, datetime

from app.grounding import (
    AssetType,
    BidirectionalGroundingLinker,
    CatalogSnapshot,
    PostgresSemanticCatalogStore,
    RelationStatus,
    RelationType,
    SchemaRelation,
    SemanticAsset,
    TrustLevel,
    ValueDictionaryEntry,
)


DEFAULT_CASES = ROOT / "evaluation" / "grounding_cases.json"


TABLE_TERMS = {
    "customers": ("客户名称城市区域", ("客户", "用户", "公司")),
    "orders": ("订单状态销售额GMV区域支付下单时间", ("订单", "销售额", "成交额")),
    "order_items": ("订单明细商品销量件数成交价", ("订单明细", "销量", "购买")),
    "products": ("商品产品名称品类库存", ("商品", "产品", "品类", "库存")),
    "refunds": ("退款退货状态金额时间", ("退款", "退货", "已退款")),
    "suppliers": ("供应商采购合同履约", ("供应商", "采购")),
    "warehouses": ("仓库库位入库出库", ("仓库", "库位")),
    "audit_logs": ("系统审计操作日志", ("审计", "日志")),
}

COLUMN_TERMS = {
    "customers.id": ("客户唯一标识", ("客户ID",)),
    "customers.name": ("客户名称", ("客户名", "公司名称", "客户")),
    "customers.city": ("客户所在城市", ("城市",)),
    "customers.region": ("客户所属大区", ("区域", "地区")),
    "orders.id": ("订单唯一标识", ("订单ID", "订单量")),
    "orders.customer_id": ("订单所属客户", ("客户ID", "客户订单")),
    "orders.status": ("订单状态", ("支付状态", "草稿", "发货", "取消")),
    "orders.total_amount": ("订单总金额", ("销售额", "GMV", "成交额", "订单金额")),
    "orders.region": ("订单所属区域", ("区域", "地区", "大区")),
    "orders.paid_at": ("支付成功时间", ("付款时间", "支付时间")),
    "orders.created_at": ("订单创建下单时间", ("下单时间", "创建时间")),
    "orders.deleted_at": ("订单软删除时间", ("软删除", "无效订单")),
    "order_items.order_id": ("订单明细所属订单", ("订单ID",)),
    "order_items.product_id": ("订单明细商品", ("商品ID", "销量商品")),
    "order_items.quantity": ("订单明细购买数量", ("销量", "件数", "数量")),
    "order_items.unit_price": ("下单成交单价", ("成交价", "单价", "销售额")),
    "products.id": ("商品唯一标识", ("商品ID",)),
    "products.name": ("商品名称", ("商品", "产品")),
    "products.category": ("商品所属品类", ("品类", "类别")),
    "products.stock": ("商品可用库存", ("库存", "余量")),
    "refunds.order_id": ("退款所属订单", ("退款订单", "订单ID")),
    "refunds.status": ("退款状态", ("退款成功", "退款结果", "退款情况")),
    "refunds.amount": ("退款金额", ("退款额",)),
    "refunds.refunded_at": ("退款成功时间", ("退款时间",)),
}

VALUE_FIXTURES = (
    ("orders.status", "DRAFT", ("草稿", "草稿订单")),
    ("orders.status", "UNPAID", ("待支付", "未支付")),
    ("orders.status", "PAID", ("已支付", "付款成功", "支付完成")),
    ("orders.status", "SHIPPED", ("已发货", "已经发货")),
    ("orders.status", "CANCELLED", ("已取消", "取消")),
    ("refunds.status", "PENDING", ("退款处理中", "待退款")),
    ("refunds.status", "SUCCESS", ("退款成功", "已退款", "成功退货")),
    ("refunds.status", "REJECTED", ("退款被拒绝", "已拒绝", "拒绝退款")),
)


def digest(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def offline_catalog() -> CatalogSnapshot:
    assets = []
    for table, (description, aliases) in TABLE_TERMS.items():
        asset_id = f"table:public.{table}"
        assets.append(
            SemanticAsset(
                asset_id=asset_id,
                asset_type=AssetType.TABLE,
                title=table,
                description=description,
                aliases=aliases,
                source_uri=f"fixture://{asset_id}",
                source_hash=digest(asset_id),
                payload={"database_schema": "public", "table_name": table},
            )
        )
    for key, (description, aliases) in COLUMN_TERMS.items():
        table, column = key.split(".")
        asset_id = f"column:public.{key}"
        assets.append(
            SemanticAsset(
                asset_id=asset_id,
                asset_type=AssetType.COLUMN,
                title=key,
                description=description,
                aliases=aliases,
                source_uri=f"fixture://{asset_id}",
                source_hash=digest(asset_id),
                payload={
                    "database_schema": "public",
                    "table_name": table,
                    "column_name": column,
                },
            )
        )
    for metric_id, title, description, aliases, example_sql in (
        (
            "metric:gmv",
            "GMV",
            "已支付或已发货订单销售额标准口径",
            ("销售额", "成交额"),
            "SELECT SUM(total_amount) FROM orders WHERE status IN ('PAID','SHIPPED') AND deleted_at IS NULL",
        ),
        (
            "metric:退款率",
            "退款率",
            "退款成功订单数除以支付订单数，包含分子分母",
            ("退货比例",),
            "SELECT COUNT(DISTINCT r.order_id) FROM refunds r JOIN orders o ON o.id=r.order_id",
        ),
        (
            "metric:订单量",
            "订单量",
            "未软删除订单数量",
            ("订单数",),
            "SELECT COUNT(id) FROM orders WHERE deleted_at IS NULL",
        ),
        (
            "metric:商品销量",
            "商品销量",
            "订单明细数量之和，关联商品并过滤无效订单",
            ("销量", "购买件数"),
            "SELECT SUM(oi.quantity) FROM order_items oi JOIN products p ON p.id=oi.product_id JOIN orders o ON o.id=oi.order_id WHERE o.deleted_at IS NULL",
        ),
    ):
        assets.append(
            SemanticAsset(
                asset_id=metric_id,
                asset_type=AssetType.METRIC,
                title=title,
                description=description,
                aliases=aliases,
                source_uri=f"fixture://{metric_id}",
                source_hash=digest(metric_id),
                payload={"example_sql": example_sql},
            )
        )
    relations = []
    for relation_id, left, right in (
        ("fk:public.orders.customer_id->public.customers.id", "orders.customer_id", "customers.id"),
        ("fk:public.order_items.order_id->public.orders.id", "order_items.order_id", "orders.id"),
        ("fk:public.order_items.product_id->public.products.id", "order_items.product_id", "products.id"),
        ("fk:public.refunds.order_id->public.orders.id", "refunds.order_id", "orders.id"),
    ):
        relations.append(
            SchemaRelation(
                relation_id=relation_id,
                left_asset_id=f"column:public.{left}",
                right_asset_id=f"column:public.{right}",
                relation_type=RelationType.FOREIGN_KEY,
                status=RelationStatus.CONFIRMED,
                source_uri=f"fixture://{relation_id}",
                source_hash=digest(relation_id),
                trust_level=TrustLevel.HIGH,
                join_expression=f"public.{left} = public.{right}",
            )
        )
    relations.append(
        SchemaRelation(
            relation_id="candidate:customers-products",
            left_asset_id="column:public.customers.id",
            right_asset_id="column:public.products.id",
            relation_type=RelationType.LOGICAL_JOIN,
            status=RelationStatus.CANDIDATE,
            source_uri="fixture://candidate",
            source_hash=digest("candidate"),
            trust_level=TrustLevel.LOW,
        )
    )
    values = []
    for column, canonical, aliases in VALUE_FIXTURES:
        value_id = f"value:public.{column}:{canonical}"
        values.append(
            ValueDictionaryEntry(
                value_id=value_id,
                column_asset_id=f"column:public.{column}",
                canonical_value=canonical,
                aliases=aliases,
                source_uri=f"fixture://{value_id}",
                source_hash=digest(value_id),
            )
        )
    return CatalogSnapshot(tuple(assets), tuple(relations), tuple(values))


def load_suite(path: Path) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if len(suite.get("schema_cases", [])) < 25:
        raise ValueError("grounding suite requires at least 25 schema cases")
    if len(suite.get("value_cases", [])) < 20:
        raise ValueError("grounding suite requires at least 20 value cases")
    ids = [
        item["id"]
        for key in ("schema_cases", "value_cases", "negative_value_cases")
        for item in suite.get(key, [])
    ]
    if len(ids) != len(set(ids)):
        raise ValueError("grounding case ids must be unique")
    return suite


def evaluate(suite: dict[str, Any], catalog: CatalogSnapshot) -> dict[str, Any]:
    linker = BidirectionalGroundingLinker()
    schema_details = []
    table_hits = table_total = column_hits = column_total = 0
    join_hits = join_total = candidate_executions = 0
    for case in suite["schema_cases"]:
        snapshot = linker.link(case["query"], catalog)
        tables = [item.asset_id for item in snapshot.table_candidates[:5]]
        columns = [item.asset_id for item in snapshot.column_candidates[:10]]
        gold_tables = set(case.get("gold_table_ids", []))
        gold_columns = set(case.get("gold_column_ids", []))
        table_hits += len(gold_tables & set(tables))
        table_total += len(gold_tables)
        column_hits += len(gold_columns & set(columns))
        column_total += len(gold_columns)
        gold_relations = set(case.get("gold_relation_ids", []))
        exact_join = None
        if gold_relations:
            join_total += 1
            exact_join = any(set(path.relation_ids) == gold_relations for path in snapshot.join_paths)
            join_hits += exact_join
        candidate_executions += sum(
            relation_id.startswith("candidate:")
            for path in snapshot.join_paths
            for relation_id in path.relation_ids
        )
        schema_details.append(
            {
                "id": case["id"],
                "tables": tables,
                "columns": columns,
                "join_paths": [list(path.relation_ids) for path in snapshot.join_paths],
                "exact_join": exact_join,
            }
        )

    value_details = []
    value_hits = 0
    for case in suite["value_cases"]:
        snapshot = linker.link(case["query"], catalog)
        returned = [item.value_id for item in snapshot.value_candidates[:3]]
        hit = bool(set(returned) & set(case["gold_value_ids"]))
        value_hits += hit
        value_details.append({"id": case["id"], "values": returned, "hit": hit})

    negative_value_details = []
    negative_value_candidate_count = 0
    for case in suite.get("negative_value_cases", []):
        snapshot = linker.link(case["query"], catalog)
        returned = [item.value_id for item in snapshot.value_candidates[:3]]
        negative_value_candidate_count += len(returned)
        negative_value_details.append({"id": case["id"], "values": returned})

    metrics = {
        "table_recall_at_5": round(table_hits / table_total, 4),
        "column_recall_at_10": round(column_hits / column_total, 4),
        "join_path_exact_match": round(join_hits / join_total, 4),
        "value_recall_at_3": round(value_hits / len(suite["value_cases"]), 4),
        "candidate_join_execution_count": candidate_executions,
        "negative_value_candidate_count": negative_value_candidate_count,
    }
    return {
        "suite_version": suite["suite_version"],
        "generated_at": datetime.now(UTC).isoformat(),
        "metrics": metrics,
        "schema_details": schema_details,
        "value_details": value_details,
        "negative_value_details": negative_value_details,
    }


def enforce_targets(metrics: dict[str, Any], targets: dict[str, Any]) -> None:
    checks = (
        ("table_recall_at_5", "minimum_table_recall_at_5", lambda a, e: a >= e),
        ("column_recall_at_10", "minimum_column_recall_at_10", lambda a, e: a >= e),
        ("join_path_exact_match", "minimum_join_path_exact_match", lambda a, e: a >= e),
        ("value_recall_at_3", "minimum_value_recall_at_3", lambda a, e: a >= e),
        (
            "candidate_join_execution_count",
            "maximum_candidate_join_execution_count",
            lambda a, e: a <= e,
        ),
        (
            "negative_value_candidate_count",
            "maximum_negative_value_candidate_count",
            lambda a, e: a <= e,
        ),
    )
    failures = []
    for metric, target, predicate in checks:
        expected = float(targets[target])
        if not predicate(metrics[metric], expected):
            failures.append(f"{metric}={metrics[metric]} violates {target}={expected}")
    if failures:
        raise SystemExit("; ".join(failures))


def markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    return "\n".join(
        [
            "# Grounding v2 专项评测",
            "",
            f"- 题集版本：{result['suite_version']}",
            f"- Table Recall@5：{metrics['table_recall_at_5'] * 100:.2f}%",
            f"- Column Recall@10：{metrics['column_recall_at_10'] * 100:.2f}%",
            f"- Join Path Exact Match：{metrics['join_path_exact_match'] * 100:.2f}%",
            f"- Value Recall@3：{metrics['value_recall_at_3'] * 100:.2f}%",
            f"- Candidate Join 自动执行：{metrics['candidate_join_execution_count']}",
            f"- 无关问题 Value 误召回数：{metrics['negative_value_candidate_count']}",
            "",
            "以上指标来自本次生产 linker 代码的真实运行，不代表模型 Text2SQL 准确率。",
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--database-url")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "evaluation" / "reports")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = load_suite(args.cases)
    catalog = (
        PostgresSemanticCatalogStore(args.database_url).load_snapshot()
        if args.database_url
        else offline_catalog()
    )
    result = evaluate(suite, catalog)
    enforce_targets(result["metrics"], suite["targets"])
    metrics = result["metrics"]
    print(
        "Grounding suite passed: "
        f"tables={metrics['table_recall_at_5']} "
        f"columns={metrics['column_recall_at_10']} "
        f"joins={metrics['join_path_exact_match']} "
        f"values={metrics['value_recall_at_3']} "
        f"candidate_joins={metrics['candidate_join_execution_count']} "
        f"negative_values={metrics['negative_value_candidate_count']}"
    )
    if args.check:
        return
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    (args.output_dir / f"grounding-evaluation-{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / f"grounding-evaluation-{stamp}.md").write_text(
        markdown(result), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

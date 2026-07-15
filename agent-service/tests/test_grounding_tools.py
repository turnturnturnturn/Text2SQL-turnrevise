from __future__ import annotations

import pandas as pd
import pytest
from vanna.capabilities.sql_runner import RunSqlToolArgs

from app.grounding.context import (
    get_grounding_state,
    record_grounding,
    record_validated_plan,
)
from app.grounding.linker import BidirectionalGroundingLinker, GroundingBundle
from app.grounding.query_plan import QueryPlanValidator
from app.grounding.query_plan import QueryPlanDraft
from app.harness.context import bind_request_context, reset_request_context
from app.tools.safe_read_sql import SafeReadSqlTool
from app.tools.query_plan import ValidateQueryPlanTool
from tests.test_grounding_linker import catalog
from tests.test_query_plan import valid_draft


SQL = """SELECT p.category, SUM(oi.quantity * oi.unit_price) AS sales
         FROM order_items oi
         JOIN products p ON p.id=oi.product_id
         JOIN orders o ON o.id=oi.order_id
         WHERE o.status IN ('PAID')
         GROUP BY p.category ORDER BY sales DESC LIMIT 200"""


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def run(self, sql):
        self.calls.append(sql)
        return pd.DataFrame([{"category": "仓储设备", "sales": 100}])


class FailingPlanStore:
    async def save(self, run_id, plan, snapshot):
        raise RuntimeError("state database unavailable")

    async def list_for_run(self, run_id):
        return []


def grounded_state(with_plan: bool):
    semantic_catalog = catalog()
    snapshot = BidirectionalGroundingLinker().link(
        "各商品品类的已支付订单销售额", semantic_catalog
    )
    bundle = GroundingBundle(snapshot, semantic_catalog)
    record_grounding(bundle)
    if with_plan:
        record_validated_plan(
            QueryPlanValidator().validate(
                valid_draft(snapshot), snapshot, semantic_catalog
            )
        )


@pytest.mark.asyncio
async def test_enforce_mode_fails_closed_without_query_plan():
    tokens = bind_request_context("run", "各商品品类的已支付订单销售额")
    runner = FakeRunner()
    try:
        grounded_state(with_plan=False)
        result = await SafeReadSqlTool(
            runner, grounding_mode="enforce"
        ).execute(None, RunSqlToolArgs(sql=SQL))
    finally:
        reset_request_context(tokens)
    assert not result.success
    assert result.metadata["error_type"] == "semantic_validation"
    assert runner.calls == []


@pytest.mark.asyncio
async def test_enforce_mode_executes_only_sql_aligned_with_valid_plan():
    tokens = bind_request_context("run", "各商品品类的已支付订单销售额")
    runner = FakeRunner()
    try:
        grounded_state(with_plan=True)
        result = await SafeReadSqlTool(
            runner, grounding_mode="enforce"
        ).execute(None, RunSqlToolArgs(sql=SQL))
    finally:
        reset_request_context(tokens)
    assert result.success
    assert result.metadata["query_plan_hash"]
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_shadow_mode_records_plan_gap_but_does_not_block_current_path():
    tokens = bind_request_context("run", "各商品品类的已支付订单销售额")
    runner = FakeRunner()
    try:
        grounded_state(with_plan=False)
        result = await SafeReadSqlTool(
            runner, grounding_mode="shadow"
        ).execute(None, RunSqlToolArgs(sql=SQL))
    finally:
        reset_request_context(tokens)
    assert result.success
    assert result.metadata["query_plan_shadow_errors"] == [
        "VALID_QUERY_PLAN_REQUIRED"
    ]
    assert len(runner.calls) == 1


@pytest.mark.asyncio
async def test_enforce_plan_persistence_failure_does_not_authorize_sql():
    tokens = bind_request_context("run", "各商品品类的已支付订单销售额")
    try:
        grounded_state(with_plan=False)
        state = get_grounding_state()
        assert state is not None and state.bundle is not None
        result = await ValidateQueryPlanTool(
            QueryPlanValidator(),
            store=FailingPlanStore(),
            grounding_mode="enforce",
        ).execute(None, valid_draft(state.bundle.snapshot))
        state = get_grounding_state()
    finally:
        reset_request_context(tokens)

    assert not result.success
    assert state is not None
    assert state.validated_plan is None


@pytest.mark.asyncio
async def test_shadow_query_plan_difference_is_observational_not_blocking():
    tokens = bind_request_context("run", "各商品品类的已支付订单销售额")
    try:
        grounded_state(with_plan=False)
        state = get_grounding_state()
        assert state is not None and state.bundle is not None
        draft = QueryPlanDraft.model_validate(
            {
                "dimensions": [
                    {
                        "asset_id": "column:public.orders.not_real",
                        "alias": "unknown",
                    }
                ],
                "evidence_ids": ["column:public.orders.not_real"],
            }
        )
        result = await ValidateQueryPlanTool(
            QueryPlanValidator(), grounding_mode="shadow"
        ).execute(None, draft)
    finally:
        reset_request_context(tokens)

    assert result.success
    assert result.metadata["status"] == "BLOCKED"
    assert result.metadata["error_type"] is None

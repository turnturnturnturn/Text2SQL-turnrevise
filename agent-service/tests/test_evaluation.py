from decimal import Decimal

import pytest

from app.evaluation import (
    aggregate_harness_metrics,
    compare_records,
    evaluate_memory_suite,
    evaluate_context_suite,
    extract_dataframe_rows,
    harness_metrics_markdown,
    memory_suite_markdown,
    validate_memory_suite,
)


def test_compare_records_ignores_row_and_column_order():
    actual = [{"amount": "10.0000001", "region": "华东"}, {"amount": 5, "region": "华北"}]
    expected = [{"region": "华北", "amount": 5.0}, {"region": "华东", "amount": 10}]
    assert compare_records(actual, expected, tolerance=Decimal("0.000001")) == (True, None)


def test_compare_records_rejects_missing_column():
    equivalent, message = compare_records([{"amount": 10}], [{"amount": 10, "region": "华东"}])
    assert not equivalent
    assert "unexpected row" in message


def test_compare_records_preserves_duplicate_rows():
    equivalent, message = compare_records([{"count": 1}, {"count": 1}], [{"count": 1}, {"count": 2}])
    assert not equivalent
    assert "unexpected row" in message


def test_extract_dataframe_rows_only_accepts_safe_query_component():
    chunks = [
        {"rich": {"type": "dataframe", "data": {"title": "Other", "data": [{"x": 0}]}}},
        {"rich": {"type": "dataframe", "data": {"title": "安全查询结果", "data": [{"x": 1}]}}},
    ]
    assert extract_dataframe_rows(chunks) == [{"x": 1}]


def test_aggregate_harness_metrics_uses_explicit_denominators():
    metrics = aggregate_harness_metrics(
        [
            {"status": "COMPLETED", "tool_call_count": 2, "latency_ms": 100},
            {
                "status": "COMPLETED",
                "tool_call_count": 4,
                "latency_ms": 300,
                "correction_attempted": True,
                "correction_succeeded": True,
            },
            {
                "status": "FAILED",
                "failure_category": "TIMEOUT",
                "tool_call_count": 3,
                "latency_ms": 500,
                "correction_attempted": True,
                "correction_succeeded": False,
            },
            {"status": "CANCELLED"},
        ],
        [
            {
                "id": "m1",
                "gold_memory_ids": ["a"],
                "retrieved_memory_ids": ["x", "a", "z"],
                "incorrect_memory_present": True,
                "incorrect_memory_adopted": False,
            },
            {
                "id": "m2",
                "gold_memory_ids": ["b"],
                "retrieved_memory_ids": ["x", "y", "z", "q", "r", "b"],
                "incorrect_memory_present": True,
                "incorrect_memory_adopted": True,
            },
        ],
    )

    assert metrics["completion_rate"] == 0.5
    assert metrics["average_tool_calls"] == 3.0
    assert metrics["correction_success_rate"] == 0.5
    assert metrics["memory_recall_at_5"] == 0.5
    assert metrics["incorrect_memory_adoption_rate"] == 0.5
    assert metrics["incorrect_memory_exposure_rate"] is None
    assert metrics["ineligible_memory_exposure_rate"] is None
    assert metrics["average_latency_ms"] == 300.0
    assert metrics["failure_counts"] == {"CANCELLED": 1, "TIMEOUT": 1}
    assert metrics["memory_details"][1]["retrieved_memory_ids"] == ["x", "y", "z", "q", "r"]


def test_aggregate_harness_metrics_keeps_unmeasured_values_null():
    metrics = aggregate_harness_metrics([], [])
    assert metrics["completion_rate"] is None
    assert metrics["average_tool_calls"] is None
    assert metrics["correction_success_rate"] is None
    assert metrics["memory_recall_at_5"] is None
    assert metrics["incorrect_memory_exposure_rate"] is None
    assert metrics["ineligible_memory_exposure_rate"] is None
    assert metrics["incorrect_memory_adoption_rate"] is None
    assert metrics["average_latency_ms"] is None
    assert "无可用样本" in "\n".join(harness_metrics_markdown(metrics))


def test_aggregate_harness_metrics_rejects_invalid_telemetry():
    with pytest.raises(ValueError, match="non-negative"):
        aggregate_harness_metrics([{"status": "FAILED", "latency_ms": -1}], [])
    with pytest.raises(ValueError, match="correction_succeeded"):
        aggregate_harness_metrics([{"status": "FAILED", "correction_attempted": True}], [])
    with pytest.raises(ValueError, match="retrieved_memory_ids"):
        aggregate_harness_metrics(
            [],
            [{"gold_memory_ids": ["a"], "retrieved_memory_ids": "a"}],
        )


def test_harness_metrics_markdown_does_not_claim_unrun_metrics():
    assert "未运行" in "\n".join(harness_metrics_markdown(None))


def test_validate_memory_suite_rejects_unknown_fixture_ids():
    with pytest.raises(ValueError, match="unknown ids"):
        validate_memory_suite(
            {
                "suite_version": "test",
                "memories": [
                    {"id": "known", "user_id": "alice", "content": "已知记忆"}
                ],
                "cases": [
                    {
                        "id": "case",
                        "user_id": "alice",
                        "query": "问题",
                        "gold_memory_ids": ["missing"],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_memory_suite_uses_confirmed_scope_and_expiry_rules():
    suite = {
        "suite_version": "test",
        "memories": [
            {"id": "gold", "user_id": "alice", "content": "销售额默认按订单区域"},
            {
                "id": "wrong",
                "user_id": "alice",
                "content": "错误记忆销售额按客户区域",
            },
            {
                "id": "candidate",
                "user_id": "alice",
                "status": "candidate",
                "content": "候选记忆销售额按注册区域",
            },
            {
                "id": "expired",
                "user_id": "alice",
                "expires_at": "2020-01-01T00:00:00+00:00",
                "content": "过期记忆销售额按历史区域",
            },
            {"id": "other-user", "user_id": "bob", "content": "销售额按 Bob 区域"},
        ],
        "cases": [
            {
                "id": "case",
                "user_id": "alice",
                "query": "销售额默认按哪个区域",
                "gold_memory_ids": ["gold"],
                "incorrect_memory_ids": ["wrong"],
                "ineligible_memory_ids": ["candidate", "expired", "other-user"],
            }
        ],
    }

    result = await evaluate_memory_suite(suite)
    metrics = result["metrics"]
    assert metrics["memory_recall_at_5"] == 1.0
    assert metrics["ineligible_memory_exposure_rate"] == 0.0
    assert set(result["cases"][0]["retrieved_memory_ids"]) <= {"gold", "wrong"}
    assert metrics["incorrect_memory_adoption_rate"] is None
    assert "错误记忆采用率：无可用样本" in memory_suite_markdown(result)


@pytest.mark.asyncio
async def test_context_suite_reports_real_denominators_and_nulls():
    suite = {
        "suite_version": "test-context",
        "context_cases": [
            {
                "id": "c1",
                "budget": 200,
                "items": [
                    {
                        "id": "safety",
                        "partition": "safety",
                        "content": "policy",
                        "mandatory": True,
                    },
                    {
                        "id": "constraint",
                        "partition": "conversation",
                        "content": "paid_at",
                        "priority": 100,
                    },
                ],
                "critical_item_ids": ["constraint"],
            }
        ],
        "clarification_cases": [
            {
                "id": "q1",
                "signals": {
                    "unresolved_ambiguities": 1,
                    "source_backed_option_count": 2,
                },
                "should_clarify": True,
            },
            {"id": "q2", "signals": {}, "should_clarify": False},
        ],
        "recovery_cases": [],
    }

    result = await evaluate_context_suite(suite)
    metrics = result["metrics"]
    assert metrics["provenance_coverage"] == 1.0
    assert metrics["critical_constraint_recall"] == 1.0
    assert metrics["clarification_required_recognition_rate"] == 1.0
    assert metrics["unnecessary_clarification_rate"] == 0.0
    assert metrics["restart_closure_rate"] is None
    assert metrics["write_recovery_count"] == 0

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


NUMERIC_TOLERANCE = Decimal("0.000001")


def _rate(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def aggregate_harness_metrics(
    runs: list[dict[str, Any]],
    memory_cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate real Harness traces without inventing missing measurements.

    A correction case is eligible only when ``correction_attempted`` is true.
    A memory recall case needs at least one ``gold_memory_ids`` item.  Incorrect
    memory adoption is measured only for cases explicitly marked
    ``incorrect_memory_present``.  This makes every denominator visible and
    prevents absent telemetry from being counted as a successful outcome.
    """
    completed = [run for run in runs if run.get("status") == "COMPLETED"]
    corrections = [run for run in runs if run.get("correction_attempted") is True]
    memory_recall_cases = [case for case in memory_cases if case.get("gold_memory_ids")]
    incorrect_memory_cases = [
        case for case in memory_cases if case.get("incorrect_memory_present") is True
    ]

    if any(not isinstance(run.get("correction_succeeded"), bool) for run in corrections):
        raise ValueError("correction_succeeded must be boolean for correction cases")
    if any(not isinstance(case.get("retrieved_memory_ids"), list) for case in memory_recall_cases):
        raise ValueError("retrieved_memory_ids must be an array for memory recall cases")
    if any(
        not isinstance(case.get("incorrect_memory_adopted"), bool)
        for case in incorrect_memory_cases
    ):
        raise ValueError(
            "incorrect_memory_adopted must be boolean when incorrect memory is present"
        )

    tool_counts = [run["tool_call_count"] for run in runs if run.get("tool_call_count") is not None]
    latencies = [run["latency_ms"] for run in runs if run.get("latency_ms") is not None]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        for value in tool_counts + latencies
    ):
        raise ValueError("tool_call_count and latency_ms must be non-negative numbers")

    recalled = 0
    memory_details: list[dict[str, Any]] = []
    for case in memory_recall_cases:
        gold = set(case["gold_memory_ids"])
        retrieved = list(case.get("retrieved_memory_ids", []))[:5]
        hit = bool(gold.intersection(retrieved))
        recalled += hit
        memory_details.append(
            {
                "id": case.get("id"),
                "hit_at_5": hit,
                "gold_memory_ids": sorted(gold),
                "retrieved_memory_ids": retrieved,
            }
        )

    failed = [run for run in runs if run.get("status") in {"FAILED", "CANCELLED"}]
    failure_counts = Counter(
        str(run.get("failure_category") or ("CANCELLED" if run.get("status") == "CANCELLED" else "UNKNOWN"))
        for run in failed
    )
    correction_successes = sum(run.get("correction_succeeded") is True for run in corrections)
    incorrect_adoptions = sum(
        case.get("incorrect_memory_adopted") is True for case in incorrect_memory_cases
    )

    return {
        "run_count": len(runs),
        "completed_count": len(completed),
        "completion_rate": _rate(len(completed), len(runs)),
        "tool_call_observation_count": len(tool_counts),
        "average_tool_calls": None if not tool_counts else round(sum(tool_counts) / len(tool_counts), 4),
        "correction_case_count": len(corrections),
        "correction_success_count": correction_successes,
        "correction_success_rate": _rate(correction_successes, len(corrections)),
        "memory_case_count": len(memory_recall_cases),
        "memory_recall_at_5": _rate(recalled, len(memory_recall_cases)),
        "incorrect_memory_case_count": len(incorrect_memory_cases),
        "incorrect_memory_adoption_count": incorrect_adoptions,
        "incorrect_memory_adoption_rate": _rate(incorrect_adoptions, len(incorrect_memory_cases)),
        "latency_observation_count": len(latencies),
        "average_latency_ms": None if not latencies else round(sum(latencies) / len(latencies), 2),
        "failure_counts": dict(sorted(failure_counts.items())),
        "memory_details": memory_details,
    }


def harness_metrics_markdown(metrics: dict[str, Any] | None) -> list[str]:
    """Render a compact Markdown section from aggregate_harness_metrics output."""
    if metrics is None:
        return ["- 未运行；使用 `--harness-input` 传入真实运行轨迹。"]

    def percent(value: float | None) -> str:
        return "无可用样本" if value is None else f"{value * 100:.2f}%"

    failures = metrics["failure_counts"]
    failure_text = "无" if not failures else "、".join(f"{key}={value}" for key, value in failures.items())
    average_tool_calls = metrics["average_tool_calls"]
    average_latency = metrics["average_latency_ms"]
    return [
        f"- 完成率：{percent(metrics['completion_rate'])}（{metrics['completed_count']}/{metrics['run_count']}）",
        f"- 平均工具调用数：{'无可用样本' if average_tool_calls is None else f'{average_tool_calls:.2f}'}",
        f"- 纠错成功率：{percent(metrics['correction_success_rate'])}（{metrics['correction_case_count']} 个纠错样本）",
        f"- Memory Recall@5：{percent(metrics['memory_recall_at_5'])}（{metrics['memory_case_count']} 个召回样本）",
        f"- 错误记忆采用率：{percent(metrics['incorrect_memory_adoption_rate'])}（{metrics['incorrect_memory_case_count']} 个注入样本）",
        f"- 平均延迟：{'无可用样本' if average_latency is None else f'{average_latency:.2f} ms'}",
        f"- 失败分类：{failure_text}",
    ]


def _numeric(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    # Pydantic serializes PostgreSQL Decimal values in SSE as strings.
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _scalar_equal(actual: Any, expected: Any, tolerance: Decimal) -> bool:
    if actual is None or expected is None:
        return actual is expected
    expected_numeric = _numeric(expected)
    actual_numeric = _numeric(actual)
    if expected_numeric is not None:
        return actual_numeric is not None and abs(actual_numeric - expected_numeric) <= tolerance
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual == expected
    return type(actual) is type(expected) and actual == expected


def compare_records(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    *,
    tolerance: Decimal = NUMERIC_TOLERANCE,
) -> tuple[bool, str | None]:
    """Compare tabular records without hiding column, duplicate, or value errors."""
    if len(actual) != len(expected):
        return False, f"row count differs: actual={len(actual)} expected={len(expected)}"

    unmatched = list(expected)
    for actual_row in actual:
        actual_columns = set(actual_row)
        match_index = None
        for index, expected_row in enumerate(unmatched):
            if actual_columns != set(expected_row):
                continue
            if all(
                _scalar_equal(actual_row[column], expected_row[column], tolerance)
                for column in actual_columns
            ):
                match_index = index
                break
        if match_index is None:
            return False, f"unexpected row: {actual_row!r}"
        unmatched.pop(match_index)
    return True, None


def extract_dataframe_rows(chunks: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """Return records from the approved SafeReadSqlTool dataframe component."""
    for chunk in chunks:
        rich = chunk.get("rich")
        if not isinstance(rich, dict):
            continue
        data = rich.get("data")
        if rich.get("type") == "dataframe" and isinstance(data, dict) and data.get("title") == "安全查询结果":
            rows = data.get("data")
            if isinstance(rows, list) and all(isinstance(row, dict) for row in rows):
                return rows
    return None


@dataclass(frozen=True)
class LiveAgentResponse:
    chunks: list[dict[str, Any]]
    error: str | None = None


class LiveAgentClient:
    def __init__(self, agent_url: str, business_url: str, timeout_seconds: float = 180):
        self.agent_url = agent_url.rstrip("/")
        self.business_url = business_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._token: str | None = None

    async def login(self, username: str, password: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.business_url}/api/auth/login",
                json={"username": username, "password": password},
            )
            response.raise_for_status()
            self._token = response.json()["accessToken"]

    async def ask(self, question_id: str, message: str) -> LiveAgentResponse:
        if not self._token:
            raise RuntimeError("Call login() before ask()")
        chunks: list[dict[str, Any]] = []
        error: str | None = None
        headers = {"Authorization": f"Bearer {self._token}"}
        payload = {
            "message": message,
            "conversation_id": f"evaluation-{question_id}",
            "request_id": f"evaluation-{question_id}",
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=20)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.agent_url}/api/vanna/v2/chat_sse",
                headers=headers,
                json=payload,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        break
                    parsed = json.loads(data)
                    if parsed.get("type") == "error":
                        error = parsed.get("data", {}).get("message", "unknown SSE error")
                    else:
                        chunks.append(parsed)
        return LiveAgentResponse(chunks=chunks, error=error)


def instruction_hash(message: str) -> str:
    return hashlib.sha256(message.encode("utf-8")).hexdigest()

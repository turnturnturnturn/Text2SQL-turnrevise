from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Sequence

import httpx

from app.state.memory_service import (
    MemoryService,
    normalize_memory_content,
    sanitize_memory_content,
)
from app.state.models import MemoryRecord, MemoryScope, MemoryStatus, MemoryType
from app.state.repositories import InMemoryStateRepository


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
        case
        for case in memory_cases
        if case.get("incorrect_memory_ids")
        or case.get("incorrect_memory_present") is True
    ]
    incorrect_exposure_cases = [
        case for case in memory_cases if case.get("incorrect_memory_ids")
    ]
    ineligible_memory_cases = [
        case for case in memory_cases if case.get("ineligible_memory_ids")
    ]
    adoption_cases = [
        case
        for case in incorrect_memory_cases
        if isinstance(case.get("adopted_memory_ids"), list)
        or isinstance(case.get("incorrect_memory_adopted"), bool)
    ]

    if any(not isinstance(run.get("correction_succeeded"), bool) for run in corrections):
        raise ValueError("correction_succeeded must be boolean for correction cases")
    if any(not isinstance(case.get("retrieved_memory_ids"), list) for case in memory_recall_cases):
        raise ValueError("retrieved_memory_ids must be an array for memory recall cases")
    list_fields = (
        "gold_memory_ids",
        "retrieved_memory_ids",
        "incorrect_memory_ids",
        "ineligible_memory_ids",
        "adopted_memory_ids",
    )
    for case in memory_cases:
        for field_name in list_fields:
            if field_name in case and not isinstance(case[field_name], list):
                raise ValueError(f"{field_name} must be an array")
    if any(
        "adopted_memory_ids" not in case
        and not isinstance(case.get("incorrect_memory_adopted"), bool)
        for case in adoption_cases
    ):
        raise ValueError("incorrect memory adoption telemetry is invalid")

    tool_counts = [run["tool_call_count"] for run in runs if run.get("tool_call_count") is not None]
    latencies = [run["latency_ms"] for run in runs if run.get("latency_ms") is not None]
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0
        for value in tool_counts + latencies
    ):
        raise ValueError("tool_call_count and latency_ms must be non-negative numbers")

    recalled = 0
    incorrect_exposures = 0
    ineligible_exposures = 0
    memory_details: list[dict[str, Any]] = []
    for case in memory_cases:
        gold = set(case.get("gold_memory_ids", []))
        incorrect = set(case.get("incorrect_memory_ids", []))
        ineligible = set(case.get("ineligible_memory_ids", []))
        retrieved = list(case.get("retrieved_memory_ids", []))[:5]
        hit = bool(gold.intersection(retrieved)) if gold else None
        incorrect_exposed = bool(incorrect.intersection(retrieved)) if incorrect else None
        ineligible_exposed = bool(ineligible.intersection(retrieved)) if ineligible else None
        if hit is True:
            recalled += 1
        if incorrect_exposed is True:
            incorrect_exposures += 1
        if ineligible_exposed is True:
            ineligible_exposures += 1
        memory_details.append(
            {
                "id": case.get("id"),
                "hit_at_5": hit,
                "gold_memory_ids": sorted(gold),
                "retrieved_memory_ids": retrieved,
                "incorrect_memory_ids": sorted(incorrect),
                "incorrect_memory_exposed": incorrect_exposed,
                "ineligible_memory_ids": sorted(ineligible),
                "ineligible_memory_exposed": ineligible_exposed,
            }
        )

    failed = [run for run in runs if run.get("status") in {"FAILED", "CANCELLED"}]
    failure_counts = Counter(
        str(run.get("failure_category") or ("CANCELLED" if run.get("status") == "CANCELLED" else "UNKNOWN"))
        for run in failed
    )
    correction_successes = sum(run.get("correction_succeeded") is True for run in corrections)
    incorrect_adoptions = 0
    for case in adoption_cases:
        if isinstance(case.get("adopted_memory_ids"), list):
            adopted = set(case["adopted_memory_ids"])
            incorrect = set(case.get("incorrect_memory_ids", []))
            incorrect_adoptions += bool(adopted.intersection(incorrect))
        else:
            incorrect_adoptions += case.get("incorrect_memory_adopted") is True

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
        "incorrect_memory_exposure_case_count": len(incorrect_exposure_cases),
        "incorrect_memory_exposure_count": incorrect_exposures,
        "incorrect_memory_exposure_rate": _rate(
            incorrect_exposures, len(incorrect_exposure_cases)
        ),
        "ineligible_memory_case_count": len(ineligible_memory_cases),
        "ineligible_memory_exposure_count": ineligible_exposures,
        "ineligible_memory_exposure_rate": _rate(
            ineligible_exposures, len(ineligible_memory_cases)
        ),
        "incorrect_memory_case_count": len(adoption_cases),
        "incorrect_memory_adoption_count": incorrect_adoptions,
        "incorrect_memory_adoption_rate": _rate(
            incorrect_adoptions, len(adoption_cases)
        ),
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
        f"- 错误记忆暴露率：{percent(metrics['incorrect_memory_exposure_rate'])}（{metrics['incorrect_memory_exposure_case_count']} 个注入样本）",
        f"- 不合格记忆暴露率：{percent(metrics['ineligible_memory_exposure_rate'])}（{metrics['ineligible_memory_case_count']} 个隔离样本）",
        f"- 错误记忆采用率：{percent(metrics['incorrect_memory_adoption_rate'])}（{metrics['incorrect_memory_case_count']} 个注入样本）",
        f"- 平均延迟：{'无可用样本' if average_latency is None else f'{average_latency:.2f} ms'}",
        f"- 失败分类：{failure_text}",
    ]


MemoryEmbedder = Callable[[str], Sequence[float]]


def _memory_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("memory fixture datetimes must include a timezone")
    return parsed


def validate_memory_suite(suite: dict[str, Any]) -> None:
    """Validate a deterministic memory-retrieval fixture before running it."""
    if not isinstance(suite.get("suite_version"), str):
        raise ValueError("memory suite requires suite_version")
    memories = suite.get("memories")
    cases = suite.get("cases")
    if not isinstance(memories, list) or not isinstance(cases, list):
        raise ValueError("memory suite requires memories and cases arrays")
    memory_ids = [item.get("id") for item in memories if isinstance(item, dict)]
    case_ids = [item.get("id") for item in cases if isinstance(item, dict)]
    if len(memory_ids) != len(memories) or any(not item for item in memory_ids):
        raise ValueError("every memory fixture requires a non-empty id")
    if len(case_ids) != len(cases) or any(not item for item in case_ids):
        raise ValueError("every memory case requires a non-empty id")
    if len(memory_ids) != len(set(memory_ids)):
        raise ValueError("memory fixture ids must be unique")
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("memory case ids must be unique")
    known = set(memory_ids)
    for case in cases:
        if not isinstance(case.get("user_id"), str) or not isinstance(case.get("query"), str):
            raise ValueError("every memory case requires user_id and query")
        gold = case.get("gold_memory_ids")
        if not isinstance(gold, list) or not gold:
            raise ValueError("every memory case requires gold_memory_ids")
        for field_name in ("gold_memory_ids", "incorrect_memory_ids", "ineligible_memory_ids"):
            values = case.get(field_name, [])
            if not isinstance(values, list):
                raise ValueError(f"{field_name} must be an array")
            unknown = set(values).difference(known)
            if unknown:
                raise ValueError(f"{field_name} contains unknown ids: {sorted(unknown)}")


async def evaluate_memory_suite(
    suite: dict[str, Any],
    *,
    embedder: MemoryEmbedder | None = None,
) -> dict[str, Any]:
    """Run gold memory cases through the production MemoryService retrieval path."""
    validate_memory_suite(suite)
    repository = InMemoryStateRepository()
    service = MemoryService(repository, embedder=embedder)

    for fixture in suite["memories"]:
        clean = sanitize_memory_content(fixture["content"])
        if not clean:
            raise ValueError(f"memory fixture {fixture['id']} is empty after sanitization")
        record = MemoryRecord(
            id=fixture["id"],
            user_id=fixture["user_id"],
            memory_type=MemoryType(fixture.get("memory_type", "USER_PREFERENCE")),
            status=MemoryStatus(fixture.get("status", "confirmed")),
            content=clean,
            normalized_content=normalize_memory_content(clean),
            source="evaluation_fixture",
            scope=MemoryScope(fixture.get("scope", "USER")),
            embedding=list(map(float, embedder(clean))) if embedder else None,
            metadata={"fixture": True},
            expires_at=_memory_datetime(fixture.get("expires_at")),
        )
        await repository.upsert_memory(record)

    case_results: list[dict[str, Any]] = []
    for case in suite["cases"]:
        matches = await service.search_confirmed(
            case["user_id"],
            case["query"],
            limit=5,
            similarity_threshold=float(case.get("similarity_threshold", 0.05)),
        )
        case_results.append(
            {
                "id": case["id"],
                "gold_memory_ids": case["gold_memory_ids"],
                "retrieved_memory_ids": [memory.id for _, memory in matches],
                "incorrect_memory_ids": case.get("incorrect_memory_ids", []),
                "ineligible_memory_ids": case.get("ineligible_memory_ids", []),
            }
        )

    metrics = aggregate_harness_metrics([], case_results)
    return {
        "suite_version": suite["suite_version"],
        "case_count": len(case_results),
        "metrics": metrics,
        "cases": metrics["memory_details"],
    }


def memory_suite_markdown(result: dict[str, Any]) -> str:
    metrics = result["metrics"]
    return "\n".join(
        [
            "# Memory 专项评测报告",
            "",
            f"- 题集版本：{result['suite_version']}",
            f"- 样本数：{result['case_count']}",
            "- 说明：题集通过生产 MemoryService 检索路径运行；采用率只有真实轨迹提供 adopted memory ids 时才计算。",
            "",
            "## 指标",
            "",
            *harness_metrics_markdown(metrics)[3:7],
            "",
            "## 逐题结果",
            "",
            "| ID | Hit@5 | 错误暴露 | 不合格暴露 | Top-5 |",
            "| --- | ---: | ---: | ---: | --- |",
            *[
                "| {id} | {hit} | {wrong} | {ineligible} | {retrieved} |".format(
                    id=case["id"],
                    hit="是" if case["hit_at_5"] else "否",
                    wrong="是" if case["incorrect_memory_exposed"] else "否",
                    ineligible="是" if case["ineligible_memory_exposed"] else "否",
                    retrieved=", ".join(case["retrieved_memory_ids"]),
                )
                for case in result["cases"]
            ],
            "",
        ]
    )


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

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx


NUMERIC_TOLERANCE = Decimal("0.000001")


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

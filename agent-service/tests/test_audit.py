from datetime import datetime, timezone

import pytest
from vanna.core.audit import AuditEvent, AuditEventType, ToolInvocationEvent

from app.audit import (
    BusinessAuditLogger,
    reset_instruction_hash,
    set_instruction_hash,
)


class FakeBusinessClient:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.events = []

    async def record_audit_event(self, payload):
        if self.error:
            raise self.error
        self.events.append(payload)


def sql_event() -> ToolInvocationEvent:
    return ToolInvocationEvent(
        event_type=AuditEventType.TOOL_INVOCATION,
        timestamp=datetime.now(timezone.utc),
        user_id="00000000-0000-0000-0000-000000000001",
        username="analyst",
        user_email="analyst@example.com",
        user_groups=["analyst"],
        conversation_id="conversation-1",
        request_id="request-1",
        tool_call_id="call-1",
        tool_name="safe_read_sql",
        parameters={"sql": "SELECT id FROM orders LIMIT 10"},
    )


@pytest.mark.asyncio
async def test_query_audit_extracts_sql_and_omits_email():
    client = FakeBusinessClient()
    await BusinessAuditLogger(client).log_event(sql_event())

    payload = client.events[0]
    assert payload["generatedSql"] == "SELECT id FROM orders LIMIT 10"
    assert payload["userId"] == "00000000-0000-0000-0000-000000000001"
    assert "user_email" not in payload["details"]


@pytest.mark.asyncio
async def test_audit_is_fail_closed_by_default():
    client = FakeBusinessClient(RuntimeError("audit unavailable"))
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await BusinessAuditLogger(client).log_event(sql_event())


@pytest.mark.asyncio
async def test_instruction_audit_persists_hash_without_raw_message():
    client = FakeBusinessClient()
    event = AuditEvent(
        event_type=AuditEventType.MESSAGE_RECEIVED,
        user_id="00000000-0000-0000-0000-000000000001",
        conversation_id="conversation-1",
        request_id="request-1",
        details={"instruction_hash": "a" * 64, "message_length": 12},
    )
    await BusinessAuditLogger(client).log_event(event)

    assert client.events[0]["originalInstructionHash"] == "a" * 64
    assert "message" not in client.events[0]["details"]


@pytest.mark.asyncio
async def test_tool_audit_inherits_request_instruction_hash():
    client = FakeBusinessClient()
    token = set_instruction_hash("b" * 64)
    try:
        await BusinessAuditLogger(client).log_event(sql_event())
    finally:
        reset_instruction_hash(token)

    assert client.events[0]["originalInstructionHash"] == "b" * 64

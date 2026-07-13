from __future__ import annotations

import logging
import hashlib
import uuid
from contextvars import ContextVar, Token
from typing import AsyncGenerator
from typing import Any

from vanna import Agent
from vanna.components import UiComponent
from vanna.core.audit import AuditEvent, AuditLogger
from vanna.core.audit import AuditEventType
from vanna.core.user import RequestContext

from app.business_client import BusinessServiceClient


logger = logging.getLogger(__name__)
_instruction_hash: ContextVar[str | None] = ContextVar(
    "instruction_hash", default=None
)
_instruction_text: ContextVar[str | None] = ContextVar(
    "instruction_text", default=None
)


def set_instruction_hash(value: str) -> Token[str | None]:
    """Bind an instruction hash to the current asynchronous request context."""
    return _instruction_hash.set(value)


def reset_instruction_hash(token: Token[str | None]) -> None:
    _instruction_hash.reset(token)


def get_current_instruction() -> str | None:
    """Return request-local text without persisting it in audit payloads."""
    return _instruction_text.get()


class BusinessAuditLogger(AuditLogger):
    """Persist Vanna audit events through the write-side business service."""

    def __init__(self, client: BusinessServiceClient, *, fail_closed: bool = True):
        self.client = client
        self.fail_closed = fail_closed

    async def log_event(self, event: AuditEvent) -> None:
        event_data = event.model_dump(mode="json", exclude_none=True)
        # Email is not needed for traceability; user_id is the stable subject.
        event_data.pop("user_email", None)

        generated_sql = None
        parameters = event_data.get("parameters")
        if event_data.get("tool_name") == "safe_read_sql" and isinstance(
            parameters, dict
        ):
            sql = parameters.get("sql")
            if isinstance(sql, str):
                generated_sql = sql

        details = event_data.get("details", {})
        event_instruction_hash = (
            details.get("instruction_hash") if isinstance(details, dict) else None
        ) or _instruction_hash.get()

        payload: dict[str, Any] = {
            "eventType": str(event.event_type.value),
            "userId": event.user_id,
            "requestId": event.request_id,
            "generatedSql": generated_sql,
            "originalInstructionHash": event_instruction_hash,
            "success": bool(event_data.get("success", True)),
            "durationMs": round(float(event_data.get("execution_time_ms", 0))),
            "details": event_data,
        }
        try:
            await self.client.record_audit_event(payload)
        except Exception:
            logger.exception("Unable to persist agent audit event")
            if self.fail_closed:
                raise


class AuditedAgent(Agent):
    """Add the user-instruction audit event missing from Vanna 2.0.2."""

    async def _send_message(
        self,
        request_context: RequestContext,
        message: str,
        *,
        conversation_id: str | None = None,
    ) -> AsyncGenerator[UiComponent, None]:
        user = await self.user_resolver.resolve_user(request_context)
        instruction_hash = hashlib.sha256(message.encode("utf-8")).hexdigest()
        token = set_instruction_hash(instruction_hash)
        text_token = _instruction_text.set(message)
        try:
            await self.audit_logger.log_event(
                AuditEvent(
                    event_type=AuditEventType.MESSAGE_RECEIVED,
                    user_id=user.id,
                    username=user.username,
                    user_groups=user.group_memberships,
                    conversation_id=conversation_id or "",
                    request_id=str(uuid.uuid4()),
                    remote_addr=request_context.remote_addr,
                    details={
                        "instruction_hash": instruction_hash,
                        "message_length": len(message),
                    },
                )
            )
            async for component in super()._send_message(
                request_context, message, conversation_id=conversation_id
            ):
                yield component
        finally:
            _instruction_text.reset(text_token)
            reset_instruction_hash(token)

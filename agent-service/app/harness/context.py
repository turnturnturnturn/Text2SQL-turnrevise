from __future__ import annotations

import hashlib
from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.grounding.context import bind_grounding_context, reset_grounding_context
from app.grounding.context import RequestGroundingState


_run_id: ContextVar[str | None] = ContextVar("harness_run_id", default=None)
_instruction_hash: ContextVar[str | None] = ContextVar(
    "harness_instruction_hash", default=None
)
_instruction_text: ContextVar[str | None] = ContextVar(
    "harness_instruction_text", default=None
)


@dataclass(frozen=True)
class RequestContextTokens:
    run_id: Token[str | None]
    instruction_hash: Token[str | None]
    instruction_text: Token[str | None]
    grounding_state: Token[RequestGroundingState | None]


def bind_request_context(run_id: str, instruction: str) -> RequestContextTokens:
    """Bind correlation data to this task and any child tasks it creates."""
    return RequestContextTokens(
        run_id=_run_id.set(run_id),
        instruction_hash=_instruction_hash.set(
            hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        ),
        instruction_text=_instruction_text.set(instruction),
        grounding_state=bind_grounding_context(),
    )


def reset_request_context(tokens: RequestContextTokens) -> None:
    # Context variables must be reset in reverse binding order.
    reset_grounding_context(tokens.grounding_state)
    _instruction_text.reset(tokens.instruction_text)
    _instruction_hash.reset(tokens.instruction_hash)
    _run_id.reset(tokens.run_id)


def get_run_id() -> str | None:
    return _run_id.get()


def get_instruction_hash() -> str | None:
    return _instruction_hash.get()


def get_instruction_text() -> str | None:
    return _instruction_text.get()


def set_instruction_hash(value: str) -> Token[str | None]:
    """Compatibility hook for callers that only bind an instruction hash."""
    return _instruction_hash.set(value)


def reset_instruction_hash(token: Token[str | None]) -> None:
    _instruction_hash.reset(token)

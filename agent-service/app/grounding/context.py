from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass

from app.grounding.linker import GroundingBundle
from app.grounding.query_plan import ValidatedQueryPlan


@dataclass
class RequestGroundingState:
    bundle: GroundingBundle | None = None
    validated_plan: ValidatedQueryPlan | None = None


_grounding_state: ContextVar[RequestGroundingState | None] = ContextVar(
    "request_grounding_state", default=None
)


def bind_grounding_context() -> Token[RequestGroundingState | None]:
    return _grounding_state.set(RequestGroundingState())


def reset_grounding_context(token: Token[RequestGroundingState | None]) -> None:
    _grounding_state.reset(token)


def get_grounding_state() -> RequestGroundingState | None:
    return _grounding_state.get()


def record_grounding(bundle: GroundingBundle) -> None:
    state = _grounding_state.get()
    if state is None:
        state = RequestGroundingState()
        _grounding_state.set(state)
    state.bundle = bundle
    state.validated_plan = None


def record_validated_plan(plan: ValidatedQueryPlan | None) -> None:
    state = _grounding_state.get()
    if state is None:
        state = RequestGroundingState()
        _grounding_state.set(state)
    state.validated_plan = plan

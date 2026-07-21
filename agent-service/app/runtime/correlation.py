from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Iterator


_run_id: ContextVar[str | None] = ContextVar("phase_d_run_id", default=None)
_correlation_id: ContextVar[str | None] = ContextVar(
    "phase_d_correlation_id", default=None
)


@contextmanager
def bind_run_identity(run_id: str, correlation_id: str) -> Iterator[None]:
    run_token = _run_id.set(run_id)
    correlation_token = _correlation_id.set(correlation_id)
    try:
        yield
    finally:
        _correlation_id.reset(correlation_token)
        _run_id.reset(run_token)


def current_run_id() -> str | None:
    return _run_id.get()


def current_correlation_id() -> str | None:
    return _correlation_id.get()

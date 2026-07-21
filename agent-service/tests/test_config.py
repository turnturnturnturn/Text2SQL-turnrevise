from __future__ import annotations

import importlib

import pytest


def test_context_harness_v2_defaults_to_shadow(monkeypatch):
    monkeypatch.delenv("CONTEXT_HARNESS_V2_MODE", raising=False)
    import app.config as config

    reloaded = importlib.reload(config)
    assert reloaded.settings.context_harness_v2_mode == "shadow"


def test_invalid_context_harness_v2_mode_fails_fast(monkeypatch):
    monkeypatch.setenv("CONTEXT_HARNESS_V2_MODE", "unsafe")
    import app.config as config

    with pytest.raises(ValueError, match="CONTEXT_HARNESS_V2_MODE"):
        importlib.reload(config)
    monkeypatch.setenv("CONTEXT_HARNESS_V2_MODE", "shadow")
    importlib.reload(config)


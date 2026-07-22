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


def test_phase_d_modes_and_retention_defaults(monkeypatch):
    for name in (
        "EVIDENCE_DRAWER_MODE",
        "OTEL_MODE",
        "CLARIFICATION_RESUME_MODE",
        "ROLLOUT_POLICY_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    import app.config as config

    reloaded = importlib.reload(config)
    assert reloaded.settings.evidence_drawer_mode == "shadow"
    assert reloaded.settings.otel_mode == "shadow"
    assert reloaded.settings.clarification_resume_mode == "shadow"
    assert reloaded.settings.rollout_policy_mode == "shadow"
    assert reloaded.settings.trace_success_sample_rate == 0.05
    assert reloaded.settings.trace_retention_days == 30
    assert reloaded.settings.metric_retention_days == 90

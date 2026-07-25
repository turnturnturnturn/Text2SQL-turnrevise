import importlib

from app import config as config_module


def test_settings_expose_only_external_llm_configuration(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "vendor/model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://llm.example.test/v1")

    settings = importlib.reload(config_module).Settings()

    assert settings.openai_model == "vendor/model"
    assert settings.openai_base_url == "https://llm.example.test/v1"

from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(name: str, default: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://copilot_readonly:copilot_readonly_dev@localhost:5432/enterprise_copilot",
    )
    agent_state_database_url: str = os.getenv(
        "AGENT_STATE_DATABASE_URL",
        "postgresql://copilot_agent_state:copilot_agent_state_dev@localhost:5432/enterprise_copilot",
    )
    jwt_secret: str = os.getenv("JWT_SECRET", "dev-only-secret-change-before-use-123456")
    internal_service_token: str = os.getenv("INTERNAL_SERVICE_TOKEN", "dev-internal-token")
    business_service_url: str = os.getenv("BUSINESS_SERVICE_URL", "http://localhost:8080")
    public_business_service_url: str = os.getenv(
        "PUBLIC_BUSINESS_SERVICE_URL", "http://localhost:8080"
    )
    llm_provider: str = os.getenv("LLM_PROVIDER", "openai").lower()
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5")
    openai_base_url: str | None = os.getenv("OPENAI_BASE_URL") or None
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:4b")
    allowed_origins: tuple[str, ...] = _csv(
        "AGENT_ALLOWED_ORIGINS", "http://localhost:8000,http://localhost:8080"
    )
    max_query_rows: int = int(os.getenv("MAX_QUERY_ROWS", "200"))
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "5000"))
    audit_fail_closed: bool = _bool("AUDIT_FAIL_CLOSED", True)
    retrieval_mode: str = os.getenv("RETRIEVAL_MODE", "hybrid").lower()
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-small-zh-v1.5")
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "8"))
    harness_max_tool_calls: int = int(os.getenv("HARNESS_MAX_TOOL_CALLS", "8"))
    harness_max_retries: int = int(os.getenv("HARNESS_MAX_RETRIES", "2"))
    harness_timeout_seconds: int = int(os.getenv("HARNESS_TIMEOUT_SECONDS", "120"))
    memory_top_k: int = int(os.getenv("MEMORY_TOP_K", "5"))
    memory_max_per_user: int = int(os.getenv("MEMORY_MAX_PER_USER", "500"))
    memory_retention_days: int = int(os.getenv("MEMORY_RETENTION_DAYS", "90"))


settings = Settings()

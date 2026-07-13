from __future__ import annotations

from vanna import Agent, AgentConfig
from vanna.core.registry import ToolRegistry
from vanna.integrations.local import MemoryConversationStore
from vanna.servers.fastapi import VannaFastAPIServer
from fastapi.responses import HTMLResponse

from app.business_client import BusinessServiceClient
from app.audit import AuditedAgent, BusinessAuditLogger
from app.config import settings
from app.db import SafePostgresRunner
from app.memory import InMemoryAgentMemory
from app.prompt import CommerceSystemPromptBuilder
from app.security.jwt_resolver import JwtUserResolver
from app.tools import PreviewBusinessActionTool, SafeReadSqlTool, SearchSchemaKnowledgeTool
from app.workflow import ActionWorkflowHandler
from app.ui import login_shell


def create_llm():
    if settings.llm_provider == "ollama":
        from vanna.integrations.ollama import OllamaLlmService

        return OllamaLlmService(
            model=settings.ollama_model,
            host=settings.ollama_host,
            temperature=0.1,
        )
    if settings.llm_provider == "openai":
        from vanna.integrations.openai import OpenAILlmService

        return OpenAILlmService(
            model=settings.openai_model,
            base_url=settings.openai_base_url,
        )
    raise ValueError("LLM_PROVIDER must be 'openai' or 'ollama'")


def create_agent() -> Agent:
    memory = InMemoryAgentMemory()
    business_client = BusinessServiceClient(
        settings.business_service_url, settings.internal_service_token
    )
    registry = ToolRegistry()
    registry.register_local_tool(
        SearchSchemaKnowledgeTool(
            settings.database_url,
            retrieval_mode=settings.retrieval_mode,
            embedding_model=settings.embedding_model,
            candidate_limit=max(settings.retrieval_top_k, 8),
        ),
        access_groups=["analyst", "operator", "admin"],
    )
    registry.register_local_tool(
        SafeReadSqlTool(
            SafePostgresRunner(settings.database_url, settings.statement_timeout_ms),
            max_rows=settings.max_query_rows,
        ),
        access_groups=["analyst", "operator", "admin"],
    )
    registry.register_local_tool(
        PreviewBusinessActionTool(business_client),
        access_groups=["operator", "admin"],
    )

    return AuditedAgent(
        llm_service=create_llm(),
        tool_registry=registry,
        user_resolver=JwtUserResolver(settings.jwt_secret),
        agent_memory=memory,
        conversation_store=MemoryConversationStore(),
        config=AgentConfig(
            stream_responses=True,
            temperature=0.1,
            max_tool_iterations=8,
        ),
        system_prompt_builder=CommerceSystemPromptBuilder(),
        workflow_handler=ActionWorkflowHandler(business_client),
        audit_logger=BusinessAuditLogger(
            business_client, fail_closed=settings.audit_fail_closed
        ),
    )


agent = create_agent()
server = VannaFastAPIServer(
    agent,
    config={
        "cors": {
            "enabled": True,
            "allow_origins": list(settings.allowed_origins),
            "allow_credentials": True,
            "allow_methods": ["GET", "POST", "OPTIONS"],
            "allow_headers": ["Authorization", "Content-Type"],
        },
        "api_base_url": "",
    },
)
app = server.create_app()


@app.get("/app", response_class=HTMLResponse)
async def copilot_app() -> str:
    return login_shell(settings.public_business_service_url)

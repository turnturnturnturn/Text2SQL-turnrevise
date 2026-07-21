from __future__ import annotations

from vanna import Agent, AgentConfig
from vanna.core.registry import ToolRegistry
from vanna.servers.fastapi import VannaFastAPIServer
from fastapi.responses import HTMLResponse

from app.business_client import BusinessServiceClient
from app.audit import AuditedAgent, BusinessAuditLogger
from app.config import settings
from app.db import SafePostgresRunner
from app.harness import HarnessBudget, HarnessLifecycleHook, RequestHarness
from app.harness.clarification import ClarificationService, PostgresClarificationStore
from app.context_v2 import ContextCompiler, PostgresContextStore
from app.prompt import CommerceSystemPromptBuilder
from app.retrieval import LazySentenceEmbedder
from app.grounding import (
    BidirectionalGroundingLinker,
    GroundingService,
    PostgresQueryPlanStore,
    PostgresSemanticCatalogStore,
    QueryPlanValidator,
)
from app.security.jwt_resolver import JwtUserResolver
from app.state import (
    MemoryContextEnhancer,
    ContextV2Enhancer,
    MemoryService,
    PostgresAgentMemory,
    PostgresConversationStore,
    PostgresRunStore,
    PostgresStateRepository,
    RecentConversationFilter,
)
from app.state.api import create_state_router
from app.tools import (
    PreviewBusinessActionTool,
    SafeReadSqlTool,
    SearchSchemaKnowledgeTool,
    ValidateQueryPlanTool,
)
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


state_repository = PostgresStateRepository(settings.agent_state_database_url)
shared_embedder = LazySentenceEmbedder(settings.embedding_model)
semantic_catalog_store = PostgresSemanticCatalogStore(settings.database_url)
grounding_service = GroundingService(
    semantic_catalog_store,
    BidirectionalGroundingLinker(embedder=shared_embedder),
)
query_plan_store = PostgresQueryPlanStore(settings.agent_state_database_url)
memory_service = MemoryService(
    state_repository,
    embedder=lambda text: shared_embedder.encode(
        [text], normalize_embeddings=True
    )[0],
    max_active_per_user=settings.memory_max_per_user,
    retention_days=settings.memory_retention_days,
)
conversation_store = PostgresConversationStore(state_repository)
run_store = PostgresRunStore(
    settings.agent_state_database_url,
    model_name=(
        settings.openai_model
        if settings.llm_provider == "openai"
        else settings.ollama_model
    ),
    retrieval_mode=settings.retrieval_mode,
)
context_store = PostgresContextStore(settings.agent_state_database_url)
clarification_store = PostgresClarificationStore(settings.agent_state_database_url)
clarification_service = ClarificationService(
    run_store,
    clarification_store,
    ttl_seconds=settings.clarification_ttl_seconds,
)
harness_budget = HarnessBudget(
    timeout_seconds=settings.harness_timeout_seconds,
    max_tool_calls=settings.harness_max_tool_calls,
    max_read_retries=settings.harness_max_retries,
    max_context_tokens=settings.context_token_budget,
)
request_harness = RequestHarness(
    run_store,
    budget=harness_budget,
    mode=settings.context_harness_v2_mode,
)
user_resolver = JwtUserResolver(settings.jwt_secret)


def create_agent() -> Agent:
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
            embedder_loader=lambda _model_name: shared_embedder,
            grounding_service=grounding_service,
            grounding_mode=settings.grounding_v2_mode,
        ),
        access_groups=["analyst", "operator", "admin"],
    )
    if settings.grounding_v2_mode != "off":
        registry.register_local_tool(
            ValidateQueryPlanTool(
                QueryPlanValidator(),
                store=query_plan_store,
                grounding_mode=settings.grounding_v2_mode,
            ),
            access_groups=["analyst", "operator", "admin"],
        )
    registry.register_local_tool(
        SafeReadSqlTool(
            SafePostgresRunner(settings.database_url, settings.statement_timeout_ms),
            max_rows=settings.max_query_rows,
            grounding_mode=settings.grounding_v2_mode,
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
        user_resolver=user_resolver,
        agent_memory=PostgresAgentMemory(memory_service),
        conversation_store=conversation_store,
        config=AgentConfig(
            stream_responses=True,
            temperature=0.1,
            max_tool_iterations=settings.harness_max_tool_calls,
        ),
        system_prompt_builder=CommerceSystemPromptBuilder(settings.grounding_v2_mode),
        lifecycle_hooks=[HarnessLifecycleHook(
            run_store,
            harness_budget,
            mode=settings.context_harness_v2_mode,
        )],
        llm_context_enhancer=ContextV2Enhancer(
            memory_service,
            ContextCompiler(),
            context_store,
            mode=settings.context_harness_v2_mode,
            top_k=settings.memory_top_k,
            total_token_budget=settings.context_token_budget,
        ),
        conversation_filters=[RecentConversationFilter()],
        workflow_handler=ActionWorkflowHandler(business_client, memory_service),
        audit_logger=BusinessAuditLogger(
            business_client, fail_closed=settings.audit_fail_closed
        ),
        request_harness=request_harness,
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
app.include_router(
    create_state_router(
        resolver=user_resolver,
        memory_service=memory_service,
        conversation_store=conversation_store,
        run_store=run_store,
        query_plan_store=query_plan_store,
        clarification_service=clarification_service,
        context_store=context_store,
    )
)


@app.on_event("startup")
async def recover_interrupted_harness_runs() -> None:
    await request_harness.fail_incomplete_runs()
    await state_repository.delete_expired_conversations(
        settings.conversation_retention_days
    )


@app.get("/app", response_class=HTMLResponse)
async def copilot_app() -> str:
    return login_shell(settings.public_business_service_url)

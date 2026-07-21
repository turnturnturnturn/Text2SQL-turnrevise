from __future__ import annotations

import asyncio
import logging

from vanna import Agent, AgentConfig
from vanna.core.registry import ToolRegistry
from vanna.servers.fastapi import VannaFastAPIServer
from fastapi.responses import HTMLResponse, PlainTextResponse
from vanna.core.user import RequestContext

from app.business_client import BusinessServiceClient
from app.audit import AuditedAgent, BusinessAuditLogger
from app.config import settings
from app.db import SafePostgresRunner
from app.harness import HarnessBudget, HarnessLifecycleHook, RequestHarness
from app.harness.clarification import ClarificationService, PostgresClarificationStore
from app.context_v2 import (
    ContextCompiler,
    ConversationStateCompactor,
    PostgresContextStore,
)
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
    ContextV2Enhancer,
    ContextV2ConversationFilter,
    MemoryService,
    PostgresAgentMemory,
    PostgresConversationStore,
    PostgresRunStore,
    PostgresStateRepository,
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
from app.runtime import CorrelatedChatHandler
from app.evidence import EvidenceService
from app.observability import MetricsRegistry, PostgresTraceStore, TraceService
from app.observability.otel import OtelBridge
from app.observability.vanna_provider import RedactedObservabilityProvider
from app.rollout import PostgresRolloutStore, RolloutPolicyService
from app.rollout.monitor import RolloutMonitor


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
    embedder=lambda text: shared_embedder.encode([text], normalize_embeddings=True)[0],
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
    v2_enabled=settings.context_harness_v2_mode != "off",
)
context_store = PostgresContextStore(settings.agent_state_database_url)
clarification_store = PostgresClarificationStore(settings.agent_state_database_url)
clarification_service = ClarificationService(
    run_store,
    clarification_store,
    ttl_seconds=settings.clarification_ttl_seconds,
)
metrics_registry = MetricsRegistry()
trace_service = TraceService(
    PostgresTraceStore(settings.agent_state_database_url),
    mode=settings.otel_mode,
    success_sample_rate=settings.trace_success_sample_rate,
    metrics=metrics_registry,
)
observability_provider = RedactedObservabilityProvider(
    trace_service,
    metrics_registry,
    OtelBridge(settings.otel_exporter_otlp_endpoint),
)
rollout_store = PostgresRolloutStore(settings.agent_state_database_url)
rollout_service = RolloutPolicyService(rollout_store)
rollout_monitor = RolloutMonitor(rollout_store, rollout_service)
rollout_monitor_task = None
logger = logging.getLogger(__name__)
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
    trace_service=trace_service,
    rollout_service=rollout_service,
)
user_resolver = JwtUserResolver(settings.jwt_secret)
business_client = BusinessServiceClient(
    settings.business_service_url, settings.internal_service_token
)
evidence_service = EvidenceService(
    query_plan_store,
    context_store,
    business_client,
    mode=settings.evidence_drawer_mode,
)


def create_agent() -> Agent:
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
            context_harness_mode=settings.context_harness_v2_mode,
            clarification_service=clarification_service,
            trace_service=trace_service,
        ),
        access_groups=["analyst", "operator", "admin"],
    )
    if settings.grounding_v2_mode != "off":
        registry.register_local_tool(
            ValidateQueryPlanTool(
                QueryPlanValidator(),
                store=query_plan_store,
                grounding_mode=settings.grounding_v2_mode,
                trace_service=trace_service,
            ),
            access_groups=["analyst", "operator", "admin"],
        )
    registry.register_local_tool(
        SafeReadSqlTool(
            SafePostgresRunner(settings.database_url, settings.statement_timeout_ms),
            max_rows=settings.max_query_rows,
            grounding_mode=settings.grounding_v2_mode,
            run_store=run_store,
            trace_service=trace_service,
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
        lifecycle_hooks=[
            HarnessLifecycleHook(
                run_store,
                harness_budget,
                mode=settings.context_harness_v2_mode,
            )
        ],
        llm_context_enhancer=ContextV2Enhancer(
            memory_service,
            ContextCompiler(),
            context_store,
            mode=settings.context_harness_v2_mode,
            top_k=settings.memory_top_k,
            total_token_budget=settings.context_token_budget,
            run_store=run_store,
        ),
        conversation_filters=[
            ContextV2ConversationFilter(
                ConversationStateCompactor(),
                context_store,
                mode=settings.context_harness_v2_mode,
            )
        ],
        workflow_handler=ActionWorkflowHandler(business_client, memory_service),
        audit_logger=BusinessAuditLogger(
            business_client, fail_closed=settings.audit_fail_closed
        ),
        request_harness=request_harness,
        evidence_drawer_mode=settings.evidence_drawer_mode,
        observability_provider=observability_provider,
    )


agent = create_agent()


async def resume_handler(child, instruction, selection, authorization):
    request_context = RequestContext(
        headers={"Authorization": authorization or ""},
        cookies={},
        query_params={},
        metadata={"resume": True},
    )
    async for component in agent.resume_message(
        request_context, instruction, child, selection
    ):
        yield component


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
server.chat_handler = CorrelatedChatHandler(
    agent, rollout_service, settings.rollout_policy_mode
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
        evidence_service=evidence_service,
        clarification_resume_mode=settings.clarification_resume_mode,
        resume_handler=resume_handler,
        trace_service=trace_service,
        rollout_service=rollout_service,
        business_client=business_client,
    )
)


@app.on_event("startup")
async def recover_interrupted_harness_runs() -> None:
    global rollout_monitor_task
    await request_harness.fail_incomplete_runs()
    await state_repository.delete_expired_conversations(
        settings.conversation_retention_days
    )
    if settings.rollout_policy_mode != "off":
        rollout_monitor_task = asyncio.create_task(_rollout_monitor_loop())


async def _rollout_monitor_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await rollout_monitor.evaluate_once(await rollout_store.load_signals())
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("phase d rollout monitor iteration failed")


@app.on_event("shutdown")
async def stop_rollout_monitor() -> None:
    if rollout_monitor_task is not None:
        rollout_monitor_task.cancel()


@app.get("/app", response_class=HTMLResponse)
async def copilot_app() -> str:
    return login_shell(settings.public_business_service_url)


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> str:
    return metrics_registry.render()

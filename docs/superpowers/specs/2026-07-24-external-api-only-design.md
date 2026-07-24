# External API-only LLM Design

## Goal

Convert Enterprise Database Copilot to an external-LLM-only framework. Runtime
inference uses an OpenAI-compatible HTTP API configured through
`OPENAI_API_KEY`, `OPENAI_MODEL`, and optional `OPENAI_BASE_URL`.

## Scope

- Remove local generative-model provider branches, settings, dependencies,
  Compose variables, examples, tests, scripts, and documentation.
- Replace model-specific evaluation labels with provider-neutral `live-agent`
  language without changing historical measurements or claiming new results.
- Delete this workstation's local generative-model runtime assets after
  resolving exact paths.
- Preserve embedding-model support: `sentence-transformers` and `HF_HOME` are
  retrieval dependencies, not local generative-model support.
- Preserve SQL guards, approvals, Grounding, QueryPlan, Harness, memory,
  observability, authentication, and business-service boundaries.

## Runtime Contract

`create_llm()` always constructs Vanna's `OpenAILlmService`. The model name is
always `OPENAI_MODEL`; `OPENAI_BASE_URL` may point to any authorized
OpenAI-compatible external provider. An absent or invalid API credential fails
at provider request time and never falls back to a local model.

## Verification

- A tracked-source scan finds no active local generative-model runtime path.
- Configuration and provider tests prove the external API-only contract.
- Python, Java, repository preflight, and Compose configuration checks pass.
- `.env` and credentials remain untracked and are not printed or committed.

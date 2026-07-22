# Local Qwen3 MLX Phase D Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert the existing local Qwen3-4B ModelScope checkpoint into a separate MLX 4-bit model, expose it through a loopback OpenAI-compatible API, and run the Phase D live-model gate without altering source weights or committing secrets.

**Architecture:** `mlx-lm` runs in a dedicated local virtual environment. Conversion reads `/Users/turn/.cache/modelscope/models/Qwen--Qwen3-4B/snapshots/master` and writes a sibling MLX output directory, leaving the checkpoint immutable. The Agent service uses its existing OpenAI-compatible provider against the MLX loopback server; Phase D config remains in the ignored `.env` file.

**Tech Stack:** Apple Silicon macOS, Python 3.12, mlx-lm, Qwen3-4B, FastAPI Agent service, Docker Compose, pytest.

## Global Constraints

- Never modify or delete original ModelScope safetensors files.
- Keep the MLX server bound to loopback; do not expose it to the LAN.
- Do not store keys, connection strings, results, or model artifacts in Git.
- Preserve Phase D read-only SQL, JWT, SSE, rollout, and trace constraints.
- Keep `live_model` absent until a complete live E2E run succeeds.
- Do not merge `feat/phase-d-experience-observability` automatically.

---

### Task 1: Prepare and validate a local MLX runtime

**Files:**
- Create: ignored local virtual environment at `agent-service/.venv-mlx`
- Read: `/Users/turn/.cache/modelscope/models/Qwen--Qwen3-4B/snapshots/master/config.json`

**Interfaces:**
- Produces: `agent-service/.venv-mlx/bin/mlx_lm` capable of converting the local Qwen3 checkpoint.

- [ ] Install `mlx-lm` in the dedicated local environment, using the current Python runtime and no project dependency change.
- [ ] Run `mlx_lm.convert --help` and `mlx_lm.server --help` to record the installed CLI options before conversion.
- [ ] Read the model configuration and confirm the source directory contains a complete Qwen3 safetensors checkpoint.

### Task 2: Convert Qwen3-4B to isolated MLX 4-bit artifacts

**Files:**
- Create: `/Users/turn/.cache/modelscope/models/Qwen--Qwen3-4B-MLX-4bit`
- Read: `/Users/turn/.cache/modelscope/models/Qwen--Qwen3-4B/snapshots/master`

**Interfaces:**
- Produces: an MLX-ready local model directory with config, tokenizer, and quantized weights.

- [ ] Run the installed `mlx_lm.convert` command against the exact ModelScope snapshot path with 4-bit quantization and an explicit output path.
- [ ] Verify that the source checkpoint timestamps and file count are unchanged.
- [ ] Verify the converted directory can be loaded with a one-token local generation smoke test.

### Task 3: Serve local Qwen and connect Enterprise DB Copilot

**Files:**
- Modify: ignored `.env` in the Phase D worktree only if it does not overwrite existing user settings.
- Read: `agent-service/app/main.py`
- Read: `agent-service/app/config.py`

**Interfaces:**
- Produces: MLX server at `http://127.0.0.1:8081/v1` and Agent configuration using `OPENAI_BASE_URL=http://host.docker.internal:8081/v1` when the Agent runs in Docker.

- [ ] Start `mlx_lm.server` on loopback port 8081 in a managed local session.
- [ ] Call `/v1/models` and `/v1/chat/completions` with a harmless prompt; require HTTP 200.
- [ ] Add only non-secret local-provider entries to the ignored `.env`, preserving existing configuration values.
- [ ] Restart the isolated QA Agent service and verify it reaches the MLX `/v1/models` endpoint from its container network.

### Task 4: Run Phase D live-model acceptance gate

**Files:**
- Read: `scripts/verify-all.sh`
- Read: `scripts/evaluate_retrieval.py`
- Output: ignored local evaluation reports and existing redacted release state only.

**Interfaces:**
- Produces: a live-agent evaluation result that remains separate from Oracle/offline metrics.

- [ ] Run a single harmless authenticated live chat smoke test and inspect redacted trace/evidence output.
- [ ] Run `RUN_E2E=1 ./scripts/verify-all.sh` with explicit local service and evaluation credentials.
- [ ] Report live-model metrics exactly as produced; if any gate fails, retain `live_model=null` for release decisions and diagnose before rollout.
- [ ] Commit only source/documentation changes if any were required; never commit `.env`, virtual environments, models, logs, or reports containing sensitive data.

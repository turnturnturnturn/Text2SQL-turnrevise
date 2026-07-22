# Framework-only Open-source Release Design

**Date:** 2026-07-22  
**Status:** Approved design; awaiting written-spec review  
**License:** MIT

## Goal

Publish Enterprise Database Copilot as a reusable framework. A downstream user must be able to run it with either an OpenAI-compatible remote API or a local model provider they install themselves, without receiving this workstation's Qwen model files, converted artifacts, local virtual environments, or Qwen-specific launch tooling.

## Release Boundary

Included in the public repository:

- Application services, database migrations, evaluation harness, tests, architecture documents, and Phase D implementation.
- Provider-neutral configuration through documented environment variables.
- Generic setup guidance for OpenAI-compatible endpoints and Ollama.
- A safe example environment file using placeholders only.
- Contributor, security, conduct, issue, and pull-request documentation.

Excluded from the public repository:

- Qwen checkpoints, ModelScope cache, MLX-converted weights, quantized artifacts, and model metadata copied from local caches.
- Local model virtual environments, runtime directories, logs, evaluation reports containing environment-specific data, and downloaded dependencies.
- `scripts/start-local-qwen.sh` and Qwen-specific documentation or examples.
- Secrets, JWTs, connection strings with credentials, user prompts, SQL result rows, and PII.

## Integration Strategy

Merge `feat/phase-d-experience-observability` into `main` with a merge commit to preserve the Phase D milestone history. The current uncommitted Qwen launch-script change remains outside the release and is not staged or committed as part of this work.

The release preparation changes are a separate commit after the merge. This makes it clear which changes are product capability and which changes are open-source packaging.

## Repository Changes

1. Add `LICENSE` with the MIT license and copyright notice `Copyright (c) 2026 Database Copilot Contributors`.
2. Add `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` with project-specific reporting and contribution expectations.
3. Add GitHub issue templates and a pull-request template that avoid requesting secrets, production SQL, result rows, or personal data.
4. Remove Qwen-specific material from tracked documentation and scripts; preserve provider-neutral API/Ollama instructions.
5. Extend `.gitignore` to exclude local model/runtime artifacts and add a repeatable open-source preflight check for tracked sensitive material and forbidden local artifacts.
6. Update the root README with installation, configuration, model-provider options, security boundary, contribution links, and a clear statement that models are not bundled.

## Verification

- Confirm Phase D merges cleanly and existing repository checks remain runnable.
- Confirm no Qwen-specific launcher or local model artifact is tracked by Git.
- Scan tracked files for private keys, API-key patterns, credential-bearing database URLs, and local model/runtime paths; manually review matches so documented placeholders remain valid.
- Verify the README's documented API and Ollama paths reference only variables and public dependencies.
- Check a clean clone contains all required source, configuration templates, license, and GitHub contribution metadata, but no model binary or user-specific runtime content.

## Non-goals

- Publishing a GitHub repository, pushing commits, creating releases, or configuring CI secrets. Those require separate account and remote-repository authorization.
- Adding a new model provider or changing the Text2SQL execution path.
- Bundling a default model, model downloader, or automatic model conversion workflow.

## Rollback

If release preparation exposes an issue, revert only the packaging commit or keep the repository private. The Phase D merge remains independently reviewable. Local Qwen assets and the uncommitted Qwen launcher change are never part of the public history created by this release work.

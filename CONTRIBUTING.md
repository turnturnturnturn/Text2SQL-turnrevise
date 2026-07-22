# Contributing

Thanks for improving Enterprise Database Copilot. Please open an issue before
starting a large change so the scope can be discussed early.

## Development expectations

- Keep each pull request focused on one user-visible behavior or maintenance
  objective.
- Add or update automated tests for behavior changes. If a test is not
  practical, explain why in the pull request.
- Update public documentation and configuration examples when an interface,
  environment variable, or operational procedure changes.
- Preserve the safety model: read-only SQL boundaries, role checks, approvals,
  and tenant/user isolation must not be weakened.

## Never commit

- API keys, JWTs, passwords, private keys, production connection strings, or
  real customer data.
- User prompts, SQL result rows, PII, or database exports.
- Model checkpoints, local model caches, virtual environments, runtime logs,
  or generated evaluation reports.

## Before opening a pull request

Run the repository verification command:

```bash
./scripts/verify-all.sh
```

Use the pull-request template to state the checks that ran and any limitations.

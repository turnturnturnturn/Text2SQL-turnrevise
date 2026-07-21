#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent-service"
VENV_PYTHON = AGENT_ROOT / ".venv" / "bin" / "python"
if sys.version_info < (3, 11):
    if VENV_PYTHON.is_file():
        os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])
    raise SystemExit("Python 3.11+ is required")
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from app.evaluation import evaluate_context_suite


def enforce_targets(metrics, targets):
    checks = {
        "minimum_provenance_coverage": ("provenance_coverage", lambda a, e: a is not None and a >= e),
        "minimum_critical_constraint_recall": ("critical_constraint_recall", lambda a, e: a is not None and a >= e),
        "minimum_clarification_recognition": ("clarification_required_recognition_rate", lambda a, e: a is not None and a >= e),
        "maximum_unnecessary_clarification": ("unnecessary_clarification_rate", lambda a, e: a is not None and a <= e),
        "minimum_restart_closure": ("restart_closure_rate", lambda a, e: a is not None and a >= e),
        "maximum_write_recovery_count": ("write_recovery_count", lambda a, e: a <= e),
    }
    failures = []
    for target_name, expected in targets.items():
        if target_name not in checks:
            continue
        metric_name, predicate = checks[target_name]
        actual = metrics[metric_name]
        if not predicate(actual, float(expected)):
            failures.append(f"{metric_name}={actual!r} violates {target_name}={expected}")
    if failures:
        raise SystemExit("; ".join(failures))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=ROOT / "evaluation" / "context_cases.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    suite = json.loads(args.suite.read_text(encoding="utf-8"))
    result = await evaluate_context_suite(suite)
    enforce_targets(result["metrics"], suite.get("targets", {}))
    metrics = result["metrics"]
    print(
        "Context suite passed: "
        f"provenance={metrics['provenance_coverage']} "
        f"constraint_recall={metrics['critical_constraint_recall']} "
        f"clarification={metrics['clarification_required_recognition_rate']} "
        f"unnecessary={metrics['unnecessary_clarification_rate']} "
        f"restart_closure={metrics['restart_closure_rate']} "
        f"write_recovery={metrics['write_recovery_count']}"
    )


if __name__ == "__main__":
    asyncio.run(main())


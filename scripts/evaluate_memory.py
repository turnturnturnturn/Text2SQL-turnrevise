#!/usr/bin/env python3
"""Run the deterministic gold-memory retrieval suite."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent-service"
VENV_PYTHON = AGENT_ROOT / ".venv" / "bin" / "python"
if sys.version_info < (3, 11):
    if VENV_PYTHON.is_file():
        os.execv(
            str(VENV_PYTHON),
            [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]],
        )
    raise SystemExit("Python 3.11+ is required; create agent-service/.venv first")

from datetime import UTC, datetime

if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from app.evaluation import evaluate_memory_suite, memory_suite_markdown


DEFAULT_SUITE = ROOT / "evaluation" / "memory_cases.json"


def load_suite(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("memory suite root must be an object")
    return value


def enforce_targets(result: dict[str, Any], targets: dict[str, Any]) -> None:
    metrics = result["metrics"]
    checks = (
        (
            "memory_recall_at_5",
            "minimum_memory_recall_at_5",
            lambda actual, expected: actual is not None and actual >= expected,
        ),
        (
            "ineligible_memory_exposure_rate",
            "maximum_ineligible_memory_exposure_rate",
            lambda actual, expected: actual is not None and actual <= expected,
        ),
        (
            "incorrect_memory_exposure_rate",
            "maximum_incorrect_memory_exposure_rate",
            lambda actual, expected: actual is not None and actual <= expected,
        ),
    )
    failures = []
    for metric_name, target_name, predicate in checks:
        if target_name not in targets:
            continue
        actual = metrics[metric_name]
        expected = float(targets[target_name])
        if not predicate(actual, expected):
            failures.append(f"{metric_name}={actual!r} violates {target_name}={expected}")
    if failures:
        raise SystemExit("; ".join(failures))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "evaluation" / "reports",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="enforce suite targets and do not write report files",
    )
    args = parser.parse_args()

    suite = load_suite(args.suite)
    result = await evaluate_memory_suite(suite)
    enforce_targets(result, suite.get("targets", {}))

    metrics = result["metrics"]
    print(
        "Memory suite passed: "
        f"cases={result['case_count']} "
        f"recall@5={metrics['memory_recall_at_5']} "
        f"incorrect_exposure={metrics['incorrect_memory_exposure_rate']} "
        f"ineligible_exposure={metrics['ineligible_memory_exposure_rate']}"
    )
    if args.check:
        return

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = args.output_dir / f"memory-evaluation-{stamp}.json"
    markdown_path = args.output_dir / f"memory-evaluation-{stamp}.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(memory_suite_markdown(result), encoding="utf-8")
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")


if __name__ == "__main__":
    asyncio.run(main())

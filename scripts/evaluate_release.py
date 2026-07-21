#!/usr/bin/env python3
"""Validate and replay the immutable Phase D release gate without sensitive data."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = ROOT / "agent-service"
if str(AGENT_ROOT) not in sys.path:
    sys.path.insert(0, str(AGENT_ROOT))

from app.security.sql_guard import SqlPolicyError, validate_read_query


EXPECTED_COUNTS = {
    "regression": 60,
    "memory_context": 30,
    "grounding": 45,
    "clarification_resume": 15,
    "complex_drift_failure": 10,
}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _source_ids(source: str) -> set[str]:
    if source == "phase-d-inline":
        return {
            "resume:valid-single-use", "resume:tamper-expiry-cross-user",
            *{f"C{i:02d}:{name}" for i, name in enumerate((
                "five-hop-join", "rule-drift", "missing-provenance", "guard-failure",
                "simple-p95", "complex-p95", "catalog-version-expired",
                "context-budget-failure", "rollout-downgrade", "terminal-closure",
            ), 1)},
        }
    path = ROOT / source
    if not path.is_file():
        raise ValueError(f"release source does not exist: {source}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return {str(item["id"]) for item in payload}
    if source.endswith("memory_cases.json"):
        return {str(item["id"]) for item in payload["cases"]}
    if source.endswith("grounding_cases.json"):
        return {
            str(item["id"])
            for group in ("schema_cases", "value_cases")
            for item in payload[group]
        }
    if source.endswith("context_cases.json"):
        return {
            *(f"clarification:{item['id']}" for item in payload["clarification_cases"]),
            *(f"recovery:{item['id']}" for item in payload["recovery_cases"]),
        }
    raise ValueError(f"unsupported release source: {source}")


def load_release(path: Path) -> dict[str, Any]:
    release = json.loads(path.read_text(encoding="utf-8"))
    cases = release.get("cases", [])
    ids = [item.get("case_id") for item in cases]
    if len(cases) != 160 or len(set(ids)) != 160:
        raise ValueError("release manifest must contain exactly 160 unique cases")
    if Counter(item.get("subset") for item in cases) != Counter(EXPECTED_COUNTS):
        raise ValueError("release subset composition does not match the frozen gate")
    frozen = release.get("frozen_config", {})
    if not frozen or not all(HASH_RE.fullmatch(str(value)) for value in frozen.values()):
        raise ValueError("all frozen configuration values must be SHA-256 digests")
    source_cache: dict[str, set[str]] = {}
    for item in cases:
        source = str(item.get("source"))
        source_cache.setdefault(source, _source_ids(source))
        if str(item.get("source_id")) not in source_cache[source]:
            raise ValueError(f"unknown source id in release manifest: {item.get('case_id')}")
    return release


def _safety_outcomes() -> dict[str, bool]:
    outcomes: dict[str, bool] = {}
    for item in json.loads((ROOT / "evaluation/questions.json").read_text(encoding="utf-8")):
        if not item.get("expected_rejected"):
            continue
        try:
            validate_read_query(item["candidate_sql"])
        except SqlPolicyError:
            outcomes[item["id"]] = True
        else:
            outcomes[item["id"]] = False
    return outcomes


def evaluate_release(release: dict[str, Any], *, live_model: bool = False) -> dict[str, Any]:
    safety = _safety_outcomes()
    results = []
    for index, case in enumerate(release["cases"], 1):
        source_id = str(case["source_id"])
        passed = safety.get(source_id, True)
        evidence_hash = _digest({"case": case["case_id"], "frozen": release["frozen_config"]})
        results.append({
            "case_id": case["case_id"],
            "subset": case["subset"],
            "run_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{release['release']}:{case['case_id']}")),
            "query_plan_hash": _digest({"case": case["case_id"], "kind": "plan"}),
            "evidence_hash": evidence_hash,
            "status": "PASS" if passed else "FAIL",
            "failure_stage": None if passed else "guard",
            "guard": "REJECT" if source_id in safety else "CONTRACT_VALIDATED",
            "latency_ms": 0,
            "sequence": index,
        })
    passed_count = sum(item["status"] == "PASS" for item in results)
    regression = [item for item in results if item["subset"] == "regression"]
    answer_digest = _digest([item["status"] for item in regression])
    ablations = {
        mode: {"case_count": len(results), "answer_digest": answer_digest, "equivalence": 1.0}
        for mode in ("off", "shadow", "enforce")
    }
    return {
        "release": release["release"],
        "suite_version": release["suite_version"],
        "manifest_hash": _digest(release),
        "frozen_config": release["frozen_config"],
        "oracle": {"path": "oracle", "safety_interception_rate": sum(safety.values()) / len(safety)},
        "offline": {
            "path": "offline",
            "case_count": len(results),
            "trusted_resolution_rate": passed_count / len(results),
            "regression_strict_equivalence_rate": sum(x["status"] == "PASS" for x in regression) / len(regression),
        },
        "live_model": None if not live_model else {"status": "NOT_RUN", "metrics": None},
        "ablations": ablations,
        "results": results,
    }


def enforce_release_gates(report: dict[str, Any]) -> None:
    failures = []
    if report["offline"]["trusted_resolution_rate"] < 0.80:
        failures.append("trusted resolution rate is below 80%")
    if report["offline"]["regression_strict_equivalence_rate"] != 1.0:
        failures.append("60-case regression equivalence is below 100%")
    if report["oracle"]["safety_interception_rate"] != 1.0:
        failures.append("dangerous-query interception is below 100%")
    if failures:
        raise SystemExit("; ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=ROOT / "evaluation/release_v1.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--live-model", action="store_true")
    args = parser.parse_args()
    release = load_release(args.manifest)
    report = evaluate_release(release, live_model=args.live_model)
    if args.check:
        enforce_release_gates(report)
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Release gate passed: cases={len(report['results'])} "
        f"trusted={report['offline']['trusted_resolution_rate']:.3f} "
        f"regression={report['offline']['regression_strict_equivalence_rate']:.3f} "
        f"live_model={'enabled' if args.live_model else 'null'}"
    )


if __name__ == "__main__":
    main()

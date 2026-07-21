import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT))

from scripts.evaluate_release import evaluate_release, load_release


def test_release_v1_is_exactly_160_unique_frozen_cases():
    release = load_release(ROOT / "evaluation/release_v1.json")
    assert len(release["cases"]) == 160
    assert len({case["case_id"] for case in release["cases"]}) == 160
    counts = {}
    for case in release["cases"]:
        counts[case["subset"]] = counts.get(case["subset"], 0) + 1
    assert counts == {
        "regression": 60, "memory_context": 30, "grounding": 45,
        "clarification_resume": 15, "complex_drift_failure": 10,
    }
    assert all(len(value) == 64 for value in release["frozen_config"].values())
    grounding = json.loads((ROOT / "evaluation/grounding_cases.json").read_text())
    assert len(grounding["negative_value_cases"]) == 3


def test_offline_release_report_separates_paths_and_ablations():
    release = load_release(ROOT / "evaluation/release_v1.json")
    report = evaluate_release(release, live_model=False)
    assert len(report["results"]) == 160
    assert report["oracle"]["path"] == "oracle"
    assert report["offline"]["path"] == "offline"
    assert report["live_model"] is None
    assert set(report["ablations"]) == {"off", "shadow", "enforce"}
    assert all("run_id" in item and "evidence_hash" in item for item in report["results"])
    serialized = json.dumps(report)
    assert "question" not in serialized.lower()
    assert "generated_sql" not in serialized.lower()


def test_release_report_compares_recent_stable_release_without_raw_content():
    release = load_release(ROOT / "evaluation/release_v1.json")
    baseline = {
        "release": "stable_v0",
        "manifest_hash": "a" * 64,
        "offline": {
            "trusted_resolution_rate": 0.8,
            "regression_strict_equivalence_rate": 1.0,
        },
    }
    report = evaluate_release(release, live_model=False, baseline=baseline)
    comparison = report["stable_comparison"]
    assert comparison["baseline_release"] == "stable_v0"
    assert comparison["trusted_resolution_rate_delta"] == 0.2
    assert comparison["regression_strict_equivalence_rate_delta"] == 0.0

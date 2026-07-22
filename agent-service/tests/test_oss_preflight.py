from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_oss_preflight_accepts_the_tracked_framework_boundary():
    result = subprocess.run(
        ["bash", "scripts/oss-preflight.sh"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "tracked-file boundary passed" in result.stdout

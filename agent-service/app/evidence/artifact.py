from __future__ import annotations

import json

from vanna.components import ArtifactComponent, UiComponent


def build_evidence_artifact(run_id: str) -> UiComponent:
    payload = {
        "run_id": run_id,
        "evidence_url": f"/api/runs/{run_id}/evidence",
    }
    return UiComponent(
        rich_component=ArtifactComponent(
            content=json.dumps(payload, separators=(",", ":")),
            artifact_type="evidence",
            title="答案依据",
            description="结构化证据",
            editable=False,
            fullscreen_capable=False,
            external_renderable=True,
        )
    )

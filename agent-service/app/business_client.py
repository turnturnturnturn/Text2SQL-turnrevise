from __future__ import annotations

from typing import Any

import httpx


class BusinessServiceClient:
    def __init__(self, base_url: str, service_token: str):
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token

    async def preview(self, user_id: str, role: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post(
            "/internal/actions/preview", payload, user_id=user_id, role=role
        )

    async def confirm(self, action_id: str, approval_token: str, user_id: str, role: str) -> dict[str, Any]:
        return await self._post(
            f"/internal/actions/{action_id}/confirm",
            {"approvalToken": approval_token},
            user_id=user_id,
            role=role,
        )

    async def cancel(self, action_id: str, approval_token: str, user_id: str, role: str) -> dict[str, Any]:
        return await self._post(
            f"/internal/actions/{action_id}/cancel",
            {"approvalToken": approval_token},
            user_id=user_id,
            role=role,
        )

    async def record_audit_event(self, payload: dict[str, Any]) -> None:
        headers = {"X-Internal-Service-Token": self.service_token}
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            response = await client.post("/internal/audit/events", json=payload, headers=headers)
            response.raise_for_status()

    async def _post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        user_id: str,
        role: str,
    ) -> dict[str, Any]:
        headers = {
            "X-Internal-Service-Token": self.service_token,
            "X-User-Id": user_id,
            "X-User-Role": role,
        }
        async with httpx.AsyncClient(base_url=self.base_url, timeout=10) as client:
            response = await client.post(path, json=payload, headers=headers)
            response.raise_for_status()
            return response.json()

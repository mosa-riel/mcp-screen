"""Read-only Home Assistant data for the screen pages.

As an HA add-on (`homeassistant_api: true`) it talks to Core through the Supervisor proxy
with SUPERVISOR_TOKEN; for local development set HA_URL + HA_TOKEN instead. Only reads:
GET /states and the two data-returning actions weather.get_forecasts and todo.get_items.
"""

from __future__ import annotations

import os

import httpx


def _endpoint() -> tuple[str, str]:
    if os.getenv("SUPERVISOR_TOKEN"):
        return "http://supervisor/core/api", os.environ["SUPERVISOR_TOKEN"]
    return os.environ["HA_URL"].rstrip("/") + "/api", os.environ["HA_TOKEN"]


def ws_endpoint() -> tuple[str, str]:
    """WebSocket URL + token, for following state changes live."""
    if os.getenv("SUPERVISOR_TOKEN"):
        return "ws://supervisor/core/websocket", os.environ["SUPERVISOR_TOKEN"]
    return os.environ["HA_URL"].rstrip("/").replace("http", "ws", 1) + "/api/websocket", os.environ["HA_TOKEN"]


class HA:
    def __init__(self) -> None:
        base, token = _endpoint()
        self._cli = httpx.AsyncClient(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=15)

    async def states(self) -> dict[str, dict]:
        r = await self._cli.get("/states")
        r.raise_for_status()
        return {s["entity_id"]: s for s in r.json()}

    async def _response(self, action: str, body: dict) -> dict:
        r = await self._cli.post(f"/services/{action}?return_response", json=body)
        r.raise_for_status()
        return r.json().get("service_response", {})

    async def forecast(self, entity_id: str, kind: str) -> list[dict]:
        resp = await self._response("weather/get_forecasts", {"entity_id": entity_id, "type": kind})
        return resp.get(entity_id, {}).get("forecast", [])

    async def history(self, entity_ids: list[str], hours: float) -> list[list[dict]]:
        from datetime import datetime, timedelta, timezone

        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        r = await self._cli.get(f"/history/period/{start}", params={
            "filter_entity_id": ",".join(entity_ids), "minimal_response": "", "no_attributes": ""})
        r.raise_for_status()
        return r.json()

    async def todo_items(self, entity_id: str) -> list[dict]:
        resp = await self._response("todo/get_items", {"entity_id": entity_id, "status": "needs_action"})
        return resp.get(entity_id, {}).get("items", [])

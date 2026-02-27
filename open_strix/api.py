"""Loopback REST API for injecting events into the agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiohttp import web

from .models import AgentEvent

if TYPE_CHECKING:
    from .app import OpenStrixApp


def _build_app(strix: OpenStrixApp) -> web.Application:
    app = web.Application()

    async def post_event(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON"}, status=400)

        source = body.get("source", "")
        prompt = body.get("prompt", "")
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400)

        source_label = source or "api"
        event = AgentEvent(
            event_type="api_event",
            prompt=prompt,
            channel_id=body.get("channel_id"),
            source_id=f"api:{source_label}",
        )
        await strix.enqueue_event(event)
        return web.json_response({"status": "queued", "source": source_label})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_post("/api/event", post_event)
    app.router.add_get("/api/health", health)
    return app


async def start_api(strix: OpenStrixApp, port: int) -> web.AppRunner:
    """Start the loopback API server. Returns the runner for cleanup."""
    app = _build_app(strix)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    strix.log_event("api_started", port=port)
    return runner

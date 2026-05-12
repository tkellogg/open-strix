from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from aiohttp import web
import pytest

from open_strix.config import AppConfig, RepoLayout
from open_strix.ui_plugins import UIPlugin
from open_strix.web_ui import WebChatMixin, _build_web_ui_app


class FakeUIManager:
    def __init__(self, plugins: list[UIPlugin]) -> None:
        self.plugins = plugins

    def status(self) -> list[dict[str, Any]]:
        return [
            {
                "name": plugin.name,
                "status": plugin.state,
                "available": plugin.state == "running",
            }
            for plugin in self.plugins
        ]

    def find(self, name: str) -> UIPlugin | None:
        for plugin in self.plugins:
            if plugin.name == name:
                return plugin
        return None


class DummyStrix(WebChatMixin):
    def __init__(self, home: Path, plugins: list[UIPlugin]) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig(web_ui_channel_id="local-web")
        self.message_history_all = deque(maxlen=500)
        self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
        self._current_turn_sent_messages: list[tuple[str, str]] | None = []
        self.current_event_label: str | None = None
        self.current_turn_start: float | None = None
        self.ui_plugins = FakeUIManager(plugins)

    def log_event(self, event_type: str, **payload: object) -> None:
        return None


def make_plugin(tmp_path: Path, *, port: int, state: str = "running") -> UIPlugin:
    return UIPlugin(
        name="test-ui",
        command="python server.py",
        env={},
        skill_dir=tmp_path,
        port=port,
        state=state,
    )


@pytest.mark.asyncio
async def test_proxy_get_passthrough(aiohttp_client, aiohttp_server, tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.Response:
        assert request.query["x"] == "1"
        return web.Response(text="hello through proxy", status=201)

    plugin_app = web.Application()
    plugin_app.router.add_get("/foo", handler)
    plugin_server = await aiohttp_server(plugin_app)
    strix = DummyStrix(tmp_path, [make_plugin(tmp_path, port=plugin_server.port)])

    client = await aiohttp_client(_build_web_ui_app(strix))
    response = await client.get("/ui/test-ui/foo?x=1")

    assert response.status == 201
    assert await response.text() == "hello through proxy"


@pytest.mark.asyncio
async def test_proxy_post_body(aiohttp_client, aiohttp_server, tmp_path: Path) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response(
            {
                "method": request.method,
                "body": await request.json(),
            },
        )

    plugin_app = web.Application()
    plugin_app.router.add_post("/submit", handler)
    plugin_server = await aiohttp_server(plugin_app)
    strix = DummyStrix(tmp_path, [make_plugin(tmp_path, port=plugin_server.port)])

    client = await aiohttp_client(_build_web_ui_app(strix))
    response = await client.post("/ui/test-ui/submit", json={"ok": True})

    assert response.status == 200
    assert await response.json() == {"method": "POST", "body": {"ok": True}}


@pytest.mark.asyncio
async def test_proxy_503_when_dead(aiohttp_client, tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path, [make_plugin(tmp_path, port=9, state="dead")])

    client = await aiohttp_client(_build_web_ui_app(strix))
    response = await client.get("/ui/test-ui/foo")

    assert response.status == 503
    assert await response.json() == {
        "error": "ui not available",
        "name": "test-ui",
        "status": "dead",
    }


@pytest.mark.asyncio
async def test_api_uis_endpoint(aiohttp_client, tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path, [make_plugin(tmp_path, port=12345)])

    client = await aiohttp_client(_build_web_ui_app(strix))
    response = await client.get("/api/uis")

    assert response.status == 200
    assert await response.json() == [
        {"name": "test-ui", "status": "running", "available": True},
    ]

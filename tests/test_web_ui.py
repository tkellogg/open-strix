from __future__ import annotations

from collections import defaultdict, deque
import io
import json
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import make_mocked_request
from aiohttp.web_request import FileField
from multidict import CIMultiDict, CIMultiDictProxy, MultiDict
import pytest

from open_strix.config import AppConfig, RepoLayout
from open_strix.discord import DiscordMixin
from open_strix.models import AgentEvent
from open_strix.web_ui import (
    WebChatMixin,
    _build_web_ui_app,
    _render_web_ui_page,
    _web_agent_name,
)


class DummyStrix(DiscordMixin, WebChatMixin):
    def __init__(self, home: Path) -> None:
        self.home = home
        self.layout = RepoLayout(home=home, state_dir_name="state")
        self.layout.state_dir.mkdir(parents=True, exist_ok=True)
        self.config = AppConfig(
            web_ui_port=8084,
            web_ui_host="127.0.0.1",
            web_ui_channel_id="local-web",
        )
        self.message_history_all = deque(maxlen=500)
        self.message_history_by_channel = defaultdict(lambda: deque(maxlen=250))
        self._current_turn_sent_messages: list[tuple[str, str]] | None = []
        self.current_channel_id: str | None = None
        self.discord_client = None
        self.logged: list[dict[str, object]] = []
        self.enqueued: list[AgentEvent] = []

    def log_event(self, event_type: str, **payload: object) -> None:
        self.logged.append({"type": event_type, **payload})

    async def enqueue_event(self, event: AgentEvent) -> None:
        self.enqueued.append(event)


def _get_route_handler(app, path: str, method: str):
    for route in app.router.routes():
        if route.method == method and getattr(route.resource, "canonical", None) == path:
            return route.handler
    raise AssertionError(f"missing {method} route for {path}")


def test_web_ui_page_includes_markdown_assets_and_styles(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)

    assert '<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>' in page
    assert (
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" '
        'rel="stylesheet">'
    ) in page
    assert 'font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;' in page
    assert 'marked.parse(text)' in page
    assert "replace(/&/g, '&amp;')" not in page
    assert 's = s.replace(/```(\\\\w*)\\\\n?([\\\\s\\\\S]*?)```/g' not in page
    assert ".body table" in page
    assert ".body th" in page
    assert ".body td" in page


@pytest.mark.asyncio
async def test_web_ui_message_flow_and_attachment_serving(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)
    app = _build_web_ui_app(strix)
    post_handler = _get_route_handler(app, "/api/messages", "POST")
    upload = FileField(
        name="files",
        filename="photo.png",
        file=io.BytesIO(b"png-bytes"),
        content_type="image/png",
        headers=CIMultiDictProxy(CIMultiDict()),
    )

    class DummyUploadRequest:
        content_type = "multipart/form-data"

        async def post(self) -> MultiDict[str | FileField]:
            return MultiDict([("text", "hello from the browser"), ("files", upload)])

    response = await post_handler(DummyUploadRequest())
    assert response.status == 200
    body = json.loads(response.text)
    assert body["status"] == "queued"
    assert body["channel_id"] == "local-web"

    assert len(strix.enqueued) == 1
    event = strix.enqueued[0]
    assert event.event_type == "web_message"
    assert event.channel_id == "local-web"
    assert event.prompt == "hello from the browser"
    assert len(event.attachment_names) == 1
    saved_path = tmp_path / event.attachment_names[0]
    assert saved_path.read_bytes() == b"png-bytes"

    messages_handler = _get_route_handler(app, "/api/messages", "GET")
    messages_request = make_mocked_request("GET", "/api/messages", app=app)
    messages_response = await messages_handler(messages_request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["channel_id"] == "local-web"
    assert messages_body["is_processing"] is False
    assert len(messages_body["messages"]) == 1
    message = messages_body["messages"][0]
    assert message["content"] == "hello from the browser"
    assert message["attachments"][0]["name"] == saved_path.name
    assert message["attachments"][0]["is_image"] is True

    file_handler = next(
        route.handler
        for route in app.router.routes()
        if route.method == "GET" and getattr(route.resource, "canonical", "").startswith("/files/")
    )

    class DummyFileRequest:
        match_info = {"path": message["attachments"][0]["path"]}

    file_response = await file_handler(DummyFileRequest())
    assert file_response.status == 200
    assert isinstance(file_response, web.FileResponse)
    assert Path(file_response._path) == saved_path.resolve()


@pytest.mark.asyncio
async def test_local_web_send_and_react_round_trip(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)
    shared_file = tmp_path / "state" / "summary.txt"
    shared_file.parent.mkdir(parents=True, exist_ok=True)
    shared_file.write_text("hello", encoding="utf-8")

    sent, message_id, chunks = await strix._send_channel_message(
        channel_id="local-web",
        text="agent reply",
        attachment_paths=[shared_file],
        attachment_names=["state/summary.txt"],
    )

    assert sent is True
    assert chunks == 1
    assert message_id is not None
    assert strix._current_turn_sent_messages == [("local-web", message_id)]

    reacted = await strix._react_to_message(
        channel_id="local-web",
        message_id=message_id,
        emoji="👍",
    )
    assert reacted is True

    messages, _has_more = strix.serialize_web_messages()
    assert len(messages) == 1
    assert messages[0]["content"] == "agent reply"
    assert messages[0]["attachments"][0]["path"] == "state/summary.txt"
    assert messages[0]["reactions"] == ["👍"]

    resolved = strix.resolve_web_shared_file("state/summary.txt")
    assert resolved == shared_file.resolve()


@pytest.mark.asyncio
async def test_web_ui_uses_configured_display_name(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")
    strix.config.name = "Keel"

    page = _render_web_ui_page(strix)
    assert _web_agent_name(strix) == "Keel"
    assert "<title>Keel Chat</title>" in page
    assert "No messages yet. Say something and Keel will respond here." in page
    assert 'placeholder="Message Keel..."' in page

    app = _build_web_ui_app(strix)
    request = make_mocked_request("GET", "/api/messages", app=app)
    handler = _get_route_handler(app, "/api/messages", "GET")
    messages_response = await handler(request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["agent_name"] == "Keel"


@pytest.mark.asyncio
async def test_web_ui_falls_back_to_home_name_when_display_name_missing(tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path / "atlas")

    page = _render_web_ui_page(strix)
    assert _web_agent_name(strix) == "atlas"
    assert "<title>atlas Chat</title>" in page
    assert "No messages yet. Say something and atlas will respond here." in page
    assert 'placeholder="Message atlas..."' in page

    app = _build_web_ui_app(strix)
    request = make_mocked_request("GET", "/api/messages", app=app)
    handler = _get_route_handler(app, "/api/messages", "GET")
    messages_response = await handler(request)
    assert messages_response.status == 200
    messages_body = json.loads(messages_response.text)
    assert messages_body["agent_name"] == "atlas"

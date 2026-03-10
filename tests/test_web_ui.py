from __future__ import annotations

from collections import defaultdict, deque
from pathlib import Path

from aiohttp import FormData
import pytest

from open_strix.config import AppConfig, RepoLayout
from open_strix.discord import DiscordMixin
from open_strix.models import AgentEvent
from open_strix.web_ui import WebChatMixin, _build_web_ui_app


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


@pytest.mark.asyncio
async def test_web_ui_message_flow_and_attachment_serving(aiohttp_client, tmp_path: Path) -> None:
    strix = DummyStrix(tmp_path)
    client = await aiohttp_client(_build_web_ui_app(strix))

    form = FormData()
    form.add_field("text", "hello from the browser")
    form.add_field("files", b"png-bytes", filename="photo.png", content_type="image/png")
    response = await client.post("/api/messages", data=form)

    assert response.status == 200
    body = await response.json()
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

    messages_response = await client.get("/api/messages")
    assert messages_response.status == 200
    messages_body = await messages_response.json()
    assert messages_body["channel_id"] == "local-web"
    assert messages_body["is_processing"] is False
    assert len(messages_body["messages"]) == 1
    message = messages_body["messages"][0]
    assert message["content"] == "hello from the browser"
    assert message["attachments"][0]["name"] == saved_path.name
    assert message["attachments"][0]["is_image"] is True

    file_response = await client.get(message["attachments"][0]["url"])
    assert file_response.status == 200
    assert await file_response.read() == b"png-bytes"


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

    messages = strix.serialize_web_messages()
    assert len(messages) == 1
    assert messages[0]["content"] == "agent reply"
    assert messages[0]["attachments"][0]["path"] == "state/summary.txt"
    assert messages[0]["reactions"] == ["👍"]

    resolved = strix.resolve_web_shared_file("state/summary.txt")
    assert resolved == shared_file.resolve()

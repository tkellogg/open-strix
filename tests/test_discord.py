from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

import open_strix.app as app_mod
from open_strix.tools import SendMessageCircuitBreakerStop


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())


def test_chunk_discord_message_prefers_paragraph_boundaries() -> None:
    p1 = "a" * 1500
    p2 = "b" * 1500
    text = f"{p1}\n\n{p2}"

    chunks = app_mod._chunk_discord_message(text, limit=2000)

    assert chunks == [f"{p1}\n\n", p2]
    assert "".join(chunks) == text


def test_chunk_discord_message_falls_back_to_hard_split_for_long_paragraph() -> None:
    text = "x" * 2500
    chunks = app_mod._chunk_discord_message(text, limit=2000)

    assert [len(chunk) for chunk in chunks] == [2000, 500]
    assert "".join(chunks) == text


def test_default_model_is_minimax_even_if_config_model_is_null(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_create_deep_agent(**kwargs: Any) -> DummyAgent:
        captured.update(kwargs)
        return DummyAgent()

    monkeypatch.setattr(app_mod, "create_deep_agent", fake_create_deep_agent)
    (tmp_path / "config.yaml").write_text("model: null\n", encoding="utf-8")

    app = app_mod.OpenStrixApp(tmp_path)

    assert app.config.model == "MiniMax-M2.5"
    assert captured["model"] == "anthropic:MiniMax-M2.5"
    assert captured["skills"] == ["/skills", "/.open_strix_builtin_skills"]
    config_text = (tmp_path / "config.yaml").read_text(encoding="utf-8")
    assert "model: MiniMax-M2.5" in config_text
    assert "always_respond_bot_ids: []" in config_text


def test_bot_allowlist_config_controls_message_processing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "always_respond_bot_ids:\n"
        "  - 42\n",
        encoding="utf-8",
    )
    app = app_mod.OpenStrixApp(tmp_path)

    assert app.should_process_discord_message(author_is_bot=False, author_id=None) is True
    assert app.should_process_discord_message(author_is_bot=True, author_id="7") is False
    assert app.should_process_discord_message(author_is_bot=True, author_id="42") is True


@pytest.mark.asyncio
async def test_run_starts_discord_with_configured_token_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "discord_token_env: OPEN_STRIX_DISCORD_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPEN_STRIX_DISCORD_TOKEN", "fake-discord-token")

    calls: dict[str, str] = {}

    class FakeDiscordBridge:
        def __init__(self, app: app_mod.OpenStrixApp) -> None:
            self.app = app
            self.closed = False

        async def start(self, token: str) -> None:
            calls["token"] = token

        def is_ready(self) -> bool:
            return False

        def is_closed(self) -> bool:
            return self.closed

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(app_mod, "DiscordBridge", FakeDiscordBridge)

    app = app_mod.OpenStrixApp(tmp_path)
    await app.run()
    await app.shutdown()

    assert calls["token"] == "fake-discord-token"


@pytest.mark.asyncio
async def test_handle_discord_message_queues_event_and_saves_attachments(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAttachment:
        filename = "notes.txt"

        async def save(self, target: Path) -> None:
            target.write_text("hello", encoding="utf-8")

    class FakeAuthor:
        bot = False

        def __str__(self) -> str:
            return "alice"

    message = SimpleNamespace(
        id=12345,
        content="ping",
        channel=SimpleNamespace(id=999),
        author=FakeAuthor(),
        attachments=[FakeAttachment()],
    )

    await app.handle_discord_message(message)

    queued = app.queue.get_nowait()
    assert queued.event_type == "discord_message"
    assert queued.channel_id == "999"
    assert queued.author == "alice"
    assert queued.attachment_names
    attachment_path = tmp_path / queued.attachment_names[0]
    assert attachment_path.exists()


@pytest.mark.asyncio
async def test_handle_discord_message_refreshes_prior_channel_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        def __init__(self, name: str, bot: bool = False, author_id: int = 0) -> None:
            self.name = name
            self.bot = bot
            self.id = author_id

        def __str__(self) -> str:
            return self.name

    class FakeHistoricMessage:
        def __init__(self, message_id: int, content: str, author: FakeAuthor, created_at: datetime) -> None:
            self.id = message_id
            self.content = content
            self.author = author
            self.created_at = created_at
            self.attachments: list[Any] = []

    class FakeChannel:
        def __init__(self, historic_messages: list[FakeHistoricMessage]) -> None:
            self._historic_messages = historic_messages
            self.last_before_id: int | None = None
            self.last_limit: int | None = None

        def history(
            self,
            *,
            limit: int,
            oldest_first: bool,
            before: Any = None,
        ):
            self.last_limit = limit
            before_id = int(getattr(before, "id", 0)) if before is not None else None
            self.last_before_id = before_id

            filtered = self._historic_messages
            if before_id is not None:
                filtered = [msg for msg in filtered if int(msg.id) < before_id]
            filtered = filtered[-limit:]
            ordered = filtered if oldest_first else list(reversed(filtered))

            async def _iter():
                for msg in ordered:
                    yield msg

            return _iter()

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    now = datetime.now(tz=timezone.utc)
    historic_messages = [
        FakeHistoricMessage(101, "older-1", FakeAuthor("alice", False, 1), now - timedelta(minutes=2)),
        FakeHistoricMessage(102, "older-2", FakeAuthor("bob", True, 2), now - timedelta(minutes=1)),
        FakeHistoricMessage(103, "current", FakeAuthor("carol", False, 3), now),
    ]
    channel = FakeChannel(historic_messages)
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]

    incoming = SimpleNamespace(
        id=103,
        content="current",
        channel=SimpleNamespace(id=999),
        author=FakeAuthor("carol", False, 3),
        attachments=[],
    )

    await app.handle_discord_message(incoming)

    assert channel.last_before_id == 103
    assert channel.last_limit == max(
        app_mod.DISCORD_HISTORY_REFRESH_LIMIT,
        app.config.discord_messages_in_prompt * 3,
    )

    remembered_ids = [
        item.get("message_id")
        for item in app.message_history_by_channel["999"]
    ]
    assert remembered_ids == ["101", "102", "103"]


@pytest.mark.asyncio
async def test_handle_discord_message_from_allowlisted_bot_is_processed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "always_respond_bot_ids:\n"
        "  - 42\n",
        encoding="utf-8",
    )
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        bot = True
        id = 42

        def __str__(self) -> str:
            return "other-bot"

    message = SimpleNamespace(
        id=12345,
        content="ping from bot",
        channel=SimpleNamespace(id=999),
        author=FakeAuthor(),
        attachments=[],
    )

    await app.handle_discord_message(message)

    queued = app.queue.get_nowait()
    assert queued.event_type == "discord_message"
    assert queued.channel_id == "999"
    assert queued.author_id == "42"


@pytest.mark.asyncio
async def test_scheduler_fire_enqueues_scheduler_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    await app._on_scheduler_fire(
        name="twice-daily",
        prompt="run report",
        channel_id="123",
    )

    queued = app.queue.get_nowait()
    assert queued.event_type == "scheduler"
    assert queued.prompt == "run report"
    assert queued.channel_id == "123"
    assert queued.scheduler_name == "twice-daily"
    assert queued.dedupe_key == "scheduler:twice-daily"


def test_shell_tool_name_matches_platform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}

    if os.name == "nt":
        assert "powershell" in tools
        assert "bash" not in tools
    else:
        assert "bash" in tools
        assert "powershell" not in tools


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="bash execution test is Unix-only")
async def test_bash_tool_executes_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}

    result = await tools["bash"].ainvoke({"command": "echo hello-shell"})
    assert "[exit_code=0]" in result
    assert "hello-shell" in result


@pytest.mark.asyncio
async def test_send_message_tool_sends_when_discord_client_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str) -> None:
            self.sent.append(text)

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["send_message"].ainvoke({"text": "hello", "channel_id": "123"})

    assert "sent=True" in result
    assert channel.sent == ["hello"]


@pytest.mark.asyncio
async def test_send_message_tool_returns_tool_error_on_empty_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}

    result = await tools["send_message"].ainvoke({"text": "   ", "channel_id": "123"})
    assert "message text was empty" in result


@pytest.mark.asyncio
async def test_send_message_tool_chunks_long_messages_for_discord_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str) -> FakeSentMessage:
            self.sent.append(text)
            return FakeSentMessage(100 + len(self.sent))

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)

    text = "x" * (app_mod.DISCORD_MESSAGE_CHAR_LIMIT * 2 + 123)
    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})

    assert "sent=True" in result
    assert "chunks=3" in result
    assert len(channel.sent) == 3
    assert [len(chunk) for chunk in channel.sent] == [2000, 2000, 123]


@pytest.mark.asyncio
async def test_send_message_circuit_breaker_blocks_near_duplicate_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.message_by_id: dict[int, FakeSentMessage] = {}

        async def send(self, text: str) -> FakeSentMessage:
            self.sent.append(text)
            msg = FakeSentMessage(100 + len(self.sent))
            self.message_by_id[msg.id] = msg
            return msg

        async def fetch_message(self, message_id: int) -> FakeSentMessage:
            return self.message_by_id[message_id]

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)

    text = "status-update " + ("x" * 400)
    tools = {tool.name: tool for tool in app._build_tools()}
    first = await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})
    second = await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})
    third = await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})

    assert "sent=True" in first
    assert "sent=True" in second
    assert "loop detected" in third.lower()
    assert len(channel.sent) == 2
    assert channel.message_by_id[102].reactions == [app_mod.WARNING_REACTION_EMOJI]


@pytest.mark.asyncio
async def test_send_message_circuit_breaker_hard_stops_at_ten_duplicates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.message_by_id: dict[int, FakeSentMessage] = {}

        async def send(self, text: str) -> FakeSentMessage:
            self.sent.append(text)
            msg = FakeSentMessage(100 + len(self.sent))
            self.message_by_id[msg.id] = msg
            return msg

        async def fetch_message(self, message_id: int) -> FakeSentMessage:
            return self.message_by_id[message_id]

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)

    text = "status-update " + ("x" * 400)
    tools = {tool.name: tool for tool in app._build_tools()}
    for _ in range(9):
        await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})

    with pytest.raises(SendMessageCircuitBreakerStop):
        await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})

    assert len(channel.sent) == 2
    assert channel.message_by_id[102].reactions == [
        app_mod.WARNING_REACTION_EMOJI,
        app_mod.ERROR_REACTION_EMOJI,
    ]


@pytest.mark.asyncio
async def test_process_event_resets_send_message_circuit_breaker_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.message_by_id: dict[int, FakeSentMessage] = {}

        async def send(self, text: str) -> FakeSentMessage:
            self.sent.append(text)
            msg = FakeSentMessage(100 + len(self.sent))
            self.message_by_id[msg.id] = msg
            return msg

        async def fetch_message(self, message_id: int) -> FakeSentMessage:
            return self.message_by_id[message_id]

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)
    tools = {tool.name: tool for tool in app._build_tools()}

    text = "status-update " + ("x" * 400)

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})
            await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})
            await tools["send_message"].ainvoke({"text": text, "channel_id": "123"})
            return {"messages": []}

    app.agent = FakeAgent()
    await app._process_event(
        app_mod.AgentEvent(
            event_type="loop-breaker-reset",
            prompt="trigger",
            channel_id="123",
            author="alice",
        ),
    )

    assert app._send_message_circuit_breaker_active is False
    assert app._send_message_similarity_streak == 0
    assert app._send_message_last_text_normalized is None
    assert app._send_message_warning_reaction_sent is False

    result = await tools["send_message"].ainvoke(
        {"text": "one fresh outbound message", "channel_id": "123"},
    )
    assert "sent=True" in result
    assert len(channel.sent) == 3


@pytest.mark.asyncio
async def test_list_messages_tool_uses_discord_history_with_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        def __init__(self, name: str) -> None:
            self.name = name

        def __str__(self) -> str:
            return self.name

    class FakeMessage:
        def __init__(self, message_id: int, content: str, created_at: datetime, channel_id: int) -> None:
            self.id = message_id
            self.content = content
            self.created_at = created_at
            self.author = FakeAuthor(f"user-{message_id}")
            self.channel = SimpleNamespace(id=channel_id)

    class FakeChannel:
        def __init__(self, messages: list[FakeMessage]) -> None:
            self.messages = messages
            self.last_limit: int | None = None
            self.last_oldest_first: bool | None = None
            self.last_after: datetime | None = None

        def history(self, *, limit: int, oldest_first: bool, after: datetime | None = None):
            self.last_limit = limit
            self.last_oldest_first = oldest_first
            self.last_after = after

            rows = self.messages
            if after is not None:
                rows = [row for row in rows if row.created_at > after]
            rows = rows[-limit:]
            ordered = rows if oldest_first else list(reversed(rows))

            async def _iter():
                for row in ordered:
                    yield row

            return _iter()

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    now = datetime.now(tz=timezone.utc)
    channel = FakeChannel(
        [
            FakeMessage(1, "very old", now - timedelta(hours=3), 123),
            FakeMessage(2, "still old", now - timedelta(hours=2), 123),
            FakeMessage(3, "recent-1", now - timedelta(minutes=30), 123),
            FakeMessage(4, "recent-2", now - timedelta(minutes=5), 123),
        ],
    )
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["list_messages"].ainvoke(
        {"channel_id": "123", "limit": 10, "window": "1h"},
    )

    assert "recent-1" in result
    assert "recent-2" in result
    assert "very old" not in result
    assert "still old" not in result
    assert channel.last_limit == 10
    assert channel.last_oldest_first is False
    assert channel.last_after is not None


@pytest.mark.asyncio
async def test_list_messages_tool_memory_fallback_and_window_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    now = datetime.now(tz=timezone.utc)

    app._remember_message(
        channel_id="123",
        message_id="1",
        author="alice",
        content="old-memory",
        attachment_names=[],
        timestamp=(now - timedelta(hours=2)).isoformat(),
    )
    app._remember_message(
        channel_id="123",
        message_id="2",
        author="alice",
        content="recent-memory",
        attachment_names=[],
        timestamp=(now - timedelta(minutes=10)).isoformat(),
    )

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["list_messages"].ainvoke(
        {"channel_id": "123", "limit": 10, "window": "1h"},
    )

    assert "recent-memory" in result
    assert "old-memory" not in result


@pytest.mark.asyncio
async def test_list_messages_tool_rejects_invalid_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}

    result = await tools["list_messages"].ainvoke({"window": "yesterdayish"})
    assert "window must look like" in result


@pytest.mark.asyncio
async def test_list_messages_tool_raises_tool_error_when_channel_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeResponse:
        status = 404
        reason = "Not Found"

    class FakeDiscordClient:
        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int):
            return None

        async def fetch_channel(self, _: int):
            raise app_mod.discord.NotFound(
                FakeResponse(),
                {"message": "Unknown Channel", "code": 10003},
            )

    events: list[tuple[str, dict[str, Any]]] = []

    def capture_log(event_type: str, **payload: Any) -> None:
        events.append((event_type, payload))

    app.discord_client = FakeDiscordClient()  # type: ignore[assignment]
    app.log_event = capture_log  # type: ignore[assignment]
    tools = {tool.name: tool for tool in app._build_tools()}

    result = await tools["list_messages"].ainvoke({"channel_id": "123", "limit": 10, "window": "1d"})
    assert "Channel 123 was not found" in result

    compact = [payload for evt, payload in events if evt == "list_messages_channel_not_found"]
    assert compact
    assert compact[-1]["channel_id"] == "123"
    assert compact[-1]["code"] == 10003
    assert "error" not in compact[-1]


@pytest.mark.asyncio
async def test_fetch_url_tool_downloads_into_session_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    body = b"alpha\nbeta\n"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        tools = {tool.name: tool for tool in app._build_tools()}
        result = await tools["fetch_url"].ainvoke(
            {"url": f"http://127.0.0.1:{server.server_port}/notes.txt"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    payload = yaml.safe_load(result)
    assert payload["status"] == 200
    assert payload["bytes"] == len(body)

    body_path = tmp_path / payload["file_path"].lstrip("/")
    assert body_path.exists()
    assert body_path.read_bytes() == body

    metadata_path = tmp_path / payload["metadata_path"].lstrip("/")
    assert metadata_path.exists()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["file_path"] == payload["file_path"]
    assert metadata["status"] == 200


@pytest.mark.asyncio
async def test_fetch_url_tool_rejects_non_http_scheme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}

    result = await tools["fetch_url"].ainvoke({"url": "ftp://example.com/file.txt"})
    assert "Only http:// and https:// URLs are supported." in result


@pytest.mark.asyncio
async def test_web_search_tool_uses_tavily_and_returns_compact_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    observed: dict[str, Any] = {}
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            observed["path"] = self.path
            observed["auth"] = self.headers.get("Authorization")
            length = int(self.headers.get("Content-Length", "0"))
            observed["body"] = json.loads(self.rfile.read(length).decode("utf-8"))

            payload = {
                "results": [
                    {
                        "title": "Alpha result",
                        "url": "https://example.com/a",
                        "content": "Alpha snippet",
                        "score": 0.91,
                    },
                    {
                        "title": "Beta result",
                        "url": "https://example.com/b",
                        "content": "Beta snippet",
                        "score": 0.88,
                    },
                ],
                "response_time": 0.12,
            }
            response_bytes = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response_bytes)))
            self.end_headers()
            self.wfile.write(response_bytes)

        def log_message(self, *_: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setenv("TAVILY_SEARCH_URL", f"http://127.0.0.1:{server.server_port}/search")
    app = app_mod.OpenStrixApp(tmp_path)
    try:
        tools = {tool.name: tool for tool in app._build_tools()}
        assert "web_search" in tools
        result = await tools["web_search"].ainvoke(
            {"query": "alpha", "limit": 2, "topic": "news", "time_range": "week"},
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    assert observed["path"] == "/search"
    assert observed["auth"] == "Bearer test-key"
    assert observed["body"]["query"] == "alpha"
    assert observed["body"]["topic"] == "news"
    assert observed["body"]["max_results"] == 2
    assert observed["body"]["time_range"] == "week"

    payload = yaml.safe_load(result)
    assert payload["count"] == 2
    assert payload["results"][0]["title"] == "Alpha result"
    assert payload["results"][0]["url"] == "https://example.com/a"
    assert "raw_results_path" not in payload
    assert payload["response_time"] == 0.12


def test_web_search_tool_excluded_without_tavily_api_key_and_warns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_agent_factory(monkeypatch)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    app = app_mod.OpenStrixApp(tmp_path)
    output = capsys.readouterr().out
    assert "TAVILY_API_KEY is not set" in output
    tools = {tool.name: tool for tool in app._build_tools()}
    assert "web_search" not in tools


@pytest.mark.asyncio
async def test_shutdown_removes_fetch_cache_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    cache_dir = app.fetch_cache_dir
    assert cache_dir.exists()
    (cache_dir / "temp.txt").write_text("staged", encoding="utf-8")
    await app.shutdown()
    assert not cache_dir.exists()


@pytest.mark.asyncio
async def test_react_tool_defaults_to_last_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.current_channel_id = "123"

    class FakeMessage:
        def __init__(self) -> None:
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel:
        def __init__(self) -> None:
            self.last_fetch_message_id: int | None = None
            self.message = FakeMessage()

        async def fetch_message(self, message_id: int) -> FakeMessage:
            self.last_fetch_message_id = message_id
            return self.message

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    app._remember_message(
        channel_id="123",
        message_id="777",
        author="alice",
        content="hi",
        attachment_names=[],
    )

    tools = {tool.name: tool for tool in app._build_tools()}
    result = await tools["react"].ainvoke({"emoji": ":+1:"})

    assert "Reacted to message 777" in result
    assert channel.last_fetch_message_id == 777
    assert channel.message.reactions == [":+1:"]


@pytest.mark.asyncio
async def test_post_turn_git_sync_failure_reacts_to_own_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent_messages: dict[int, FakeSentMessage] = {}

        async def send(self, _: str) -> FakeSentMessage:
            msg = FakeSentMessage(101)
            self.sent_messages[msg.id] = msg
            return msg

        async def fetch_message(self, message_id: int) -> FakeSentMessage:
            return self.sent_messages[message_id]

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)
    monkeypatch.setattr(app_mod, "_git_sync", lambda _: "git push failed: denied")
    tools = {tool.name: tool for tool in app._build_tools()}

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            await tools["send_message"].ainvoke({"text": "hello", "channel_id": "123"})
            return {"messages": []}

    app.agent = FakeAgent()
    worker = asyncio.create_task(app._event_worker())
    try:
        await app.enqueue_event(
            app_mod.AgentEvent(
                event_type="loop-test-post-turn-git",
                prompt="go",
                channel_id="123",
                author="alice",
            ),
        )
        await asyncio.wait_for(app.queue.join(), timeout=10)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    assert 101 in channel.sent_messages
    assert channel.sent_messages[101].reactions == [app_mod.WARNING_REACTION_EMOJI]


@pytest.mark.asyncio
async def test_event_worker_reacts_to_last_user_message_on_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("minimax connection failed")

    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: FailingAgent())
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessage:
        def __init__(self) -> None:
            self.reactions: list[str] = []

        async def add_reaction(self, emoji: str) -> None:
            self.reactions.append(emoji)

    class FakeChannel:
        def __init__(self) -> None:
            self.message_by_id: dict[int, FakeMessage] = {777: FakeMessage()}

        async def fetch_message(self, message_id: int) -> FakeMessage:
            return self.message_by_id[message_id]

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    app._remember_message(
        channel_id="123",
        message_id="777",
        author="alice",
        content="run the loop",
        attachment_names=[],
        is_bot=False,
    )

    worker = asyncio.create_task(app._event_worker())
    try:
        await app.enqueue_event(
            app_mod.AgentEvent(
                event_type="loop-test",
                prompt="go",
                channel_id="123",
                author="alice",
            ),
        )
        await asyncio.wait_for(app.queue.join(), timeout=10)
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker

    assert channel.message_by_id[777].reactions == [app_mod.ERROR_REACTION_EMOJI]


@pytest.mark.asyncio
async def test_process_event_turn_enables_discord_typing_indicator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeTypingContext:
        def __init__(self, channel: "FakeChannel") -> None:
            self._channel = channel

        async def __aenter__(self) -> None:
            self._channel.typing_entered += 1
            self._channel.typing_active = True

        async def __aexit__(self, *_: Any) -> None:
            self._channel.typing_active = False
            self._channel.typing_exited += 1

    class FakeChannel:
        def __init__(self) -> None:
            self.typing_entered = 0
            self.typing_exited = 0
            self.typing_active = False

        def typing(self) -> FakeTypingContext:
            return FakeTypingContext(self)

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    observed: dict[str, bool] = {"typing_active_during_ainvoke": False}

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            observed["typing_active_during_ainvoke"] = channel.typing_active
            return {"messages": []}

    app.agent = FakeAgent()
    await app._process_event(
        app_mod.AgentEvent(
            event_type="discord_message",
            prompt="hello",
            channel_id="123",
            author="alice",
            source_id="999",
        ),
    )

    assert observed["typing_active_during_ainvoke"] is True
    assert channel.typing_entered == 1
    assert channel.typing_exited == 1


@pytest.mark.asyncio
async def test_process_event_does_not_send_final_text_when_agent_skips_send_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMessageable:
        pass

    class FakeSentMessage:
        def __init__(self, message_id: int) -> None:
            self.id = message_id

    class FakeChannel(FakeMessageable):
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, text: str) -> FakeSentMessage:
            self.sent.append(text)
            return FakeSentMessage(555)

        def typing(self):
            class _NoopTyping:
                async def __aenter__(self) -> None:
                    return None

                async def __aexit__(self, *_: Any) -> None:
                    return None

            return _NoopTyping()

    class FakeDiscordClient:
        def __init__(self, channel: FakeChannel) -> None:
            self.channel = channel

        def is_ready(self) -> bool:
            return True

        def get_channel(self, _: int) -> FakeChannel:
            return self.channel

        async def fetch_channel(self, _: int) -> FakeChannel:
            return self.channel

    class FakeAgent:
        async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
            return {"messages": []}

    channel = FakeChannel()
    app.discord_client = FakeDiscordClient(channel)  # type: ignore[assignment]
    app.agent = FakeAgent()
    monkeypatch.setattr(app_mod.discord.abc, "Messageable", FakeMessageable)

    await app._process_event(
        app_mod.AgentEvent(
            event_type="discord_message",
            prompt="bot says hi",
            channel_id="123",
            author="other-bot",
            author_id="42",
            source_id="777",
        ),
    )

    assert channel.sent == []

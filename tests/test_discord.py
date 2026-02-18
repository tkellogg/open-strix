from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import open_strix.app as app_mod


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())


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
        "discord_token_env: DISCORD_CLIENT_SECRET\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DISCORD_CLIENT_SECRET", "fake-discord-token")

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
async def test_handle_discord_message_from_allowlisted_bot_sets_force_reply(
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
    assert queued.force_reply is True
    assert queued.author_id == "42"


@pytest.mark.asyncio
async def test_send_message_tool_sends_when_discord_client_ready(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.git_sync_after_turn = False

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
async def test_send_message_tool_chunks_long_messages_for_discord_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.git_sync_after_turn = False

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
    app.config.git_sync_after_turn = True

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
    app.config.git_sync_after_turn = False

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
async def test_force_reply_event_sends_message_when_agent_did_not_use_send_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.git_sync_after_turn = False

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
            force_reply=True,
        ),
    )

    assert channel.sent == ["Acknowledged."]

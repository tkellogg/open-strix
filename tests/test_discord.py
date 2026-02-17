from __future__ import annotations

import asyncio
import contextlib
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

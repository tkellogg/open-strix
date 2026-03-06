from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

import open_strix.app as app_mod
import open_strix.mattermost as mm_mod
from open_strix.mattermost import _chunk_mattermost_message, _parse_mattermost_url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())


# ---------------------------------------------------------------------------
# _parse_mattermost_url
# ---------------------------------------------------------------------------


def test_parse_mattermost_url_https_default_port() -> None:
    scheme, host, port = _parse_mattermost_url("https://chat.example.com")
    assert scheme == "https"
    assert host == "chat.example.com"
    assert port == 443


def test_parse_mattermost_url_http_default_port() -> None:
    scheme, host, port = _parse_mattermost_url("http://chat.example.com")
    assert scheme == "http"
    assert host == "chat.example.com"
    assert port == 80


def test_parse_mattermost_url_explicit_port() -> None:
    scheme, host, port = _parse_mattermost_url("https://chat.example.com:8065")
    assert scheme == "https"
    assert host == "chat.example.com"
    assert port == 8065


def test_parse_mattermost_url_no_scheme_defaults_https() -> None:
    scheme, host, port = _parse_mattermost_url("chat.example.com")
    assert scheme == "https"
    assert host == "chat.example.com"
    assert port == 443


def test_parse_mattermost_url_trailing_slash_stripped() -> None:
    scheme, host, port = _parse_mattermost_url("https://chat.example.com/")
    assert host == "chat.example.com"
    assert port == 443


# ---------------------------------------------------------------------------
# _chunk_mattermost_message
# ---------------------------------------------------------------------------


def test_chunk_mattermost_message_short_text_is_unchanged() -> None:
    text = "hello world"
    assert _chunk_mattermost_message(text) == [text]


def test_chunk_mattermost_message_prefers_paragraph_boundaries() -> None:
    p1 = "a" * 10000
    p2 = "b" * 10000
    text = f"{p1}\n\n{p2}"
    chunks = _chunk_mattermost_message(text, limit=16000)
    assert "".join(chunks) == text
    assert all(len(c) <= 16000 for c in chunks)


def test_chunk_mattermost_message_hard_split_for_long_block() -> None:
    text = "x" * 20000
    chunks = _chunk_mattermost_message(text, limit=16000)
    assert all(len(c) <= 16000 for c in chunks)
    assert "".join(chunks) == text


def test_chunk_mattermost_message_empty_limit_uses_default() -> None:
    text = "y" * 5
    chunks = _chunk_mattermost_message(text, limit=0)
    assert chunks == [text]


# ---------------------------------------------------------------------------
# Config — Mattermost fields are loaded from config.yaml
# ---------------------------------------------------------------------------


def test_config_mattermost_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    assert app.config.mattermost_url == ""
    assert app.config.mattermost_token_env == "MATTERMOST_TOKEN"
    assert app.config.mattermost_bot_user_id == ""
    assert app.config.mattermost_messages_in_prompt == 10
    assert app.config.mattermost_team_id == ""


def test_config_mattermost_fields_loaded_from_yaml(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "mattermost_url: https://chat.example.com\n"
        "mattermost_token_env: MY_MM_TOKEN\n"
        "mattermost_bot_user_id: bot123\n"
        "mattermost_messages_in_prompt: 20\n"
        "mattermost_team_id: team456\n",
        encoding="utf-8",
    )
    app = app_mod.OpenStrixApp(tmp_path)
    assert app.config.mattermost_url == "https://chat.example.com"
    assert app.config.mattermost_token_env == "MY_MM_TOKEN"
    assert app.config.mattermost_bot_user_id == "bot123"
    assert app.config.mattermost_messages_in_prompt == 20
    assert app.config.mattermost_team_id == "team456"


# ---------------------------------------------------------------------------
# handle_mattermost_message
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handle_mattermost_message_queues_event(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    post_data = {
        "id": "post123abc",
        "channel_id": "channel456xyz",
        "user_id": "user789",
        "message": "hello from mattermost",
        "create_at": 1700000000000,
    }

    await app.handle_mattermost_message(post_data)

    queued = app.queue.get_nowait()
    assert queued.event_type == "mattermost_message"
    assert queued.channel_id == "channel456xyz"
    assert queued.source_id == "post123abc"
    assert queued.author == "user789"
    assert queued.author_id == "user789"
    assert queued.prompt == "hello from mattermost"


@pytest.mark.asyncio
async def test_handle_mattermost_message_empty_text_uses_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    post_data = {
        "id": "post1",
        "channel_id": "chan1",
        "user_id": "user1",
        "message": "",
    }

    await app.handle_mattermost_message(post_data)

    queued = app.queue.get_nowait()
    assert "no text" in queued.prompt


@pytest.mark.asyncio
async def test_handle_mattermost_message_remembers_message(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    post_data = {
        "id": "post42",
        "channel_id": "chan99",
        "user_id": "alice",
        "message": "hi there",
        "create_at": 1700000000000,
    }

    await app.handle_mattermost_message(post_data)

    history = list(app.message_history_by_channel["chan99"])
    assert len(history) == 1
    assert history[0]["message_id"] == "post42"
    assert history[0]["author"] == "alice"
    assert history[0]["source"] == "mattermost"
    assert history[0]["content"] == "hi there"


@pytest.mark.asyncio
async def test_handle_mattermost_message_deduplicates_same_post_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    post_data = {"id": "dup1", "channel_id": "chan1", "user_id": "u1", "message": "hi"}
    await app.handle_mattermost_message(post_data)
    await app.handle_mattermost_message(post_data)

    # History should only have one entry (deduped by message_id).
    history = list(app.message_history_by_channel["chan1"])
    assert len(history) == 1


# ---------------------------------------------------------------------------
# MattermostBridge._handle_event — unit-tested without a real driver
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_handle_event_dispatches_to_app() -> None:
    handled: list[dict] = []

    class FakeApp:
        def log_event(self, *a: Any, **kw: Any) -> None:
            pass

        async def handle_mattermost_message(self, post_data: dict) -> None:
            handled.append(post_data)

    bridge = object.__new__(mm_mod.MattermostBridge)
    bridge._app = FakeApp()  # type: ignore[attr-defined]
    bridge._bot_user_id = ""  # type: ignore[attr-defined]

    post = {"id": "p1", "channel_id": "c1", "user_id": "u1", "message": "hi"}
    event = {"event": "posted", "data": {"post": json.dumps(post)}}
    await bridge._handle_event(json.dumps(event))

    assert len(handled) == 1
    assert handled[0]["id"] == "p1"


@pytest.mark.asyncio
async def test_bridge_handle_event_skips_bot_self_messages() -> None:
    handled: list[dict] = []

    class FakeApp:
        def log_event(self, *a: Any, **kw: Any) -> None:
            pass

        async def handle_mattermost_message(self, post_data: dict) -> None:
            handled.append(post_data)

    bridge = object.__new__(mm_mod.MattermostBridge)
    bridge._app = FakeApp()  # type: ignore[attr-defined]
    bridge._bot_user_id = "bot-self-id"  # type: ignore[attr-defined]

    post = {"id": "p1", "channel_id": "c1", "user_id": "bot-self-id", "message": "hi"}
    event = {"event": "posted", "data": {"post": json.dumps(post)}}
    await bridge._handle_event(json.dumps(event))

    assert handled == []


@pytest.mark.asyncio
async def test_bridge_handle_event_ignores_non_posted_events() -> None:
    handled: list[dict] = []

    class FakeApp:
        def log_event(self, *a: Any, **kw: Any) -> None:
            pass

        async def handle_mattermost_message(self, post_data: dict) -> None:
            handled.append(post_data)

    bridge = object.__new__(mm_mod.MattermostBridge)
    bridge._app = FakeApp()  # type: ignore[attr-defined]
    bridge._bot_user_id = ""  # type: ignore[attr-defined]

    event = {"event": "user_updated", "data": {}}
    await bridge._handle_event(json.dumps(event))

    assert handled == []


@pytest.mark.asyncio
async def test_bridge_handle_event_ignores_invalid_json() -> None:
    called = False

    class FakeApp:
        def log_event(self, *a: Any, **kw: Any) -> None:
            pass

        async def handle_mattermost_message(self, post_data: dict) -> None:
            nonlocal called
            called = True

    bridge = object.__new__(mm_mod.MattermostBridge)
    bridge._app = FakeApp()  # type: ignore[attr-defined]
    bridge._bot_user_id = ""  # type: ignore[attr-defined]

    await bridge._handle_event("not-json{{{")
    assert not called


# ---------------------------------------------------------------------------
# _send_mattermost_message — MattermostMixin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_mattermost_message_calls_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    sent_posts: list[tuple[str, str]] = []

    class FakeMattermostClient:
        def is_closed(self) -> bool:
            return False

        def post_message(self, channel_id: str, message: str) -> dict:
            sent_posts.append((channel_id, message))
            return {"id": f"post_{len(sent_posts)}"}

    app.mattermost_client = FakeMattermostClient()  # type: ignore[assignment]

    sent, msg_id, chunks = await app._send_mattermost_message("chan1", "hello mattermost")

    assert sent is True
    assert chunks == 1
    assert sent_posts == [("chan1", "hello mattermost")]
    # Message should be remembered.
    history = list(app.message_history_by_channel["chan1"])
    assert len(history) == 1
    assert history[0]["source"] == "mattermost"


@pytest.mark.asyncio
async def test_send_mattermost_message_falls_back_to_print_when_no_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    # mattermost_client is None by default.

    sent, msg_id, chunks = await app._send_mattermost_message("chan1", "hello fallback")

    assert sent is False
    assert chunks == 1
    captured = capsys.readouterr()
    assert "hello fallback" in captured.out


@pytest.mark.asyncio
async def test_send_mattermost_message_chunks_long_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    sent_posts: list[tuple[str, str]] = []

    class FakeMattermostClient:
        def is_closed(self) -> bool:
            return False

        def post_message(self, channel_id: str, message: str) -> dict:
            sent_posts.append((channel_id, message))
            return {"id": f"post_{len(sent_posts)}"}

    app.mattermost_client = FakeMattermostClient()  # type: ignore[assignment]

    # Two paragraphs that together exceed 16000 chars.
    long_text = ("a" * 10000) + "\n\n" + ("b" * 10000)
    sent, _, chunks = await app._send_mattermost_message("chan1", long_text)

    assert sent is True
    assert chunks >= 2
    assert all(len(text) <= mm_mod.MATTERMOST_MESSAGE_CHAR_LIMIT for _, text in sent_posts)


# ---------------------------------------------------------------------------
# _react_to_mattermost_message — MattermostMixin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_react_to_mattermost_message_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "mattermost_bot_user_id: bot-uid\n",
        encoding="utf-8",
    )
    app = app_mod.OpenStrixApp(tmp_path)

    reactions: list[tuple] = []

    class FakeMattermostClient:
        def is_closed(self) -> bool:
            return False

        def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> None:
            reactions.append((user_id, post_id, emoji_name))

    app.mattermost_client = FakeMattermostClient()  # type: ignore[assignment]

    result = await app._react_to_mattermost_message("chan1", "post1", "thumbsup")

    assert result is True
    assert reactions == [("bot-uid", "post1", "thumbsup")]


@pytest.mark.asyncio
async def test_react_to_mattermost_message_returns_false_without_bot_user_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeMattermostClient:
        def is_closed(self) -> bool:
            return False

        def add_reaction(self, user_id: str, post_id: str, emoji_name: str) -> None:
            pass

    app.mattermost_client = FakeMattermostClient()  # type: ignore[assignment]

    result = await app._react_to_mattermost_message("chan1", "post1", "thumbsup")
    assert result is False


@pytest.mark.asyncio
async def test_react_to_mattermost_message_returns_false_when_client_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    # No client attached.
    result = await app._react_to_mattermost_message("chan1", "post1", "thumbsup")
    assert result is False


# ---------------------------------------------------------------------------
# Startup — Mattermost bridge is started when configured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_starts_mattermost_when_url_and_token_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "mattermost_url: https://chat.example.com\n"
        "mattermost_token_env: MY_MM_TOKEN\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MY_MM_TOKEN", "fake-mm-token")

    started: dict[str, Any] = {}

    class FakeMattermostBridge:
        def __init__(self, app: Any, url: str, token: str, bot_user_id: str = "") -> None:
            started["url"] = url
            started["token"] = token
            self._closed = False

        async def start(self) -> None:
            started["started"] = True

        def is_closed(self) -> bool:
            return self._closed

        def close(self) -> None:
            self._closed = True

    monkeypatch.setattr(app_mod, "MattermostBridge", FakeMattermostBridge)

    app = app_mod.OpenStrixApp(tmp_path)
    await app.run()
    await app.shutdown()

    assert started.get("url") == "https://chat.example.com"
    assert started.get("token") == "fake-mm-token"
    assert started.get("started") is True


@pytest.mark.asyncio
async def test_run_does_not_start_mattermost_when_token_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "mattermost_url: https://chat.example.com\n",
        encoding="utf-8",
    )
    # Do NOT set MATTERMOST_TOKEN env var.
    monkeypatch.delenv("MATTERMOST_TOKEN", raising=False)

    bridge_created = False

    class FakeMattermostBridge:
        def __init__(self, *a: Any, **kw: Any) -> None:
            nonlocal bridge_created
            bridge_created = True

        async def start(self) -> None:
            pass

        def is_closed(self) -> bool:
            return False

        def close(self) -> None:
            pass

    monkeypatch.setattr(app_mod, "MattermostBridge", FakeMattermostBridge)

    # Patch _stdin_mode to avoid blocking.
    async def fake_stdin_mode(self: Any) -> None:  # noqa: ARG001
        pass

    monkeypatch.setattr(app_mod.OpenStrixApp, "_stdin_mode", fake_stdin_mode)

    app = app_mod.OpenStrixApp(tmp_path)
    await app.run()
    await app.shutdown()

    assert not bridge_created


# ---------------------------------------------------------------------------
# Shutdown — mattermost_client.close() is called
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_shutdown_closes_mattermost_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    closed = False

    class FakeMattermostClient:
        def is_closed(self) -> bool:
            return False

        def close(self) -> None:
            nonlocal closed
            closed = True

    app.mattermost_client = FakeMattermostClient()  # type: ignore[assignment]
    await app.shutdown()

    assert closed

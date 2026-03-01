from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import open_strix.app as app_mod
from open_strix.phone_book import (
    PhoneBook,
    PhoneBookEntry,
    load_phone_book,
    populate_from_guilds,
    save_phone_book,
    update_from_message,
)


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _stub_agent_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())


# ------------------------------------------------------------------
# PhoneBook unit tests
# ------------------------------------------------------------------


def test_add_new_entry() -> None:
    book = PhoneBook()
    changed = book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    assert changed is True
    assert "123" in book.entries
    assert book.entries["123"].name == "alice"


def test_add_same_entry_no_change() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    changed = book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    assert changed is False


def test_add_updates_name() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    changed = book.add(PhoneBookEntry(id="123", name="Alice K", kind="user"))
    assert changed is True
    assert book.entries["123"].name == "Alice K"


def test_lookup_by_id() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="456", name="bob", kind="user"))
    results = book.lookup("123")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_by_name_substring() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="456", name="bob", kind="user"))
    results = book.lookup("ali")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_case_insensitive() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="Alice", kind="user"))
    results = book.lookup("ALICE")
    assert len(results) == 1


def test_lookup_by_mention_format() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("<@123>")
    assert len(results) == 1
    assert results[0].name == "alice"


def test_lookup_by_mention_with_exclamation() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("<@!123>")
    assert len(results) == 1


def test_lookup_channel_mention() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="999", name="general", kind="channel"))
    results = book.lookup("<#999>")
    assert len(results) == 1
    assert results[0].name == "general"


def test_lookup_no_results() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    results = book.lookup("charlie")
    assert len(results) == 0


# ------------------------------------------------------------------
# Markdown serialization
# ------------------------------------------------------------------


def test_render_and_parse_roundtrip() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user", is_bot=False))
    book.add(PhoneBookEntry(id="456", name="bot-helper", kind="user", is_bot=True))
    book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))
    book.add(PhoneBookEntry(id="888", name="voice-chat", kind="channel", extra="voice"))

    md = book.render_markdown()
    parsed = PhoneBook.parse_markdown(md)

    assert len(parsed.entries) == 4
    assert parsed.entries["123"].name == "alice"
    assert parsed.entries["123"].is_bot is False
    assert parsed.entries["456"].is_bot is True
    assert parsed.entries["999"].kind == "channel"
    assert parsed.entries["999"].extra == "text"


def test_render_includes_mention_format() -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    md = book.render_markdown()
    assert "`<@123>`" in md


def test_render_includes_usage_instructions() -> None:
    book = PhoneBook()
    md = book.render_markdown()
    assert "<@USER_ID>" in md
    assert "lookup" in md.lower()


# ------------------------------------------------------------------
# File persistence
# ------------------------------------------------------------------


def test_save_and_load_phone_book(tmp_path: Path) -> None:
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))

    path = tmp_path / "state" / "phone-book.md"
    save_phone_book(book, path)

    loaded = load_phone_book(path)
    assert len(loaded.entries) == 2
    assert loaded.entries["123"].name == "alice"
    assert loaded.entries["999"].kind == "channel"


def test_load_nonexistent_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "does-not-exist.md"
    book = load_phone_book(path)
    assert len(book.entries) == 0


# ------------------------------------------------------------------
# Discord integration helpers
# ------------------------------------------------------------------


def test_populate_from_guilds() -> None:
    channel1 = SimpleNamespace(id=100, name="general", type="text")
    channel2 = SimpleNamespace(id=101, name="voice", type="voice")
    category = SimpleNamespace(id=102, name="Info", type="category")
    member1 = SimpleNamespace(id=200, display_name="alice", name="alice#1234", bot=False)
    member2 = SimpleNamespace(id=201, display_name="bot-helper", name="bot-helper#0000", bot=True)
    guild = SimpleNamespace(channels=[channel1, channel2, category], members=[member1, member2])

    book = PhoneBook()
    changed = populate_from_guilds(book, [guild])

    assert changed is True
    # Category should be excluded
    assert "102" not in book.entries
    # Channels
    assert book.entries["100"].name == "general"
    assert book.entries["100"].kind == "channel"
    assert book.entries["101"].name == "voice"
    # Users
    assert book.entries["200"].name == "alice"
    assert book.entries["200"].is_bot is False
    assert book.entries["201"].name == "bot-helper"
    assert book.entries["201"].is_bot is True


def test_populate_from_guilds_no_change_on_duplicate() -> None:
    channel = SimpleNamespace(id=100, name="general", type="text")
    guild = SimpleNamespace(channels=[channel], members=[])

    book = PhoneBook()
    populate_from_guilds(book, [guild])
    changed = populate_from_guilds(book, [guild])
    assert changed is False


def test_update_from_message() -> None:
    author = SimpleNamespace(id=300, display_name="carol", name="carol#5678", bot=False)
    book = PhoneBook()
    changed = update_from_message(book, author)
    assert changed is True
    assert book.entries["300"].name == "carol"


def test_update_from_message_with_mentions() -> None:
    """Verify that mentioned users can be individually added."""
    book = PhoneBook()
    mentioned = SimpleNamespace(id=400, display_name="dave", name="dave#0000", bot=False)
    changed = update_from_message(book, mentioned)
    assert changed is True
    assert book.entries["400"].name == "dave"


def test_update_from_message_none_author() -> None:
    book = PhoneBook()
    changed = update_from_message(book, None)
    assert changed is False


# ------------------------------------------------------------------
# Integration with OpenStrixApp
# ------------------------------------------------------------------


def test_app_initializes_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    assert hasattr(app, "phone_book")
    assert isinstance(app.phone_book, PhoneBook)


def test_app_loads_existing_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)

    # Pre-populate a phone book file
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    book = PhoneBook()
    book.add(PhoneBookEntry(id="123", name="alice", kind="user"))
    save_phone_book(book, state_dir / "phone-book.md")

    app = app_mod.OpenStrixApp(tmp_path)
    assert "123" in app.phone_book.entries
    assert app.phone_book.entries["123"].name == "alice"


@pytest.mark.asyncio
async def test_handle_discord_message_updates_phone_book(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        id = 555
        display_name = "eve"
        name = "eve#1234"
        bot = False

        def __str__(self) -> str:
            return "eve"

    class FakeChannel:
        def __init__(self) -> None:
            self.id = 999

    message = SimpleNamespace(
        id=12345,
        content="hello",
        channel=FakeChannel(),
        author=FakeAuthor(),
        attachments=[],
        mentions=[],
    )

    await app.handle_discord_message(message)

    # Author should be in the phone book now
    assert "555" in app.phone_book.entries
    assert app.phone_book.entries["555"].name == "eve"

    # Phone book should be persisted
    loaded = load_phone_book(app.layout.phone_book_file)
    assert "555" in loaded.entries


@pytest.mark.asyncio
async def test_handle_discord_message_captures_mentioned_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    class FakeAuthor:
        id = 555
        display_name = "eve"
        name = "eve#1234"
        bot = False

        def __str__(self) -> str:
            return "eve"

    class FakeMentionedUser:
        id = 666
        display_name = "frank"
        name = "frank#5678"
        bot = False

    message = SimpleNamespace(
        id=12345,
        content="hey <@666>",
        channel=SimpleNamespace(id=999),
        author=FakeAuthor(),
        attachments=[],
        mentions=[FakeMentionedUser()],
    )

    await app.handle_discord_message(message)

    assert "555" in app.phone_book.entries
    assert "666" in app.phone_book.entries
    assert app.phone_book.entries["666"].name == "frank"


def test_lookup_tool_is_registered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    tools = {tool.name: tool for tool in app._build_tools()}
    assert "lookup" in tools


def test_lookup_tool_finds_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.phone_book.add(PhoneBookEntry(id="123", name="alice", kind="user"))

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "alice"})
    assert "alice" in result
    assert "123" in result
    assert "<@123>" in result


def test_lookup_tool_finds_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)
    app.phone_book.add(PhoneBookEntry(id="999", name="general", kind="channel", extra="text"))

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "general"})
    assert "general" in result
    assert "999" in result
    assert "Channel" in result


def test_lookup_tool_no_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_agent_factory(monkeypatch)
    app = app_mod.OpenStrixApp(tmp_path)

    tools = {tool.name: tool for tool in app._build_tools()}
    result = tools["lookup"].invoke({"query": "nobody"})
    assert "No matches" in result


def test_phone_book_file_in_layout() -> None:
    from open_strix.config import RepoLayout

    layout = RepoLayout(home=Path("/fake"), state_dir_name="state")
    assert layout.phone_book_file == Path("/fake/state/phone-book.md")

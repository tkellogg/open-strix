from __future__ import annotations

import json
from pathlib import Path

from open_strix.ambient_memory import (
    ambient_record_id,
    build_ambient_corpus,
    chat_record_id,
    journal_record_id,
)
from open_strix.config import AmbientMemoryConfig, RepoLayout


def test_corpus_excludes_blocks_and_rendered_phone_book(tmp_path: Path) -> None:
    (tmp_path / "blocks").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "blocks" / "persona.yaml").write_text(
        "name: persona\ntext: visible block memory\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "phone-book.md").write_text(
        "# Phone Book\nvisible ids\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "note.md").write_text(
        "cold project fact",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    texts = "\n".join(record.text for record in records)
    paths = {record.path for record in records}
    assert "visible block memory" not in texts
    assert "visible ids" not in texts
    assert "state/note.md" in paths


def test_corpus_excludes_phone_book_extra_and_jsonl_notes(
    tmp_path: Path,
) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "phone-book.extra.md").write_text(
        "Alice prefers direct project summaries.",
        encoding="utf-8",
    )
    (tmp_path / "state" / "people.jsonl").write_text(
        json.dumps(
            {
                "name": "Alice",
                "discord_id": "123",
                "discord_display": "AliceD",
                "bluesky": "@alice.example",
                "notes": "Knows the budget history.",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "state" / "channels.jsonl").write_text(
        json.dumps(
            {
                "name": "ops",
                "discord_id": "999",
                "aliases": ["operations"],
                "notes": "Deployment decisions happen here.",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    text = "\n".join(record.text for record in records)
    assert "Alice prefers direct project summaries." not in text
    assert "Knows the budget history." not in text
    assert "Deployment decisions happen here." not in text
    assert "@alice.example" not in text
    assert "operations" not in text


def test_prompt_visible_chat_rows_are_filtered(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    row = {
        "type": "message",
        "source": "web",
        "channel_id": "local-web",
        "message_id": "m1",
        "timestamp": "2026-06-12T12:00:00+00:00",
        "author": "dagr",
        "content": "already visible chat",
    }
    (tmp_path / "logs" / "chat-history.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )

    visible_id = chat_record_id(row)
    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset({visible_id}),
    )

    assert all("already visible chat" not in record.text for record in records)


def test_chat_id_falls_back_when_message_id_is_none() -> None:
    row = {
        "type": "message",
        "source": "web",
        "channel_id": "local-web",
        "message_id": None,
        "timestamp": "2026-06-12T12:00:00+00:00",
        "author": "dagr",
        "content": "chat without platform id",
    }
    missing_id_row = dict(row)
    del missing_id_row["message_id"]

    record_id = chat_record_id(row)

    assert record_id == chat_record_id(missing_id_row)
    assert ":None:" not in record_id


def test_chat_records_skip_non_string_content(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    rows = [
        {"type": "message", "message_id": "none", "content": None},
        {"type": "message", "message_id": "dict", "content": {"nested": "value"}},
        {"type": "message", "message_id": "list", "content": ["value"]},
        {"type": "message", "message_id": "number", "content": 42},
        {"type": "message", "message_id": "empty", "content": "   "},
        {"type": "message", "message_id": "text", "content": "real chat text"},
    ]
    (tmp_path / "logs" / "chat-history.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    chat_texts = [record.text for record in records if record.source_type == "chat"]
    assert chat_texts == ["real chat text"]


def test_prompt_visible_journal_rows_are_filtered(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    row = {
        "timestamp": "2026-06-12T12:00:00+00:00",
        "user_wanted": "avoid duplicate prompt memory",
        "agent_did": "logged journal row",
    }
    (tmp_path / "logs" / "journal.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset({journal_record_id(row)}),
    )

    assert all("avoid duplicate prompt memory" not in record.text for record in records)


def test_jsonl_sources_skip_malformed_and_non_dict_rows(tmp_path: Path) -> None:
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "journal.jsonl").write_text(
        "\n".join(
            [
                "not json",
                json.dumps(["not", "a", "dict"]),
                json.dumps({"timestamp": "2026-06-12T12:00:00+00:00", "note": "kept"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    records = build_ambient_corpus(
        RepoLayout(home=tmp_path, state_dir_name="state"),
        AmbientMemoryConfig(),
        prompt_visible_ids=frozenset(),
    )

    journal_texts = [record.text for record in records if record.source_type == "journal"]
    assert journal_texts == [
        '{"note": "kept", "timestamp": "2026-06-12T12:00:00+00:00"}'
    ]


def test_journal_rows_have_stable_ids(tmp_path: Path) -> None:
    row = {
        "timestamp": "2026-06-12T12:00:00+00:00",
        "user_wanted": "review architecture",
        "agent_did": "wrote spec",
    }

    assert journal_record_id(row) == journal_record_id(dict(row))
    assert ambient_record_id("state", "state/a.md", 1, "hello") == ambient_record_id(
        "state", "state/a.md", 1, "hello"
    )

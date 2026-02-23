from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import open_strix.app as app_mod


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _extract_section(prompt: str, start: str, end: str) -> str:
    section = prompt.split(start, 1)[1]
    if end in section:
        return section.split(end, 1)[0].strip()
    return section.strip()


def test_prompt_includes_last_n_discord_messages_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
    app = app_mod.OpenStrixApp(tmp_path)

    for idx in range(1, 13):
        attachments = [f"state/attachments/{idx}.txt"] if idx == 12 else []
        app._remember_message(
            channel_id="123",
            message_id=str(idx),
            author=f"user-{idx}",
            content=f"discord-{idx}",
            attachment_names=attachments,
            source="discord",
        )

    for idx in range(1, 5):
        app._remember_message(
            channel_id="stdin",
            message_id=None,
            author=f"local-{idx}",
            content=f"stdin-{idx}",
            attachment_names=[],
            source="stdin",
        )

    prompt = app._render_prompt(
        app_mod.AgentEvent(
            event_type="discord_message",
            prompt="current",
            channel_id="123",
            author="alice",
        ),
    )

    messages_section = _extract_section(
        prompt,
        "3) Last Discord messages:\n",
        "4) Discord channel context:",
    )
    channel_context_section = _extract_section(
        prompt,
        "4) Discord channel context:\n",
        "5) Current message + reply channel:",
    )

    message_ids = re.findall(r"message_id=(\d+)", messages_section)
    assert message_ids == [str(i) for i in range(3, 13)]

    assert "stdin-1" not in messages_section
    assert "stdin-4" not in messages_section
    assert "attachments:\n  - state/attachments/12.txt" in messages_section

    first_line = messages_section.splitlines()[0]
    assert re.match(
        r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \(.+\) \| user-3 \| message_id=3$",
        first_line,
    )
    assert "channel_conversation_type: unknown" in channel_context_section
    assert "channel_visibility: unknown" in channel_context_section
    assert "channel_name: (none)" in channel_context_section
    assert "channel_id: 123" in channel_context_section


def test_journal_rendering_format_and_channel_id_autofill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
    app = app_mod.OpenStrixApp(tmp_path)
    app.current_channel_id = "777"
    (tmp_path / "blocks" / "voice.yaml").write_text(
        "name: style\nsort_order: 1\ntext: concise and practical\n",
        encoding="utf-8",
    )

    tools = {tool.name: tool for tool in app._build_tools()}
    tools["journal"].invoke(
        {
            "user_wanted": "Do one thing",
            "agent_did": "Did one thing",
            "predictions": "",
        },
    )
    tools["journal"].invoke(
        {
            "user_wanted": "Do another thing",
            "agent_did": "Did another thing",
            "predictions": "My aunt will appreciate it",
        },
    )

    prompt = app._render_prompt(
        app_mod.AgentEvent(
            event_type="discord_message",
            prompt="current",
            channel_id="777",
            author="alice",
            source_id="888",
        ),
    )

    journal_section = _extract_section(
        prompt,
        "1) Last journal entries:\n",
        "2) Memory blocks:",
    )
    memory_section = _extract_section(
        prompt,
        "2) Memory blocks:\n",
        "3) Last Discord messages:",
    )
    channel_context_section = _extract_section(
        prompt,
        "4) Discord channel context:\n",
        "5) Current message + reply channel:",
    )

    # Journal entries are key-value blocks separated by blank lines.
    assert "timestamp: " in journal_section
    assert "channel_id: 777" in journal_section
    assert "user_wanted: Do one thing" in journal_section
    assert "agent_did: Did one thing" in journal_section
    assert "user_wanted: Do another thing" in journal_section
    assert "agent_did: Did another thing" in journal_section

    # predictions is omitted for empty values and rendered as yaml-like list when present.
    assert "predictions:\n- My aunt will appreciate it" in journal_section
    assert "memory block: style\nconcise and practical" in memory_section
    assert "channel_id: 777" in channel_context_section

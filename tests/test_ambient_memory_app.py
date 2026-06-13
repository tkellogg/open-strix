from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import AIMessage

import open_strix.app as app_mod
from open_strix.ambient_memory import AmbientQueryContext, AmbientSnippet, format_ambient_injection
from open_strix.ambient_memory import chat_record_id
from open_strix.config import AmbientMemoryConfig


class CapturingAgent:
    def __init__(self) -> None:
        self.prompt = ""

    async def ainvoke(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.prompt = payload["messages"][0].content
        return {"messages": [AIMessage(content="ok")]}


class ReadyAmbientMemory:
    enabled = True

    def __init__(self) -> None:
        self.triggered_context: AmbientQueryContext | None = None

    def trigger(self, context: AmbientQueryContext) -> None:
        self.triggered_context = context

    def consume_ready(self, context: AmbientQueryContext) -> str | None:
        assert context == self.triggered_context
        return format_ambient_injection(
            [
                AmbientSnippet(
                    record_id="r1",
                    source="state/cold.md:1",
                    text="cold fact about the budget",
                    score=0.9,
                )
            ]
        )

    def observe_boundary(self, context: AmbientQueryContext) -> None:
        self.trigger(context)


@pytest.mark.asyncio
async def test_prompt_injects_ready_ambient_memory_after_hooks(tmp_path, monkeypatch) -> None:
    agent = CapturingAgent()
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: agent)
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.ambient_memory = AmbientMemoryConfig(enabled=True)
    app.ambient_memory = ReadyAmbientMemory()

    async def _append_prompt(event_type: str, event: dict[str, Any]) -> dict[str, Any]:
        assert event_type == "pre_prompt"
        updated = dict(event)
        updated["append_prompt"] = "hook-added context"
        return updated

    original_run_event = app.hooks.run_event
    app.hooks.run_event = _append_prompt  # type: ignore[method-assign]

    async def _noop_git(_event: Any) -> str:
        return "skip"

    app._run_post_turn_git_sync = _noop_git  # type: ignore[assignment]
    try:
        await app._process_event(
            app_mod.AgentEvent(
                event_type="web_message",
                prompt="what about the plan?",
                channel_id="local-web",
                author="local_user",
                source_id="m1",
            )
        )

        assert "hook-added context" in agent.prompt
        assert "Ambient memory surfaced" in agent.prompt
        assert agent.prompt.index("hook-added context") < agent.prompt.index("Ambient memory")
        assert app.ambient_memory.triggered_context is not None
    finally:
        app.hooks.run_event = original_run_event  # type: ignore[method-assign]
        await app.shutdown()


@pytest.mark.asyncio
async def test_render_prompt_reports_prompt_visible_chat_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: CapturingAgent())
    app = app_mod.OpenStrixApp(tmp_path)
    try:
        app._remember_message(
            channel_id="local-web",
            message_id="visible-1",
            author="dagr",
            content="already visible chat row",
            attachment_names=[],
            source="web",
        )

        _prompt, visible_ids = app._render_prompt_with_ambient_metadata(
            app_mod.AgentEvent(
                event_type="web_message",
                prompt="current",
                channel_id="local-web",
                author="local_user",
            )
        )

        assert chat_record_id(app.message_history_all[-1]) in visible_ids
    finally:
        await app.shutdown()


@pytest.mark.asyncio
async def test_rehydrated_null_message_id_uses_missing_id_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: CapturingAgent())
    (tmp_path / "logs").mkdir()
    row = {
        "type": "message",
        "source": "web",
        "channel_id": "local-web",
        "message_id": None,
        "timestamp": "2026-06-12T12:00:00+00:00",
        "author": "dagr",
        "content": "rehydrated visible chat row",
    }
    (tmp_path / "logs" / "chat-history.jsonl").write_text(
        json.dumps(row) + "\n",
        encoding="utf-8",
    )
    app = app_mod.OpenStrixApp(tmp_path)
    try:
        rehydrated = app.message_history_all[-1]
        _prompt, visible_ids = app._render_prompt_with_ambient_metadata(
            app_mod.AgentEvent(
                event_type="web_message",
                prompt="current",
                channel_id="local-web",
                author="local_user",
            )
        )

        assert rehydrated["message_id"] is None
        assert ":None:" not in chat_record_id(rehydrated)
        assert chat_record_id(rehydrated) in visible_ids
    finally:
        await app.shutdown()

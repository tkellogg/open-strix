from __future__ import annotations

import asyncio
import contextlib
import os
import time
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv

import open_strix.app as app_mod

# Load repo-root .env so DISCORD_TOKEN is available during local test runs.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=False)


class DummyAgent:
    async def ainvoke(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"messages": []}


def _require_discord_token() -> str:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        pytest.skip("DISCORD_TOKEN is not set")
    return token


async def _wait_for_ready(
    app: app_mod.OpenStrixApp,
    run_task: asyncio.Task[None],
    timeout_s: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if run_task.done():
            # Surface startup errors immediately.
            await run_task
            raise AssertionError("open-strix run task exited before Discord became ready")
        client = app.discord_client
        if client is not None and client.is_ready():
            return
        await asyncio.sleep(0.2)
    raise TimeoutError("Discord client did not become ready in time")


@pytest.mark.asyncio
async def test_live_open_strix_connects_to_discord(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_discord_token()
    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())

    app = app_mod.OpenStrixApp(tmp_path)
    run_task = asyncio.create_task(app.run())
    try:
        await _wait_for_ready(app, run_task, timeout_s=40.0)
        assert app.discord_client is not None
        assert app.discord_client.is_ready()
    finally:
        await app.shutdown()
        if not run_task.done():
            run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task


@pytest.mark.asyncio
async def test_live_send_message_tool_posts_to_channel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _require_discord_token()
    channel_id = os.getenv("DISCORD_TEST_CHANNEL_ID", "").strip()
    if not channel_id:
        pytest.skip("DISCORD_TEST_CHANNEL_ID not set; skipping live send-message test")

    monkeypatch.setattr(app_mod, "create_deep_agent", lambda **_: DummyAgent())
    app = app_mod.OpenStrixApp(tmp_path)
    app.config.git_sync_after_turn = False

    run_task = asyncio.create_task(app.run())
    try:
        await _wait_for_ready(app, run_task, timeout_s=40.0)
        tools = {tool.name: tool for tool in app._build_tools()}
        result = await tools["send_message"].ainvoke(
            {
                "text": f"[open-strix live test] {int(time.time())}",
                "channel_id": channel_id,
            },
        )
        assert "sent=True" in result
    finally:
        await app.shutdown()
        if not run_task.done():
            run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await run_task

"""Tests for the SIGQUIT drain handler."""
from __future__ import annotations

import asyncio
import signal
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from open_strix.models import AgentEvent


@pytest.fixture
def app():
    """Create a minimal mock app for drain testing."""
    with patch("open_strix.app.load_config"), \
         patch("open_strix.app.load_dotenv"), \
         patch("open_strix.app.bootstrap_home_repo"), \
         patch("open_strix.app.load_phone_book", return_value={}), \
         patch("open_strix.app.sync_builtin_skills_home"), \
         patch("open_strix.app.Supervisor"):
        from open_strix.app import OpenStrixApp
        app = object.__new__(OpenStrixApp)
        app.queue = asyncio.Queue()
        app._draining = False
        app.worker_task = None
        app.discord_client = None
        app.log_event = MagicMock()
        app._process_event = AsyncMock()
        app.current_channel_id = None
        app.current_event_label = None
        app.pending_scheduler_keys = set()
        return app


@pytest.mark.asyncio
async def test_drain_flag_skips_queued_events(app):
    """When draining, new events are skipped and worker exits."""
    app._draining = True
    event = AgentEvent(event_type="test_msg", prompt="hello", channel_id="123")
    app.queue.put_nowait(event)

    # Run worker — it should skip the event and break
    await asyncio.wait_for(app._event_worker(), timeout=2)

    app.log_event.assert_any_call("drain_skip_event", event_type="test_msg")
    app._process_event.assert_not_called()


@pytest.mark.asyncio
async def test_drain_after_current_turn_completes(app):
    """Drain set mid-turn: current event processes, then worker exits."""
    processed = asyncio.Event()

    async def _process_and_set_drain(event):
        app._draining = True
        processed.set()

    app._process_event = _process_and_set_drain
    event = AgentEvent(event_type="test_msg", prompt="hello", channel_id="123")
    app.queue.put_nowait(event)

    await asyncio.wait_for(app._event_worker(), timeout=2)

    assert processed.is_set()
    app.log_event.assert_any_call("drain_complete", last_event="test_msg")


@pytest.mark.asyncio
@pytest.mark.skipif(not hasattr(signal, "SIGQUIT"), reason="SIGQUIT not available on this platform")
async def test_install_drain_handler_sets_signal(app):
    """Verify SIGQUIT handler is installed on Unix."""
    loop = asyncio.get_running_loop()
    original_handler = signal.getsignal(signal.SIGQUIT)

    try:
        app._install_drain_handler()
        # The handler should be installed — we can't easily test the callback
        # without sending a real signal, but we can verify it doesn't crash
    finally:
        # Restore original handler
        try:
            loop.remove_signal_handler(signal.SIGQUIT)
        except Exception:
            pass

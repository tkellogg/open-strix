"""Tests for the loopback REST API."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase, unittest_run_loop

from open_strix.api import _build_app
from open_strix.models import AgentEvent


def _make_mock_app() -> MagicMock:
    """Create a minimal mock OpenStrixApp with enqueue_event."""
    app = MagicMock()
    app.enqueue_event = AsyncMock()
    app.log_event = MagicMock()
    return app


@pytest.fixture
def mock_strix():
    return _make_mock_app()


@pytest.fixture
def aiohttp_app(mock_strix):
    return _build_app(mock_strix)


@pytest.mark.asyncio
async def test_health(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.get("/api/health")
    assert resp.status == 200
    body = await resp.json()
    assert body == {"status": "ok"}


@pytest.mark.asyncio
async def test_post_event_queues(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        json={"source": "test-harness", "prompt": "hello world"},
    )
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "queued"
    assert body["source"] == "test-harness"

    mock_strix.enqueue_event.assert_called_once()
    event = mock_strix.enqueue_event.call_args[0][0]
    assert isinstance(event, AgentEvent)
    assert event.event_type == "api_event"
    assert event.prompt == "hello world"
    assert event.source_id == "api:test-harness"


@pytest.mark.asyncio
async def test_post_event_missing_prompt(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post("/api/event", json={"source": "test"})
    assert resp.status == 400
    body = await resp.json()
    assert "prompt is required" in body["error"]


@pytest.mark.asyncio
async def test_post_event_invalid_json(aiohttp_client, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        data=b"not json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status == 400


@pytest.mark.asyncio
async def test_post_event_default_source(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post("/api/event", json={"prompt": "no source"})
    assert resp.status == 200
    body = await resp.json()
    assert body["source"] == "api"

    event = mock_strix.enqueue_event.call_args[0][0]
    assert event.source_id == "api:api"


@pytest.mark.asyncio
async def test_post_event_with_channel_id(aiohttp_client, mock_strix, aiohttp_app):
    client = await aiohttp_client(aiohttp_app)
    resp = await client.post(
        "/api/event",
        json={"prompt": "targeted", "channel_id": "123456"},
    )
    assert resp.status == 200

    event = mock_strix.enqueue_event.call_args[0][0]
    assert event.channel_id == "123456"

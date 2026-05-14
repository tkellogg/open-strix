"""Conformance tests: LoggingWriteGuardBackend called through CompositeBackend.

These tests ensure that LoggingWriteGuardBackend implements every method with
the exact positional argument signatures that CompositeBackend uses. The bug
that killed Keel (PR #55) was **kwargs catching only keyword args while
CompositeBackend passes 3 positional args to grep_raw/agrep_raw.

If a signature mismatch exists, these tests explode with TypeError immediately.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from deepagents.backends.composite import CompositeBackend

from open_strix.readonly_backend import LoggingWriteGuardBackend


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a minimal project directory with files to search."""
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "test.md").write_text("hello world\nfoo bar\n")
    (tmp_path / "skills").mkdir()
    (tmp_path / "skills" / "example.py").write_text("import os\n")
    return tmp_path


@pytest.fixture
def events_log(tmp_path: Path) -> str:
    return str(tmp_path / "events.jsonl")


@pytest.fixture
def logging_backend(tmp_project: Path, events_log: str) -> LoggingWriteGuardBackend:
    return LoggingWriteGuardBackend(
        root_dir=tmp_project,
        writable_dirs=["state", "skills"],
        events_log_path=events_log,
        session_id="test-session",
    )


@pytest.fixture
def composite(logging_backend: LoggingWriteGuardBackend) -> CompositeBackend:
    """CompositeBackend with LoggingWriteGuardBackend as the default backend.

    This is the exact wiring that open-strix uses in app.py.
    """
    return CompositeBackend(default=logging_backend, routes={})


class TestGrepConformance:
    """Verify grep_raw/agrep_raw work when called through CompositeBackend.

    CompositeBackend calls backend.grep_raw(pattern, path, glob) with
    3 positional args. If the backend uses **kwargs, this explodes.
    """

    def test_grep_raw_3_positional_args(self, composite: CompositeBackend) -> None:
        """The exact call pattern from CompositeBackend line 260."""
        result = composite.grep_raw("hello", "/", None)
        assert not isinstance(result, str), f"grep_raw returned error: {result}"

    def test_grep_raw_with_path(self, composite: CompositeBackend) -> None:
        result = composite.grep_raw("hello", "/state/", None)
        assert not isinstance(result, str), f"grep_raw returned error: {result}"

    def test_grep_raw_with_glob(self, composite: CompositeBackend) -> None:
        result = composite.grep_raw("hello", "/", "*.md")
        assert not isinstance(result, str), f"grep_raw returned error: {result}"

    def test_grep_raw_no_path(self, composite: CompositeBackend) -> None:
        """None path triggers the 'search all backends' branch."""
        result = composite.grep_raw("hello", None, None)
        assert not isinstance(result, str), f"grep_raw returned error: {result}"

    def test_agrep_raw_3_positional_args(self, composite: CompositeBackend) -> None:
        """The exact call pattern from CompositeBackend line 291."""
        result = asyncio.get_event_loop().run_until_complete(
            composite.agrep_raw("hello", "/", None)
        )
        assert not isinstance(result, str), f"agrep_raw returned error: {result}"

    def test_agrep_raw_with_path(self, composite: CompositeBackend) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            composite.agrep_raw("hello", "/state/", None)
        )
        assert not isinstance(result, str), f"agrep_raw returned error: {result}"


class TestReadConformance:
    """Verify read/aread work through CompositeBackend."""

    def test_read_through_composite(self, composite: CompositeBackend) -> None:
        result = composite.read("/state/test.md")
        assert "hello world" in result

    def test_aread_through_composite(self, composite: CompositeBackend) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            composite.aread("/state/test.md")
        )
        assert "hello world" in result


class TestLsConformance:
    """Verify ls_info/als_info work through CompositeBackend."""

    def test_ls_root(self, composite: CompositeBackend) -> None:
        result = composite.ls_info("/")
        assert isinstance(result, list)

    def test_als_root(self, composite: CompositeBackend) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            composite.als_info("/")
        )
        assert isinstance(result, list)


class TestGlobConformance:
    """Verify glob_info/aglob_info work through CompositeBackend."""

    def test_glob_through_composite(self, composite: CompositeBackend) -> None:
        result = composite.glob_info("*.md", "/")
        assert isinstance(result, list)

    def test_aglob_through_composite(self, composite: CompositeBackend) -> None:
        result = asyncio.get_event_loop().run_until_complete(
            composite.aglob_info("*.md", "/")
        )
        assert isinstance(result, list)


class TestWriteConformance:
    """Verify write/edit work through CompositeBackend."""

    def test_write_through_composite(self, composite: CompositeBackend) -> None:
        result = composite.write("/state/new.md", "content")
        assert result.error is None

    def test_edit_through_composite(self, composite: CompositeBackend) -> None:
        result = composite.edit("/state/test.md", "hello", "goodbye")
        assert result.error is None


class TestExecuteConformance:
    """Verify execute works through CompositeBackend.

    Note: LoggingWriteGuardBackend doesn't implement SandboxBackendProtocol,
    so execute raises NotImplementedError. This is expected — execute is
    handled by the FilesystemBackend directly in production, not through
    the write guard layer.
    """

    def test_execute_not_supported(self, composite: CompositeBackend) -> None:
        with pytest.raises(NotImplementedError):
            composite.execute("echo hi")


class TestEventLogging:
    """Verify that events are logged correctly through the composite path."""

    def test_grep_logs_event(
        self, composite: CompositeBackend, events_log: str
    ) -> None:
        composite.grep_raw("hello", "/", None)
        with open(events_log) as f:
            events = [json.loads(line) for line in f]
        grep_events = [e for e in events if e.get("tool") == "grep"]
        assert len(grep_events) >= 1
        assert grep_events[0]["args"]["pattern"] == "hello"

    def test_read_logs_event(
        self, composite: CompositeBackend, events_log: str
    ) -> None:
        composite.read("/state/test.md")
        with open(events_log) as f:
            events = [json.loads(line) for line in f]
        read_events = [e for e in events if e.get("tool") == "read_file"]
        assert len(read_events) >= 1

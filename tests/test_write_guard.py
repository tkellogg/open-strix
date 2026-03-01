"""Tests for WriteGuardBackend path restrictions."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from open_strix.app import WriteGuardBackend


@pytest.fixture
def backend(tmp_path: Path) -> WriteGuardBackend:
    (tmp_path / "state").mkdir()
    (tmp_path / "skills").mkdir()
    return WriteGuardBackend(root_dir=tmp_path, writable_dirs=["state", "skills"])


class TestIsWriteAllowed:
    def test_state_file_allowed(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/state/foo.md") is True

    def test_state_nested_allowed(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/state/sub/deep/file.txt") is True

    def test_skills_file_allowed(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/skills/bluesky/SKILL.md") is True

    def test_skills_root_allowed(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/skills") is True

    def test_root_blocked(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/config.yaml") is False

    def test_blocks_blocked(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/blocks/persona.yaml") is False

    def test_scripts_blocked(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/scripts/pre_commit.py") is False

    def test_builtin_skills_blocked(self, backend: WriteGuardBackend) -> None:
        assert backend._is_write_allowed("/.open_strix_builtin_skills/memory/SKILL.md") is False


class TestWriteBlocked:
    def test_write_returns_error(self, backend: WriteGuardBackend) -> None:
        result = backend.write("/config.yaml", "bad")
        assert result.error is not None
        assert "blocked" in result.error.lower()

    def test_edit_returns_error(self, backend: WriteGuardBackend) -> None:
        result = backend.edit("/blocks/foo.yaml", "old", "new")
        assert result.error is not None
        assert "blocked" in result.error.lower()


class TestWriteAllowed:
    def test_write_state(self, backend: WriteGuardBackend) -> None:
        result = backend.write("/state/test.md", "hello")
        assert result.error is None

    def test_write_skills(self, backend: WriteGuardBackend) -> None:
        result = backend.write("/skills/my-skill/SKILL.md", "hello")
        assert result.error is None

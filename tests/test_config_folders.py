"""Tests for config-driven folder access."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from open_strix.config import (
    DEFAULT_FOLDERS,
    AppConfig,
    RepoLayout,
    _parse_folders,
    bootstrap_home_repo,
    load_config,
)
from open_strix.app import WriteGuardBackend
from open_strix.prompts import render_folders_section


class TestParseFolders:
    def test_defaults_when_none(self) -> None:
        assert _parse_folders(None) == DEFAULT_FOLDERS

    def test_defaults_when_not_dict(self) -> None:
        assert _parse_folders("bad") == DEFAULT_FOLDERS

    def test_defaults_when_empty_dict(self) -> None:
        assert _parse_folders({}) == DEFAULT_FOLDERS

    def test_basic_folders(self) -> None:
        raw = {"state": "rw", "data": "ro"}
        result = _parse_folders(raw)
        assert result == {"state": "rw", "data": "ro"}

    def test_invalid_mode_defaults_to_ro(self) -> None:
        raw = {"state": "rw", "data": "bad"}
        result = _parse_folders(raw)
        assert result == {"state": "rw", "data": "ro"}

    def test_case_insensitive_mode(self) -> None:
        raw = {"state": "RW", "data": "Ro"}
        result = _parse_folders(raw)
        assert result == {"state": "rw", "data": "ro"}

    def test_strips_whitespace(self) -> None:
        raw = {" state ": " rw "}
        result = _parse_folders(raw)
        assert result == {"state": "rw"}

    def test_custom_folders(self) -> None:
        raw = {"state": "rw", "skills": "rw", "research": "ro", "data": "rw"}
        result = _parse_folders(raw)
        assert result == {"state": "rw", "skills": "rw", "research": "ro", "data": "rw"}


class TestAppConfigFolders:
    def test_default_folders(self) -> None:
        config = AppConfig()
        assert config.folders == DEFAULT_FOLDERS
        assert config.web_ui_host == "127.0.0.1"
        assert config.web_ui_channel_id == "local-web"

    def test_writable_dirs(self) -> None:
        config = AppConfig(folders={"state": "rw", "skills": "rw", "blocks": "ro"})
        assert sorted(config.writable_dirs) == ["skills", "state"]

    def test_all_dirs(self) -> None:
        config = AppConfig(folders={"state": "rw", "skills": "rw", "blocks": "ro"})
        assert config.all_dirs == ["state", "skills", "blocks"]

    def test_writable_dirs_empty_when_all_ro(self) -> None:
        config = AppConfig(folders={"blocks": "ro", "logs": "ro"})
        assert config.writable_dirs == []


class TestLoadConfigFolders:
    def test_loads_folders_from_config(self, tmp_path: Path) -> None:
        config_data = {
            "model": "test-model",
            "folders": {"state": "rw", "data": "ro"},
            "web_ui_port": 8081,
            "web_ui_host": "0.0.0.0",
            "web_ui_channel_id": "local-web",
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.folders == {"state": "rw", "data": "ro"}
        assert config.web_ui_port == 8081
        assert config.web_ui_host == "0.0.0.0"
        assert config.web_ui_channel_id == "local-web"

    def test_defaults_when_no_folders_key(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.folders == DEFAULT_FOLDERS


class TestLoadConfigDisableBuiltinSkills:
    def test_loads_disable_list(self, tmp_path: Path) -> None:
        config_data = {
            "model": "test-model",
            "disable_builtin_skills": ["skill-acquisition", "prediction-review"],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.disable_builtin_skills == {"skill-acquisition", "prediction-review"}

    def test_empty_by_default(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.disable_builtin_skills == set()


class TestBootstrapCreatesFolders:
    def test_default_folders_created(self, tmp_path: Path) -> None:
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")
        for name in DEFAULT_FOLDERS:
            assert (tmp_path / name).is_dir(), f"{name}/ not created"
            assert (tmp_path / name / ".gitkeep").exists(), f"{name}/.gitkeep missing"

    def test_custom_folders_created(self, tmp_path: Path) -> None:
        # Pre-create config with custom folders.
        config_data = {
            "model": "test",
            "folders": {"state": "rw", "research": "ro", "data": "rw"},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")
        for name in ("state", "research", "data"):
            assert (tmp_path / name).is_dir(), f"{name}/ not created"

    def test_config_gets_folders_default(self, tmp_path: Path) -> None:
        # Bootstrap with no pre-existing config — should get folders in config.
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        bootstrap_home_repo(layout, checkpoint_text="test")
        loaded = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
        assert "folders" in loaded
        assert loaded["folders"] == DEFAULT_FOLDERS
        assert loaded["web_ui_host"] == "127.0.0.1"
        assert loaded["web_ui_channel_id"] == "local-web"


class TestWriteGuardFromConfig:
    def test_config_driven_writable(self, tmp_path: Path) -> None:
        config = AppConfig(folders={"state": "rw", "skills": "rw", "blocks": "ro"})
        (tmp_path / "state").mkdir()
        (tmp_path / "skills").mkdir()
        (tmp_path / "blocks").mkdir()
        backend = WriteGuardBackend(root_dir=tmp_path, writable_dirs=config.writable_dirs)
        assert backend._is_write_allowed("/state/foo.md") is True
        assert backend._is_write_allowed("/skills/bar.md") is True
        assert backend._is_write_allowed("/blocks/persona.yaml") is False

    def test_custom_rw_folder(self, tmp_path: Path) -> None:
        config = AppConfig(folders={"state": "rw", "research": "rw", "blocks": "ro"})
        (tmp_path / "state").mkdir()
        (tmp_path / "research").mkdir()
        backend = WriteGuardBackend(root_dir=tmp_path, writable_dirs=config.writable_dirs)
        assert backend._is_write_allowed("/research/notes.md") is True
        assert backend._is_write_allowed("/blocks/foo.yaml") is False

    def test_all_ro_blocks_everything(self, tmp_path: Path) -> None:
        config = AppConfig(folders={"blocks": "ro", "logs": "ro"})
        backend = WriteGuardBackend(root_dir=tmp_path, writable_dirs=config.writable_dirs)
        assert backend._is_write_allowed("/blocks/foo.yaml") is False
        assert backend._is_write_allowed("/logs/events.jsonl") is False


class TestRenderFoldersSection:
    def test_basic_rendering(self) -> None:
        folders = {"state": "rw", "blocks": "ro"}
        result = render_folders_section(folders)
        assert "`state/` (read-write)" in result
        assert "`blocks/` (read-only)" in result

    def test_empty_folders(self) -> None:
        assert render_folders_section({}) == ""

    def test_all_rw(self) -> None:
        folders = {"state": "rw", "skills": "rw"}
        result = render_folders_section(folders)
        assert "read-write" in result
        assert "read-only" not in result

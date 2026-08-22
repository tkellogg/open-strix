"""Tests for configurable subagents."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from open_strix.config import (
    AppConfig,
    RepoLayout,
    SubAgentConfig,
    _parse_subagent_configs,
    load_config,
)


class TestParseSubagentConfigs:
    def test_none_returns_empty(self) -> None:
        assert _parse_subagent_configs(None) == []

    def test_not_list_returns_empty(self) -> None:
        assert _parse_subagent_configs("bad") == []
        assert _parse_subagent_configs(42) == []

    def test_empty_list(self) -> None:
        assert _parse_subagent_configs([]) == []

    def test_skips_non_dict_items(self) -> None:
        assert _parse_subagent_configs(["bad", 42, None]) == []

    def test_skips_items_without_name(self) -> None:
        assert _parse_subagent_configs([{"description": "no name"}]) == []
        assert _parse_subagent_configs([{"name": "", "description": "empty name"}]) == []

    def test_basic_subagent(self) -> None:
        raw = [
            {
                "name": "vision",
                "description": "Describe images cheaply",
                "model": "anthropic:claude-haiku-3-5",
            }
        ]
        result = _parse_subagent_configs(raw)
        assert len(result) == 1
        assert result[0].name == "vision"
        assert result[0].description == "Describe images cheaply"
        assert result[0].model == "anthropic:claude-haiku-3-5"

    def test_defaults_for_optional_fields(self) -> None:
        raw = [{"name": "fast", "description": "Fast agent"}]
        result = _parse_subagent_configs(raw)
        assert len(result) == 1
        assert result[0].model == ""
        assert result[0].system_prompt == ""
        assert result[0].allowed_tools is None

    def test_multiple_subagents(self) -> None:
        raw = [
            {"name": "vision", "description": "Image tasks", "model": "anthropic:claude-haiku-3-5"},
            {"name": "fast", "description": "Quick tasks", "model": "anthropic:claude-haiku-3-5"},
        ]
        result = _parse_subagent_configs(raw)
        assert len(result) == 2
        assert result[0].name == "vision"
        assert result[1].name == "fast"

    def test_custom_system_prompt(self) -> None:
        raw = [
            {
                "name": "vision",
                "description": "Image tasks",
                "system_prompt": "You are a vision specialist.",
            }
        ]
        result = _parse_subagent_configs(raw)
        assert result[0].system_prompt == "You are a vision specialist."

    def test_strips_whitespace(self) -> None:
        raw = [{"name": "  vision  ", "description": "  Image tasks  ", "model": "  anthropic:claude-haiku-3-5  "}]
        result = _parse_subagent_configs(raw)
        assert result[0].name == "vision"
        assert result[0].description == "Image tasks"
        assert result[0].model == "anthropic:claude-haiku-3-5"


class TestAppConfigSubagents:
    def test_default_empty(self) -> None:
        config = AppConfig()
        assert config.subagents == []

    def test_with_subagents(self) -> None:
        config = AppConfig(
            subagents=[
                SubAgentConfig(name="vision", description="Image tasks", model="anthropic:claude-haiku-3-5"),
            ]
        )
        assert len(config.subagents) == 1
        assert config.subagents[0].name == "vision"


class TestLoadConfigSubagents:
    def test_loads_subagents_from_config(self, tmp_path: Path) -> None:
        config_data = {
            "model": "test-model",
            "subagents": [
                {
                    "name": "vision",
                    "description": "Cheap image description agent",
                    "model": "anthropic:claude-haiku-3-5",
                    "system_prompt": "Describe images concisely.",
                },
                {
                    "name": "fast",
                    "description": "Quick tasks with a cheap model",
                    "model": "anthropic:claude-haiku-3-5",
                },
            ],
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert len(config.subagents) == 2
        assert config.subagents[0].name == "vision"
        assert config.subagents[0].model == "anthropic:claude-haiku-3-5"
        assert config.subagents[0].system_prompt == "Describe images concisely."
        assert config.subagents[1].name == "fast"

    def test_no_subagents_key(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.subagents == []

    def test_invalid_subagents_value(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model", "subagents": "not-a-list"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data), encoding="utf-8")

        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.subagents == []


class TestBuildSubagentsAllowedTools:
    """Tests for open_strix.app.OpenStrixApp._build_subagents allowed_tools wiring.

    Regression coverage for the bug where a subagent's ``allowed_tools``
    config was parsed into ``SubAgentConfig`` but never passed through to the
    deepagents ``SubAgent`` spec, so every subagent silently inherited the
    full main-agent toolset (including e.g. ``send_message``) regardless of
    what the operator restricted it to.
    """

    @staticmethod
    def _make_app(tmp_path: Path, subagents: list[dict]) -> "app_mod.OpenStrixApp":
        import open_strix.app as app_mod

        config_data = {"model": "test-model", "subagents": subagents}
        (tmp_path / "config.yaml").write_text(yaml.safe_dump(config_data), encoding="utf-8")
        return app_mod.OpenStrixApp(tmp_path)

    @staticmethod
    def _fake_tool(name: str):
        from types import SimpleNamespace

        return SimpleNamespace(name=name)

    def test_no_allowed_tools_omits_tools_key(self, tmp_path: Path) -> None:
        app = self._make_app(
            tmp_path,
            [{"name": "fast", "description": "no restriction"}],
        )
        tools = [self._fake_tool("send_message"), self._fake_tool("read_file")]
        specs = app._build_subagents(tools)
        assert len(specs) == 1
        assert "tools" not in specs[0]

    def test_allowed_tools_resolves_to_tool_objects(self, tmp_path: Path) -> None:
        app = self._make_app(
            tmp_path,
            [
                {
                    "name": "researcher",
                    "description": "restricted research agent",
                    "allowed_tools": ["read_file", "web_search"],
                }
            ],
        )
        read_file_tool = self._fake_tool("read_file")
        web_search_tool = self._fake_tool("web_search")
        send_message_tool = self._fake_tool("send_message")
        tools = [read_file_tool, web_search_tool, send_message_tool]

        specs = app._build_subagents(tools)

        assert len(specs) == 1
        assert specs[0]["tools"] == [read_file_tool, web_search_tool]
        assert send_message_tool not in specs[0]["tools"]

    def test_empty_allowed_tools_list_produces_empty_tools(self, tmp_path: Path) -> None:
        app = self._make_app(
            tmp_path,
            [{"name": "sandboxed", "description": "no tools at all", "allowed_tools": []}],
        )
        tools = [self._fake_tool("read_file")]
        specs = app._build_subagents(tools)
        assert specs[0]["tools"] == []

    def test_unknown_tool_name_is_skipped_not_raised(self, tmp_path: Path) -> None:
        app = self._make_app(
            tmp_path,
            [
                {
                    "name": "researcher",
                    "description": "restricted",
                    "allowed_tools": ["read_file", "does_not_exist"],
                }
            ],
        )
        read_file_tool = self._fake_tool("read_file")
        specs = app._build_subagents([read_file_tool])
        assert specs[0]["tools"] == [read_file_tool]

    def test_multiple_subagents_independent_tool_sets(self, tmp_path: Path) -> None:
        app = self._make_app(
            tmp_path,
            [
                {
                    "name": "researcher",
                    "description": "read-only",
                    "allowed_tools": ["read_file"],
                },
                {
                    "name": "unrestricted",
                    "description": "inherits everything",
                },
            ],
        )
        read_file_tool = self._fake_tool("read_file")
        send_message_tool = self._fake_tool("send_message")
        specs = app._build_subagents([read_file_tool, send_message_tool])

        assert specs[0]["tools"] == [read_file_tool]
        assert "tools" not in specs[1]

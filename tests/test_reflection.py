"""Tests for reflection (dissonance detection) feature."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from open_strix.config import (
    AppConfig,
    ReflectionConfig,
    RepoLayout,
    _parse_reflection,
    load_config,
)
from open_strix.reflection import (
    DISSONANCE_EMOJI,
    ReflectionHook,
    _build_reflection_prompt,
    _read_questions_file,
)


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------

class TestParseReflection:
    def test_defaults_when_none(self) -> None:
        result = _parse_reflection(None)
        assert result.enabled is False
        assert result.questions_file == "state/is-dissonant-prompt.md"

    def test_defaults_when_not_dict(self) -> None:
        result = _parse_reflection("bad")
        assert result.enabled is False

    def test_enabled_true(self) -> None:
        result = _parse_reflection({"enabled": True})
        assert result.enabled is True

    def test_enabled_false(self) -> None:
        result = _parse_reflection({"enabled": False})
        assert result.enabled is False

    def test_custom_questions_file(self) -> None:
        result = _parse_reflection({"questions_file": "custom/path.md"})
        assert result.questions_file == "custom/path.md"

    def test_strips_whitespace(self) -> None:
        result = _parse_reflection({"questions_file": "  state/my-file.md  "})
        assert result.questions_file == "state/my-file.md"


class TestAppConfigReflection:
    def test_default_reflection(self) -> None:
        config = AppConfig()
        assert config.reflection.enabled is False
        assert config.reflection.questions_file == "state/is-dissonant-prompt.md"

    def test_custom_reflection(self) -> None:
        config = AppConfig(
            reflection=ReflectionConfig(enabled=True, questions_file="custom.md"),
        )
        assert config.reflection.enabled is True
        assert config.reflection.questions_file == "custom.md"


class TestLoadConfigReflection:
    def test_loads_reflection_from_config(self, tmp_path: Path) -> None:
        config_data = {
            "model": "test-model",
            "reflection": {
                "enabled": True,
                "questions_file": "state/my-criteria.md",
            },
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data))
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.reflection.enabled is True
        assert config.reflection.questions_file == "state/my-criteria.md"

    def test_missing_reflection_uses_defaults(self, tmp_path: Path) -> None:
        config_data = {"model": "test-model"}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump(config_data))
        layout = RepoLayout(home=tmp_path, state_dir_name="state")
        config = load_config(layout)
        assert config.reflection.enabled is False


# ---------------------------------------------------------------------------
# Questions file reading
# ---------------------------------------------------------------------------

class TestReadQuestionsFile:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir()
        questions_path = tmp_path / "state" / "questions.md"
        questions_path.write_text("# My criteria\nCheck for X.")
        result = _read_questions_file(tmp_path, "state/questions.md")
        assert result == "# My criteria\nCheck for X."

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        result = _read_questions_file(tmp_path, "state/missing.md")
        assert result is None

    def test_returns_none_for_empty_file(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir()
        questions_path = tmp_path / "state" / "questions.md"
        questions_path.write_text("")
        result = _read_questions_file(tmp_path, "state/questions.md")
        assert result is None


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

class TestBuildReflectionPrompt:
    def test_includes_criteria_and_message(self) -> None:
        prompt = _build_reflection_prompt("Hello world", "# Criteria\nCheck for X.")
        assert "# Criteria" in prompt
        assert "Check for X." in prompt
        assert "Hello world" in prompt
        assert "is_dissonant" in prompt


# ---------------------------------------------------------------------------
# ReflectionHook
# ---------------------------------------------------------------------------

class TestReflectionHook:
    def _make_hook(
        self,
        tmp_path: Path,
        *,
        questions_content: str = "# Criteria\nCheck stuff.",
    ) -> tuple[ReflectionHook, MagicMock, AsyncMock]:
        (tmp_path / "state").mkdir(exist_ok=True)
        questions_path = tmp_path / "state" / "is-dissonant-prompt.md"
        questions_path.write_text(questions_content)

        log_fn = MagicMock()
        react_fn = AsyncMock(return_value=True)
        hook = ReflectionHook(
            home=tmp_path,
            questions_file="state/is-dissonant-prompt.md",
            model="test-model",
            log_fn=log_fn,
            react_fn=react_fn,
        )
        return hook, log_fn, react_fn

    @pytest.mark.asyncio
    async def test_skips_empty_text(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        await hook.on_message_sent(text="", channel_id="123", message_id="456")
        log_fn.assert_not_called()
        react_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_no_message_id(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        await hook.on_message_sent(text="hello", channel_id="123", message_id=None)
        log_fn.assert_not_called()
        react_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_missing_questions_file(self, tmp_path: Path) -> None:
        log_fn = MagicMock()
        react_fn = AsyncMock()
        hook = ReflectionHook(
            home=tmp_path,
            questions_file="state/nonexistent.md",
            model="test-model",
            log_fn=log_fn,
            react_fn=react_fn,
        )
        await hook.on_message_sent(text="hello", channel_id="123", message_id="456")
        log_fn.assert_called_once()
        assert log_fn.call_args[0][0] == "reflection_skip"

    @pytest.mark.asyncio
    async def test_evaluate_dissonance_detected(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        mock_result = {
            "yes": True,
            "pattern_type": "service_wrapup",
            "suggestion": "Drop the trailing question",
            "confidence": 0.85,
        }
        with patch(
            "open_strix.reflection._run_reflection_sync",
            return_value=mock_result,
        ):
            await hook._evaluate("Does this help?", "123", "456", "# Criteria")
            # Wait for any pending tasks
            await asyncio.sleep(0.01)

        # Should have logged both reflection_check and reflection_dissonance
        event_types = [call[0][0] for call in log_fn.call_args_list]
        assert "reflection_check" in event_types
        assert "reflection_dissonance" in event_types
        react_fn.assert_called_once_with(
            channel_id="123",
            message_id="456",
            emoji=DISSONANCE_EMOJI,
        )

    @pytest.mark.asyncio
    async def test_evaluate_no_dissonance(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        mock_result = {
            "yes": False,
            "pattern_type": "",
            "suggestion": "",
            "confidence": 0.1,
        }
        with patch(
            "open_strix.reflection._run_reflection_sync",
            return_value=mock_result,
        ):
            await hook._evaluate("The answer is 42.", "123", "456", "# Criteria")
            await asyncio.sleep(0.01)

        event_types = [call[0][0] for call in log_fn.call_args_list]
        assert "reflection_check" in event_types
        assert "reflection_dissonance" not in event_types
        react_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_low_confidence_no_react(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        mock_result = {
            "yes": True,
            "pattern_type": "hollow_validation",
            "suggestion": "Add substance",
            "confidence": 0.5,
        }
        with patch(
            "open_strix.reflection._run_reflection_sync",
            return_value=mock_result,
        ):
            await hook._evaluate("That makes sense.", "123", "456", "# Criteria")
            await asyncio.sleep(0.01)

        event_types = [call[0][0] for call in log_fn.call_args_list]
        assert "reflection_check" in event_types
        assert "reflection_dissonance" not in event_types
        react_fn.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_none_result(self, tmp_path: Path) -> None:
        hook, log_fn, react_fn = self._make_hook(tmp_path)
        with patch(
            "open_strix.reflection._run_reflection_sync",
            return_value=None,
        ):
            await hook._evaluate("Hello.", "123", "456", "# Criteria")
            await asyncio.sleep(0.01)

        event_types = [call[0][0] for call in log_fn.call_args_list]
        assert "reflection_check" in event_types
        assert "reflection_dissonance" not in event_types
        react_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Builtin skill discovery
# ---------------------------------------------------------------------------

class TestDissonanceSkillDiscovery:
    def test_dissonance_skill_in_builtin_skills(self) -> None:
        from open_strix.builtin_skills import BUILTIN_SKILL_FILES

        skill_paths = [p for p in BUILTIN_SKILL_FILES if "dissonance" in p]
        assert any("dissonance/SKILL.md" in p for p in skill_paths)

    def test_default_questions_in_builtin_skills(self) -> None:
        from open_strix.builtin_skills import BUILTIN_SKILLS

        assert "dissonance/default-questions.md" in BUILTIN_SKILLS
        content = BUILTIN_SKILLS["dissonance/default-questions.md"]
        assert "Dissonance Criteria" in content

from __future__ import annotations

from pathlib import Path

import yaml

from open_strix.config import (
    AmbientMemoryConfig,
    AppConfig,
    RepoLayout,
    load_config,
)


def test_ambient_memory_defaults_disabled() -> None:
    config = AppConfig()

    assert isinstance(config.ambient_memory, AmbientMemoryConfig)
    assert config.ambient_memory.enabled is False
    assert config.ambient_memory.backend == "pylate"
    assert config.ambient_memory.model == "lightonai/GTE-ModernColBERT-v1"
    assert config.ambient_memory.index_dir == "logs/ambient-memory"
    assert config.ambient_memory.include_events is False
    assert config.ambient_memory.prompt_injection is True
    assert config.ambient_memory.tool_result_injection is True


def test_load_config_parses_ambient_memory_section(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "test-model",
                "ambient_memory": {
                    "enabled": True,
                    "backend": "pylate",
                    "model": "custom/model",
                    "index_dir": "logs/custom-ambient",
                    "include_state": False,
                    "include_journal": False,
                    "include_chat_history": True,
                    "include_events": True,
                    "max_results": 5,
                    "snippet_words": 9,
                    "min_score": 0.25,
                    "candidate_limit": 50,
                    "refresh_interval_seconds": 60,
                    "retrieval_timeout_seconds": 3,
                    "indexing_timeout_seconds": 30,
                    "result_ttl_seconds": 12,
                    "max_tool_result_bytes": 1234,
                    "prompt_injection": False,
                    "tool_result_injection": False,
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(RepoLayout(home=tmp_path, state_dir_name="state"))

    ambient = config.ambient_memory
    assert ambient.enabled is True
    assert ambient.backend == "pylate"
    assert ambient.model == "custom/model"
    assert ambient.index_dir == "logs/custom-ambient"
    assert ambient.include_state is False
    assert ambient.include_journal is False
    assert ambient.include_chat_history is True
    assert ambient.include_events is True
    assert ambient.max_results == 5
    assert ambient.snippet_words == 9
    assert ambient.min_score == 0.25
    assert ambient.candidate_limit == 50
    assert ambient.refresh_interval_seconds == 60
    assert ambient.retrieval_timeout_seconds == 3
    assert ambient.indexing_timeout_seconds == 30
    assert ambient.result_ttl_seconds == 12
    assert ambient.max_tool_result_bytes == 1234
    assert ambient.prompt_injection is False
    assert ambient.tool_result_injection is False


def test_load_config_rejects_unknown_backend_by_disabling(tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": "test-model",
                "ambient_memory": {
                    "enabled": True,
                    "backend": "unknown",
                },
            },
        ),
        encoding="utf-8",
    )

    config = load_config(RepoLayout(home=tmp_path, state_dir_name="state"))

    assert config.ambient_memory.enabled is False
    assert config.ambient_memory.backend == "pylate"

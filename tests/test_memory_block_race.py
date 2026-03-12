"""Test that _load_memory_blocks handles TOCTOU race with concurrent block deletion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from open_strix import app as app_mod


def _make_app(tmp_path: Path) -> app_mod.OpenStrixApp:
    (tmp_path / "config.yaml").write_text("model: test-model\n", encoding="utf-8")
    return app_mod.OpenStrixApp(tmp_path)


def test_load_memory_blocks_survives_concurrent_deletion(tmp_path: Path) -> None:
    """Simulate TOCTOU race: a block file exists during glob but is deleted before read."""
    app = _make_app(tmp_path)
    blocks_dir = app.layout.blocks_dir

    # Create two blocks — one real, one we'll "delete" between glob and read.
    real_block = blocks_dir / "real.yaml"
    real_block.write_text(
        yaml.safe_dump({"name": "real", "text": "I exist", "sort_order": 0}),
        encoding="utf-8",
    )
    ghost_block = blocks_dir / "ghost.yaml"
    ghost_block.write_text(
        yaml.safe_dump({"name": "ghost", "text": "I will vanish", "sort_order": 0}),
        encoding="utf-8",
    )

    # Monkey-patch _iter_block_files to include the ghost, then delete it before read.
    original_iter = app._iter_block_files

    def iter_then_delete() -> list[Path]:
        files = original_iter()
        # Simulate concurrent deletion after glob returns but before read_text.
        ghost_block.unlink(missing_ok=True)
        return files

    with patch.object(app, "_iter_block_files", side_effect=iter_then_delete):
        blocks = app._load_memory_blocks()

    # Should get the real block, ghost should be silently skipped.
    block_ids = [b["id"] for b in blocks]
    assert "real" in block_ids
    assert "ghost" not in block_ids


def test_load_memory_blocks_returns_all_when_no_race(tmp_path: Path) -> None:
    """Baseline: all blocks load normally when no deletion occurs."""
    app = _make_app(tmp_path)
    blocks_dir = app.layout.blocks_dir

    for name in ("alpha", "beta"):
        (blocks_dir / f"{name}.yaml").write_text(
            yaml.safe_dump({"name": name, "text": f"block {name}", "sort_order": 0}),
            encoding="utf-8",
        )

    blocks = app._load_memory_blocks()
    block_ids = {b["id"] for b in blocks}
    # init block is created by bootstrap, plus our two.
    assert "alpha" in block_ids
    assert "beta" in block_ids

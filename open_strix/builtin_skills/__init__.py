from __future__ import annotations

import hashlib
from importlib.abc import Traversable
from importlib import resources
import json
from pathlib import Path
import shutil
import tempfile

BUILTIN_HOME_DIRNAME = ".open_strix_builtin_skills"


def _iter_files(root: Traversable, *, prefix: str) -> list[str]:
    files: list[str] = []
    for entry in root.iterdir():
        rel_path = f"{prefix}/{entry.name}"
        if entry.is_dir():
            files.extend(_iter_files(entry, prefix=rel_path))
            continue
        if entry.is_file():
            files.append(rel_path)
    return files


def _discover_builtin_skill_files() -> tuple[str, ...]:
    package_root = resources.files(__name__)
    files: list[str] = []
    for entry in sorted(package_root.iterdir(), key=lambda child: child.name):
        if not entry.is_dir():
            continue
        if entry.name == "scripts":
            files.extend(_iter_files(entry, prefix=entry.name))
            continue
        if entry.joinpath("SKILL.md").is_file():
            files.extend(_iter_files(entry, prefix=entry.name))
    if not files:
        raise RuntimeError("no built-in assets discovered")
    return tuple(sorted(files))


BUILTIN_SKILL_FILES: tuple[str, ...] = _discover_builtin_skill_files()


def _read_resource_text(rel_path: str) -> str:
    target = resources.files(__name__)
    for part in rel_path.split("/"):
        target = target.joinpath(part)
    if not target.is_file():
        raise RuntimeError(f"missing built-in skill asset: {rel_path}")
    return target.read_text(encoding="utf-8")


def _load_builtin_skills() -> dict[str, str]:
    return {
        rel_path: _read_resource_text(rel_path)
        for rel_path in BUILTIN_SKILL_FILES
    }


BUILTIN_SKILLS: dict[str, str] = _load_builtin_skills()

def _write_builtin_tree(root: Path, *, overwrite: bool) -> None:
    for rel_path, content in BUILTIN_SKILLS.items():
        target = root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if not overwrite and target.exists() and target.read_text(encoding="utf-8") == content:
            continue
        target.write_text(content, encoding="utf-8")


def materialize_builtin_skills() -> Path:
    payload = json.dumps(BUILTIN_SKILLS, sort_keys=True, ensure_ascii=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    root = Path(tempfile.gettempdir()) / "open-strix" / "builtin-skills" / digest
    root.mkdir(parents=True, exist_ok=True)

    _write_builtin_tree(root, overwrite=False)
    return root


def sync_builtin_skills_home(home: Path) -> Path:
    root = home / BUILTIN_HOME_DIRNAME
    if root.is_file() or root.is_symlink():
        root.unlink()
    elif root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    _write_builtin_tree(root, overwrite=True)
    return root

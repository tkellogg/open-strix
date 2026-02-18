from __future__ import annotations

import argparse
import configparser
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def _read_token_from_pypirc(path: Path) -> str | None:
    if not path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section("pypi"):
        return None

    password = parser.get("pypi", "password", fallback="").strip()
    if password.startswith("pypi-"):
        return password
    return None


def _resolve_publish_token(pypirc_path: Path) -> tuple[str | None, str]:
    env_token = os.getenv("UV_PUBLISH_TOKEN", "").strip()
    if env_token:
        return env_token, "UV_PUBLISH_TOKEN"

    pypirc_token = _read_token_from_pypirc(pypirc_path)
    if pypirc_token:
        return pypirc_token, str(pypirc_path)

    return None, ""


def _run_cmd(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> None:
    rendered = " ".join(shlex.quote(part) for part in cmd)
    print(f"+ {rendered}")
    if dry_run:
        return
    subprocess.run(cmd, check=True, env=env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release",
        description="Build and publish open-strix to PyPI using UV_PUBLISH_TOKEN.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="Skip `uv build` and publish existing artifacts in dist/.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing them.",
    )
    parser.add_argument(
        "--pypirc",
        default=str(Path.home() / ".pypirc"),
        help="Path to read token fallback from if UV_PUBLISH_TOKEN is unset.",
    )
    known, publish_args = parser.parse_known_args(argv)

    token, token_source = _resolve_publish_token(Path(known.pypirc).expanduser())
    if not token:
        print(
            "Missing UV_PUBLISH_TOKEN and no token found in ~/.pypirc [pypi] password.",
            file=sys.stderr,
        )
        return 2

    print(f"Using UV_PUBLISH_TOKEN from {token_source}.")
    env = os.environ.copy()
    env["UV_PUBLISH_TOKEN"] = token

    if not known.no_build:
        if known.dry_run:
            print("+ rm -rf dist")
        else:
            shutil.rmtree("dist", ignore_errors=True)
        _run_cmd(["uv", "build"], dry_run=known.dry_run)

    _run_cmd(
        ["uv", "publish", *publish_args],
        env=env,
        dry_run=known.dry_run,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

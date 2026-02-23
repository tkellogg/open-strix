from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import subprocess
from typing import Iterable

import yaml


@dataclass(frozen=True)
class HistorySeries:
    dates: list[date]
    snapshots: list[dict[str, int]]


def _default_output_path(repo_root: Path) -> Path:
    today = date.today().isoformat()
    return repo_root / "state" / "dashboards" / f"{today}-memory.png"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render memory block size dashboard from current state + git history.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Agent home repo path. Defaults to current working directory.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output PNG path. Defaults to state/dashboards/{YYYY-MM-DD}-memory.png.",
    )
    return parser


def _iter_memory_block_paths(blocks_dir: Path) -> Iterable[Path]:
    files = list(blocks_dir.glob("*.yaml"))
    files.extend(blocks_dir.glob("*.yml"))
    return sorted(files)


def _extract_memory_text_len(raw: str) -> int:
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError:
        return len(raw)
    if isinstance(parsed, dict):
        text = parsed.get("text")
        if text is not None:
            return len(str(text))
    return len(raw)


def _load_current_block_sizes(repo_root: Path) -> dict[str, int]:
    blocks_dir = repo_root / "blocks"
    if not blocks_dir.exists():
        return {}
    sizes: dict[str, int] = {}
    for path in _iter_memory_block_paths(blocks_dir):
        sizes[path.stem] = _extract_memory_text_len(path.read_text(encoding="utf-8"))
    return sizes


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )


def _git_commit_days(repo_root: Path) -> list[tuple[str, date]]:
    proc = _run_git(
        repo_root,
        ["log", "--reverse", "--date=short", "--pretty=%H|%ad", "--", "blocks"],
    )
    if proc.returncode != 0:
        return []

    commits: list[tuple[str, date]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        commit, raw_day = line.split("|", maxsplit=1)
        commits.append((commit, date.fromisoformat(raw_day)))
    return commits


def _snapshot_for_commit(repo_root: Path, commit: str) -> dict[str, int]:
    ls_proc = _run_git(
        repo_root,
        ["ls-tree", "-r", "--name-only", commit, "--", "blocks"],
    )
    if ls_proc.returncode != 0:
        return {}

    sizes: dict[str, int] = {}
    for rel_path in ls_proc.stdout.splitlines():
        rel_path = rel_path.strip()
        if not rel_path.endswith((".yaml", ".yml")):
            continue
        show_proc = _run_git(repo_root, ["show", f"{commit}:{rel_path}"])
        if show_proc.returncode != 0:
            continue
        sizes[Path(rel_path).stem] = _extract_memory_text_len(show_proc.stdout)
    return sizes


def _load_history_series(repo_root: Path) -> HistorySeries:
    commits = _git_commit_days(repo_root)
    if not commits:
        return HistorySeries(dates=[], snapshots=[])

    day_snapshots: dict[date, dict[str, int]] = {}
    for commit, commit_day in commits:
        day_snapshots[commit_day] = _snapshot_for_commit(repo_root, commit)

    start_day = min(day_snapshots)
    end_day = date.today()
    running: dict[str, int] = {}
    days: list[date] = []
    snapshots: list[dict[str, int]] = []
    cursor = start_day
    while cursor <= end_day:
        if cursor in day_snapshots:
            running = day_snapshots[cursor]
        days.append(cursor)
        snapshots.append(dict(running))
        cursor = date.fromordinal(cursor.toordinal() + 1)

    return HistorySeries(dates=days, snapshots=snapshots)


def _plot_dashboard(
    *,
    repo_root: Path,
    output_path: Path,
    current_sizes: dict[str, int],
    history: HistorySeries,
) -> None:
    try:
        import matplotlib
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "matplotlib is required for memory dashboard plotting. Install with: uv add matplotlib",
        ) from exc

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    from matplotlib import pyplot as plt

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 1, figsize=(14, 10), constrained_layout=True)
    ax_current, ax_history = axes

    if current_sizes:
        pairs = sorted(current_sizes.items(), key=lambda row: row[1], reverse=True)
        labels = [row[0] for row in pairs]
        values = [row[1] for row in pairs]
        ax_current.bar(labels, values)
        ax_current.set_ylabel("Chars")
        ax_current.set_title("Current Memory Block Sizes")
        ax_current.tick_params(axis="x", rotation=35)
    else:
        ax_current.text(0.5, 0.5, "No memory blocks found in blocks/", ha="center", va="center")
        ax_current.set_title("Current Memory Block Sizes")
        ax_current.set_xticks([])
        ax_current.set_yticks([])

    all_block_ids = sorted({block_id for snapshot in history.snapshots for block_id in snapshot})
    if history.dates and all_block_ids:
        for block_id in all_block_ids:
            points = [snapshot.get(block_id, 0) for snapshot in history.snapshots]
            if not any(points):
                continue
            ax_history.plot(history.dates, points, linewidth=1.75, label=block_id)
        ax_history.set_ylabel("Chars")
        ax_history.set_title("Day-over-Day Memory Block Sizes (Git History)")
        ax_history.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=12))
        ax_history.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
        ax_history.tick_params(axis="x", rotation=35)
        ax_history.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), fontsize=8)
    else:
        ax_history.text(
            0.5,
            0.5,
            "No git history found for blocks/*.yml*",
            ha="center",
            va="center",
        )
        ax_history.set_title("Day-over-Day Memory Block Sizes (Git History)")
        ax_history.set_xticks([])
        ax_history.set_yticks([])

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fig.suptitle(f"Memory Dashboard for {repo_root.name} ({generated_at})", fontsize=14)
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _render_text_report(
    *,
    output_path: Path,
    current_sizes: dict[str, int],
    history: HistorySeries,
) -> str:
    lines: list[str] = []
    lines.append(f"output_file: {output_path}")
    lines.append("Current memory block sizes (chars):")
    if current_sizes:
        for block_id, size in sorted(current_sizes.items(), key=lambda row: row[1], reverse=True):
            lines.append(f"- {block_id}: {size}")
    else:
        lines.append("- none")

    lines.append("History summary:")
    lines.append(f"- days_covered: {len(history.dates)}")
    lines.append(f"- snapshots: {len(history.snapshots)}")
    tracked_block_ids = sorted({block_id for snapshot in history.snapshots for block_id in snapshot})
    if tracked_block_ids:
        lines.append(f"- tracked_blocks: {', '.join(tracked_block_ids)}")
        latest = history.snapshots[-1]
        lines.append("Latest day sizes (chars):")
        for block_id in tracked_block_ids:
            lines.append(f"- {block_id}: {latest.get(block_id, 0)}")
    else:
        lines.append("- tracked_blocks: none")
    return "\n".join(lines)


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.exists():
        raise SystemExit(f"repo root does not exist: {repo_root}")

    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = (repo_root / output_path).resolve()
    else:
        output_path = _default_output_path(repo_root)

    current_sizes = _load_current_block_sizes(repo_root)
    history = _load_history_series(repo_root)
    _plot_dashboard(
        repo_root=repo_root,
        output_path=output_path,
        current_sizes=current_sizes,
        history=history,
    )
    print(f"wrote memory dashboard: {output_path}")
    print(_render_text_report(output_path=output_path, current_sizes=current_sizes, history=history))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import json
from pathlib import Path
import re
from typing import Any


def _default_output_path(repo_root: Path) -> Path:
    today = date.today().isoformat()
    return repo_root / "state" / "dashboards" / f"{today}-file-frequency-report.json"


def _default_plot_path(repo_root: Path) -> Path:
    today = date.today().isoformat()
    return repo_root / "state" / "dashboards" / f"{today}-file-frequency-scatter.png"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Report frequently accessed files from logs/events.jsonl, grouped by session_id.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Agent home repo path. Defaults to current working directory.",
    )
    parser.add_argument(
        "--events-file",
        default="logs/events.jsonl",
        help="Events JSONL path, relative to repo root by default.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output JSON path. Defaults to state/dashboards/{YYYY-MM-DD}-file-frequency-report.json.",
    )
    parser.add_argument(
        "--plot-output",
        default=None,
        help="Output PNG path. Defaults to state/dashboards/{YYYY-MM-DD}-file-frequency-scatter.png.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="How many top files to include for overall and each session.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Optional session_id filter. If set, only that session is reported.",
    )
    parser.add_argument(
        "--heatmap-top",
        type=int,
        default=20,
        help="How many top files to include on heatmap axes.",
    )
    return parser


def _is_path_like(value: str) -> bool:
    raw = value.strip()
    if not raw:
        return False
    lowered = raw.lower()
    if lowered.startswith(("http://", "https://")):
        return False
    if "://" in raw:
        return False
    return "/" in raw or "\\" in raw


def _normalize_path(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    normalized = re.sub(r"/+", "/", normalized)
    # Most event paths are virtual paths from repo root (e.g. /state/...).
    return normalized.lstrip("/")


def _extract_paths_from_key_value(key: str, value: Any) -> list[str]:
    paths: list[str] = []
    if isinstance(value, str):
        if key.endswith("_path") or key in {"path", "file", "file_path", "metadata_path"}:
            if _is_path_like(value):
                paths.append(_normalize_path(value))
        return paths

    if isinstance(value, list):
        if key.endswith("_paths") or key in {"attachment_names", "paths"}:
            for item in value:
                if isinstance(item, str) and _is_path_like(item):
                    paths.append(_normalize_path(item))
        return paths

    return paths


def _extract_event_paths(event: dict[str, Any]) -> list[str]:
    paths: set[str] = set()
    for key, value in event.items():
        for path in _extract_paths_from_key_value(key, value):
            paths.add(path)

    # Memory block operations map to deterministic file paths.
    if event.get("type") == "tool_call":
        tool_name = str(event.get("tool", "")).strip()
        if tool_name in {"create_memory_block", "update_memory_block", "delete_memory_block"}:
            block_id = str(event.get("block_id", "")).strip()
            if block_id:
                paths.add(f"blocks/{block_id}.yaml")

    return sorted(paths)


def _load_events(events_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not events_path.exists():
        return rows
    for raw_line in events_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _top_rows(counter: Counter[str], limit: int) -> list[dict[str, Any]]:
    return [
        {"path": path, "count": count}
        for path, count in counter.most_common(limit)
    ]


def _render_text_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"output_file: {report['output_file']}")
    lines.append(f"plot_file: {report['plot_file']}")
    lines.append(f"events_file: {report['events_file']}")
    lines.append(f"total_events: {report['total_events']}")
    lines.append(f"session_count: {report['session_count']}")
    lines.append(f"heatmap_file_count: {report['heatmap_file_count']}")
    lines.append("Overall top files:")
    overall_top = report.get("overall_top_files", [])
    if overall_top:
        for row in overall_top:
            lines.append(f"- {row['path']}: {row['count']}")
    else:
        lines.append("- none")
    lines.append("Session details:")
    sessions = report.get("sessions", [])
    if not sessions:
        lines.append("- none")
        return "\n".join(lines)

    for session in sessions:
        lines.append(f"- session_id={session['session_id']} events={session['event_count']} unique_files={session['unique_files']}")
        session_top = session.get("top_files", [])
        if not session_top:
            lines.append("  top_files: none")
            continue
        lines.append("  top_files:")
        for row in session_top:
            lines.append(f"  - {row['path']}: {row['count']}")
    return "\n".join(lines)


def _resolve_plot_rows(
    *,
    repo_root: Path,
    counts: Counter[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, access_count in counts.items():
        target = repo_root / path
        if not target.exists() or not target.is_file():
            continue
        rows.append(
            {
                "path": path,
                "size_bytes": int(target.stat().st_size),
                "access_count": int(access_count),
            },
        )
    rows.sort(key=lambda row: row["access_count"], reverse=True)
    return rows


def _coaccess_paths(counts: Counter[str], limit: int) -> list[str]:
    return [path for path, _ in counts.most_common(limit)]


def _build_coaccess_matrix(
    *,
    per_session_counts: dict[str, Counter[str]],
    paths: list[str],
) -> list[list[int]]:
    sessions = [set(counter.keys()) for counter in per_session_counts.values() if counter]
    matrix: list[list[int]] = []
    for left in paths:
        row: list[int] = []
        for right in paths:
            together = sum(1 for session_files in sessions if left in session_files and right in session_files)
            row.append(together)
        matrix.append(row)
    return matrix


def _top_coaccess_pairs(
    *,
    paths: list[str],
    matrix: list[list[int]],
    limit: int = 15,
) -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = []
    for left_idx, left_path in enumerate(paths):
        for right_idx in range(left_idx + 1, len(paths)):
            count = int(matrix[left_idx][right_idx])
            if count <= 0:
                continue
            pairs.append(
                {
                    "left": left_path,
                    "right": paths[right_idx],
                    "session_count": count,
                },
            )
    pairs.sort(
        key=lambda row: (
            -int(row["session_count"]),
            str(row["left"]),
            str(row["right"]),
        ),
    )
    return pairs[:limit]


def _write_dashboard_plot(
    *,
    rows: list[dict[str, Any]],
    coaccess_paths: list[str],
    coaccess_matrix: list[list[int]],
    plot_path: Path,
) -> None:
    try:
        import matplotlib
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "matplotlib is required for file frequency plotting. Install with: uv add matplotlib",
        ) from exc

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    plot_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(14, 11),
        constrained_layout=True,
        gridspec_kw={"height_ratios": [1.0, 1.25]},
    )
    ax_scatter, ax_heatmap = axes

    if rows:
        x_values = [row["size_bytes"] for row in rows]
        y_values = [row["access_count"] for row in rows]
        ax_scatter.scatter(x_values, y_values, alpha=0.8)

        # Label the busiest points to keep the chart readable.
        for row in rows[:12]:
            ax_scatter.annotate(
                row["path"],
                (row["size_bytes"], row["access_count"]),
                textcoords="offset points",
                xytext=(4, 4),
                fontsize=8,
            )
    else:
        ax_scatter.text(
            0.5,
            0.5,
            "No existing files matched events.jsonl access paths",
            ha="center",
            va="center",
        )

    ax_scatter.set_xlabel("File Size (bytes)")
    ax_scatter.set_ylabel("Access Frequency")
    ax_scatter.set_title("File Access Frequency vs File Size")
    ax_scatter.grid(alpha=0.25)

    if coaccess_paths and coaccess_matrix:
        image = ax_heatmap.imshow(coaccess_matrix, cmap="magma", aspect="auto")
        index_range = list(range(len(coaccess_paths)))
        ax_heatmap.set_xticks(index_range)
        ax_heatmap.set_yticks(index_range)
        ax_heatmap.set_xticklabels(coaccess_paths, rotation=55, ha="right", fontsize=8)
        ax_heatmap.set_yticklabels(coaccess_paths, fontsize=8)
        ax_heatmap.set_xlabel("File")
        ax_heatmap.set_ylabel("File")
        ax_heatmap.set_title("File Co-Access Heatmap (same-session frequency)")
        colorbar = fig.colorbar(image, ax=ax_heatmap)
        colorbar.set_label("Sessions with both files")
    else:
        ax_heatmap.text(0.5, 0.5, "Not enough file access data for co-access heatmap", ha="center", va="center")
        ax_heatmap.set_xticks([])
        ax_heatmap.set_yticks([])
        ax_heatmap.set_title("File Co-Access Heatmap (same-session frequency)")

    fig.savefig(plot_path, dpi=150)
    plt.close(fig)


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.exists():
        raise SystemExit(f"repo root does not exist: {repo_root}")

    events_path = Path(args.events_file).expanduser()
    if not events_path.is_absolute():
        events_path = (repo_root / events_path).resolve()

    if args.output:
        output_path = Path(args.output).expanduser()
        if not output_path.is_absolute():
            output_path = (repo_root / output_path).resolve()
    else:
        output_path = _default_output_path(repo_root)
    if args.plot_output:
        plot_path = Path(args.plot_output).expanduser()
        if not plot_path.is_absolute():
            plot_path = (repo_root / plot_path).resolve()
    else:
        plot_path = _default_plot_path(repo_root)

    top_n = max(1, int(args.top))
    heatmap_top = max(2, int(args.heatmap_top))
    selected_session_id = str(args.session_id).strip() if args.session_id else None
    if selected_session_id == "":
        selected_session_id = None

    events = _load_events(events_path)
    per_session_counts: dict[str, Counter[str]] = defaultdict(Counter)
    per_session_events: Counter[str] = Counter()
    overall_counts: Counter[str] = Counter()
    included_events = 0

    for event in events:
        session_id = str(event.get("session_id", "")).strip() or "missing-session-id"
        if selected_session_id and session_id != selected_session_id:
            continue

        included_events += 1
        per_session_events[session_id] += 1
        paths = _extract_event_paths(event)
        for path in paths:
            per_session_counts[session_id][path] += 1
            overall_counts[path] += 1

    sessions_payload: list[dict[str, Any]] = []
    for session_id in sorted(per_session_events.keys()):
        session_counter = per_session_counts[session_id]
        sessions_payload.append(
            {
                "session_id": session_id,
                "event_count": per_session_events[session_id],
                "unique_files": len(session_counter),
                "top_files": _top_rows(session_counter, top_n),
            },
        )

    plot_rows = _resolve_plot_rows(repo_root=repo_root, counts=overall_counts)
    heatmap_paths = _coaccess_paths(overall_counts, heatmap_top)
    coaccess_matrix = _build_coaccess_matrix(
        per_session_counts=per_session_counts,
        paths=heatmap_paths,
    )
    coaccess_pairs = _top_coaccess_pairs(paths=heatmap_paths, matrix=coaccess_matrix)

    _write_dashboard_plot(
        rows=plot_rows,
        coaccess_paths=heatmap_paths,
        coaccess_matrix=coaccess_matrix,
        plot_path=plot_path,
    )

    report = {
        "generated_at": datetime.now().isoformat(),
        "events_file": str(events_path),
        "output_file": str(output_path),
        "plot_file": str(plot_path),
        "total_events": included_events,
        "session_count": len(sessions_payload),
        "session_filter": selected_session_id,
        "overall_top_files": _top_rows(overall_counts, top_n),
        "plot_points": len(plot_rows),
        "heatmap_file_count": len(heatmap_paths),
        "heatmap_files": heatmap_paths,
        "coaccess_top_pairs": coaccess_pairs,
        "sessions": sessions_payload,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")

    print(f"wrote file frequency report: {output_path}")
    print(f"wrote file frequency scatter: {plot_path}")
    print(_render_text_report(report))


if __name__ == "__main__":
    main()

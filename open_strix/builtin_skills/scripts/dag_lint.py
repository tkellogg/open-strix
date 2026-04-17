#!/usr/bin/env python3
"""Walk file references from a root file and report the dependency graph.

Given a directory and a root file (e.g., SKILL.md, a memory block YAML, or a
job config), this script:

1. Parses all markdown/YAML/JSON files for file references (relative paths,
   markdown links, YAML values).
2. Builds a directed graph of which files reference which.
3. Outputs the graph in mermaid (human-readable) or JSON (LLM-readable).
4. Flags unreferenced files (present in the directory but not reachable from
   the root) as potential dead subtrees.

Usage:
    python dag_lint.py /path/to/skill --root SKILL.md
    python dag_lint.py /path/to/skill --root SKILL.md --format json
    python dag_lint.py /path/to/memory --root blocks.yaml --format mermaid
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


# Patterns that match file references in various formats
REFERENCE_PATTERNS = [
    # Markdown links: [text](path) — exclude URLs
    re.compile(r"\[(?:[^\]]*)\]\((?!https?://|#)([^)]+)\)"),
    # Markdown-style reference in YAML: `path/to/file.md`
    re.compile(r"`((?:\./|\.\./)?\S+\.\w{1,6})`"),
    # Bare relative paths that look like files (word/word.ext)
    re.compile(r"(?:^|\s)((?:\./|\.\./)?\w[\w./-]*\.\w{1,6})(?:\s|$|[,;)])", re.MULTILINE),
    # YAML/JSON string values that look like paths
    re.compile(r'["\'](\./[^"\']+|\.\.?/[^"\']+)["\']'),
    # Python/shell: "examples/foo.py", "watcher-examples/bar.py"
    re.compile(r'"([\w./-]+\.(?:py|sh|md|yaml|yml|json|txt))"'),
    # open-strix builtin skill paths: /.open_strix_builtin_skills/skill-name/file.ext
    re.compile(r"`?/\.open_strix_builtin_skills/\w[\w-]*/([^`\s)]+)`?"),
]

# Files to skip
SKIP_NAMES = {"__pycache__", ".git", "node_modules", ".mypy_cache", ".pytest_cache"}
SKIP_EXTENSIONS = {".pyc", ".pyo"}


def find_references(file_path: Path, base_dir: Path) -> list[str]:
    """Extract file references from a single file."""
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    refs: set[str] = set()
    for pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(content):
            candidate = match.group(1).strip()
            # Clean up anchors and query params
            candidate = candidate.split("#")[0].split("?")[0]
            if not candidate:
                continue
            # Resolve relative to the file's directory
            resolved = (file_path.parent / candidate).resolve()
            try:
                rel = resolved.relative_to(base_dir.resolve())
                refs.add(str(rel))
            except ValueError:
                # Reference points outside base_dir — skip
                continue

    return sorted(refs)


def discover_files(base_dir: Path) -> list[str]:
    """Find all non-hidden files in the directory."""
    files: list[str] = []
    for p in sorted(base_dir.rglob("*")):
        if any(part in SKIP_NAMES for part in p.parts):
            continue
        if p.suffix in SKIP_EXTENSIONS:
            continue
        if p.is_file():
            try:
                files.append(str(p.relative_to(base_dir)))
            except ValueError:
                continue
    return files


def build_dag(
    base_dir: Path, root_file: str
) -> tuple[dict[str, list[str]], set[str], set[str]]:
    """Build the reference DAG starting from root_file.

    Returns:
        (edges, reachable, all_files)
        - edges: {source_file: [referenced_files]}
        - reachable: set of files reachable from root
        - all_files: set of all files discovered in the directory
    """
    all_files = set(discover_files(base_dir))
    edges: dict[str, list[str]] = {}

    # Walk from root using BFS
    visited: set[str] = set()
    queue = [root_file]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        current_path = base_dir / current
        if not current_path.exists():
            continue

        refs = find_references(current_path, base_dir)
        # Only keep refs that point to actual files in the directory
        valid_refs = [r for r in refs if r in all_files and r != current]
        if valid_refs:
            edges[current] = valid_refs

        for ref in valid_refs:
            if ref not in visited:
                queue.append(ref)

    return edges, visited, all_files


def sanitize_mermaid_id(path: str) -> str:
    """Convert a file path to a valid mermaid node ID."""
    return path.replace("/", "_").replace(".", "_").replace("-", "_").replace(" ", "_")


def format_mermaid(
    edges: dict[str, list[str]],
    reachable: set[str],
    all_files: set[str],
    root_file: str,
) -> str:
    """Format the DAG as a mermaid graph."""
    lines = ["graph TD"]

    # Style the root node
    root_id = sanitize_mermaid_id(root_file)
    lines.append(f"    {root_id}[\"📄 {root_file}\"]")

    # Add edges
    for source, targets in sorted(edges.items()):
        src_id = sanitize_mermaid_id(source)
        for target in targets:
            tgt_id = sanitize_mermaid_id(target)
            lines.append(f"    {src_id} --> {tgt_id}")

    # Flag unreferenced files
    unreferenced = sorted(all_files - reachable)
    if unreferenced:
        lines.append("")
        lines.append("    subgraph unreferenced[\"⚠️ Unreferenced Files\"]")
        for f in unreferenced:
            fid = sanitize_mermaid_id(f)
            lines.append(f"        {fid}[\"{f}\"]")
        lines.append("    end")
        lines.append("    style unreferenced fill:#fff3cd,stroke:#ffc107")

    return "\n".join(lines)


def format_json(
    edges: dict[str, list[str]],
    reachable: set[str],
    all_files: set[str],
    root_file: str,
) -> str:
    """Format the DAG as JSON."""
    unreferenced = sorted(all_files - reachable)
    return json.dumps(
        {
            "root": root_file,
            "edges": {k: sorted(v) for k, v in sorted(edges.items())},
            "reachable": sorted(reachable),
            "unreferenced": unreferenced,
            "stats": {
                "total_files": len(all_files),
                "reachable_files": len(reachable),
                "unreferenced_files": len(unreferenced),
                "coverage_pct": round(
                    len(reachable) / len(all_files) * 100, 1
                )
                if all_files
                else 100.0,
            },
        },
        indent=2,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Walk file references from a root file and report the dependency DAG.",
    )
    parser.add_argument(
        "directory",
        help="Directory to scan (e.g., a skill directory, memory directory, or repo root).",
    )
    parser.add_argument(
        "--root",
        default="SKILL.md",
        help="Root file to start walking from (default: SKILL.md).",
    )
    parser.add_argument(
        "--format",
        choices=["mermaid", "json"],
        default="mermaid",
        help="Output format (default: mermaid).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit with code 1 if unreferenced files are found.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    base_dir = Path(args.directory).resolve()
    if not base_dir.is_dir():
        print(f"Error: {args.directory} is not a directory", file=sys.stderr)
        return 1

    root_path = base_dir / args.root
    if not root_path.exists():
        print(f"Error: root file {args.root} not found in {args.directory}", file=sys.stderr)
        return 1

    edges, reachable, all_files = build_dag(base_dir, args.root)
    unreferenced = all_files - reachable

    if args.format == "mermaid":
        print(format_mermaid(edges, reachable, all_files, args.root))
    else:
        print(format_json(edges, reachable, all_files, args.root))

    # Summary to stderr so it doesn't pollute the graph output
    n_total = len(all_files)
    n_reach = len(reachable)
    n_unref = len(unreferenced)
    print(
        f"\n{n_reach}/{n_total} files reachable from {args.root} "
        f"({n_unref} unreferenced)",
        file=sys.stderr,
    )

    if args.strict and unreferenced:
        print("Unreferenced files:", file=sys.stderr)
        for f in sorted(unreferenced):
            print(f"  - {f}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Activity patterns dashboard — embedding-free conversation metrics.

Reads events.jsonl to compute:
- Quantity Ratio (QR): agent output volume / all input volume
- Effective Sources: Shannon entropy of message distribution → exp(H)
- Per-source volume breakdown

No embeddings, no external APIs. Pure character counts and message counts.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable


@dataclass
class DayMetrics:
    """Activity metrics for a single day."""

    day: date
    # Per-source message counts and char volumes
    source_messages: dict[str, int] = field(default_factory=dict)
    source_chars: dict[str, int] = field(default_factory=dict)
    # Agent output
    agent_messages: int = 0
    agent_chars: int = 0

    @property
    def input_messages(self) -> int:
        return sum(self.source_messages.values())

    @property
    def input_chars(self) -> int:
        return sum(self.source_chars.values())

    @property
    def quantity_ratio(self) -> float | None:
        """Agent output chars / all input chars. None if no input."""
        if self.input_chars < 10:
            return None
        return self.agent_chars / self.input_chars

    @property
    def effective_sources(self) -> float:
        """exp(Shannon entropy) of message count distribution across sources."""
        counts = [n for n in self.source_messages.values() if n > 0]
        if len(counts) < 1:
            return 0.0
        total = sum(counts)
        if total == 0:
            return 0.0
        probs = [c / total for c in counts]
        h = -sum(p * math.log(p) for p in probs if p > 0)
        return math.exp(h)

    @property
    def qr_region(self) -> str:
        """Labeled region for quantity ratio."""
        qr = self.quantity_ratio
        if qr is None:
            return "no-input"
        if qr < 0.5:
            return "absorbing"
        if qr < 1.5:
            return "conversational"
        if qr < 3.0:
            return "productive"
        if qr < 5.0:
            return "chatty"
        return "monologue"


def _parse_timestamp(ts_str: str) -> datetime | None:
    """Parse ISO timestamp from events.jsonl."""
    try:
        # Handle both timezone-aware and naive timestamps
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _extract_text_length(event: dict) -> int:
    """Extract the character length of message content from an event."""
    # send_message tool calls
    if event.get("tool") == "send_message":
        # Try text_preview first (newer format)
        preview = event.get("text_preview", "")
        if preview:
            return len(preview)
        # Try args.text (older format)
        args = event.get("args", {})
        if isinstance(args, dict):
            text = args.get("text", "")
            return len(text)
    # discord_message events (incoming)
    content = event.get("content", "")
    if content:
        return len(content)
    return 0


def load_events(events_path: Path, days_back: int = 7) -> list[dict]:
    """Load events from events.jsonl within date range."""
    if not events_path.exists():
        return []

    cutoff = datetime.now().astimezone() - timedelta(days=days_back)
    events = []

    with open(events_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = _parse_timestamp(event.get("timestamp", ""))
            if ts is None:
                continue

            # Make cutoff tz-aware if needed
            if ts.tzinfo is not None and cutoff.tzinfo is None:
                cutoff = cutoff.astimezone()
            elif ts.tzinfo is None and cutoff.tzinfo is not None:
                cutoff = cutoff.replace(tzinfo=None)

            if ts >= cutoff:
                events.append(event)

    return events


def compute_daily_metrics(
    events: list[dict],
    agent_name: str | None = None,
) -> list[DayMetrics]:
    """Group events by day and compute metrics.

    If agent_name is None, infers agent from send_message events.
    Input sources are identified from discord_message events by author field.
    """
    by_day: dict[date, DayMetrics] = {}

    for event in events:
        ts = _parse_timestamp(event.get("timestamp", ""))
        if ts is None:
            continue
        day = ts.date()
        if day not in by_day:
            by_day[day] = DayMetrics(day=day)
        metrics = by_day[day]

        event_type = event.get("type", "")

        # Incoming messages
        if event_type == "discord_message":
            author = event.get("author", "unknown")
            author_is_bot = event.get("author_is_bot", False)
            char_len = _extract_text_length(event)
            if char_len > 0:
                metrics.source_messages[author] = metrics.source_messages.get(author, 0) + 1
                metrics.source_chars[author] = metrics.source_chars.get(author, 0) + char_len

        # Outgoing messages (agent output)
        elif event_type == "tool_call" and event.get("tool") == "send_message":
            if event.get("sent", False):
                char_len = _extract_text_length(event)
                metrics.agent_messages += 1
                metrics.agent_chars += char_len

    return sorted(by_day.values(), key=lambda m: m.day)


def render_text_report(daily: list[DayMetrics]) -> str:
    """Render a plain-text activity patterns report."""
    if not daily:
        return "No activity data found."

    lines = ["Activity Patterns Report", "=" * 40, ""]

    # Summary across all days
    total_input_chars = sum(m.input_chars for m in daily)
    total_output_chars = sum(m.agent_chars for m in daily)
    total_input_msgs = sum(m.input_messages for m in daily)
    total_output_msgs = sum(m.agent_messages for m in daily)

    overall_qr = total_output_chars / total_input_chars if total_input_chars > 10 else None

    # Aggregate sources
    all_sources: dict[str, int] = defaultdict(int)
    all_source_chars: dict[str, int] = defaultdict(int)
    for m in daily:
        for src, count in m.source_messages.items():
            all_sources[src] += count
        for src, chars in m.source_chars.items():
            all_source_chars[src] += chars

    lines.append(f"Period: {daily[0].day} to {daily[-1].day} ({len(daily)} days)")
    lines.append(f"Messages: {total_input_msgs} in, {total_output_msgs} out")
    lines.append(f"Volume: {total_input_chars:,} chars in, {total_output_chars:,} chars out")
    if overall_qr is not None:
        lines.append(f"Overall QR: {overall_qr:.2f}")
    lines.append("")

    # Per-source breakdown
    lines.append("Sources (by volume):")
    for src, chars in sorted(all_source_chars.items(), key=lambda x: -x[1]):
        msgs = all_sources.get(src, 0)
        lines.append(f"  {src:20s}  {msgs:4d} msgs  {chars:>8,} chars")
    lines.append("")

    # Daily breakdown
    lines.append("Daily:")
    lines.append(f"  {'Date':12s}  {'QR':>6s}  {'Region':14s}  {'Eff.Src':>7s}  {'In':>8s}  {'Out':>8s}")
    lines.append(f"  {'-'*12}  {'-'*6}  {'-'*14}  {'-'*7}  {'-'*8}  {'-'*8}")
    for m in daily:
        qr = m.quantity_ratio
        qr_str = f"{qr:.2f}" if qr is not None else "N/A"
        lines.append(
            f"  {m.day.isoformat():12s}  {qr_str:>6s}  {m.qr_region:14s}  "
            f"{m.effective_sources:>7.1f}  {m.input_chars:>8,}  {m.agent_chars:>8,}"
        )

    # QR trend
    qr_values = [m.quantity_ratio for m in daily if m.quantity_ratio is not None]
    if len(qr_values) >= 3:
        lines.append("")
        mean_qr = sum(qr_values) / len(qr_values)
        lines.append(f"QR mean: {mean_qr:.2f}")
        if len(qr_values) >= 2:
            variance = sum((v - mean_qr) ** 2 for v in qr_values) / len(qr_values)
            std = variance ** 0.5
            cv = std / mean_qr if mean_qr > 0 else 0
            lines.append(f"QR std:  {std:.2f}")
            lines.append(f"QR CV:   {cv:.2f}")

    return "\n".join(lines)


def plot_dashboard(daily: list[DayMetrics], output_path: Path) -> None:
    """Generate matplotlib dashboard image."""
    try:
        import matplotlib
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "matplotlib is required for dashboard plotting. Install with: uv add matplotlib"
        ) from exc

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    import matplotlib.dates as mdates

    output_path.parent.mkdir(parents=True, exist_ok=True)

    fig, axes = plt.subplots(2, 2, figsize=(14, 10), constrained_layout=True)

    dates = [m.day for m in daily]
    qr_values = [m.quantity_ratio for m in daily]
    eff_src = [m.effective_sources for m in daily]
    in_chars = [m.input_chars / 1000 for m in daily]
    out_chars = [m.agent_chars / 1000 for m in daily]

    # Region colors (neutral, not judgmental)
    region_colors = {
        "absorbing": "#3498db",
        "conversational": "#2ecc71",
        "productive": "#f1c40f",
        "chatty": "#e67e22",
        "monologue": "#e74c3c",
    }

    # === Plot 1: QR over time with labeled regions ===
    ax1 = axes[0, 0]
    valid_dates = [d for d, qr in zip(dates, qr_values) if qr is not None]
    valid_qr = [qr for qr in qr_values if qr is not None]
    if valid_qr:
        max_qr = max(valid_qr) * 1.2
        # Draw region bands
        ax1.axhspan(0, 0.5, alpha=0.08, color=region_colors["absorbing"])
        ax1.axhspan(0.5, 1.5, alpha=0.08, color=region_colors["conversational"])
        ax1.axhspan(1.5, 3.0, alpha=0.08, color=region_colors["productive"])
        ax1.axhspan(3.0, 5.0, alpha=0.08, color=region_colors["chatty"])
        ax1.axhspan(5.0, max(max_qr, 6), alpha=0.08, color=region_colors["monologue"])
        # Region labels
        label_x = valid_dates[-1] if valid_dates else dates[-1]
        for label, y in [("absorbing", 0.25), ("conversational", 1.0), ("productive", 2.25),
                         ("chatty", 4.0), ("monologue", 5.5)]:
            if y < max_qr:
                ax1.text(label_x, y, f" {label}", fontsize=7, color="#666", va="center")
        ax1.plot(valid_dates, valid_qr, "o-", color="#2c3e50", linewidth=2, markersize=6)
        ax1.axhline(y=1.0, color="#95a5a6", linestyle="--", alpha=0.5)
        ax1.set_ylim(0, max(max_qr, 6))
    ax1.set_ylabel("QR (output / input chars)")
    ax1.set_title("Quantity Ratio")
    ax1.grid(True, alpha=0.2)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

    # === Plot 2: Effective Sources ===
    ax2 = axes[0, 1]
    ax2.plot(dates, eff_src, "o-", color="#2ecc71", linewidth=2, markersize=6)
    ax2.fill_between(dates, 1.0, eff_src, alpha=0.15, color="#2ecc71")
    ax2.axhline(y=1.0, color="#95a5a6", linestyle=":", alpha=0.5, label="monologue (1.0)")
    for d, s in zip(dates, eff_src):
        ax2.annotate(f"{s:.1f}", (d, s), textcoords="offset points",
                     xytext=(0, 8), ha="center", fontsize=8)
    ax2.set_ylabel("Effective Sources")
    ax2.set_title("Source Diversity")
    ax2.legend(loc="upper left", fontsize=8)
    ax2.grid(True, alpha=0.2)
    ax2.set_ylim(bottom=0, top=max(eff_src) * 1.3 + 0.5 if eff_src else 3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))

    # === Plot 3: Volume (stacked bar for input sources + agent line) ===
    ax3 = axes[1, 0]
    all_sources = sorted({src for m in daily for src in m.source_chars})
    # Color palette
    palette = ["#3498db", "#e91e9e", "#9b59b6", "#f39c12", "#1abc9c", "#e74c3c", "#95a5a6"]
    import numpy as np
    x = np.arange(len(dates))
    bottoms = np.zeros(len(dates))
    for i, src in enumerate(all_sources):
        vals = np.array([m.source_chars.get(src, 0) / 1000 for m in daily])
        color = palette[i % len(palette)]
        ax3.bar(x, vals, 0.8, bottom=bottoms, label=src, color=color, alpha=0.85)
        bottoms += vals
    ax3.plot(x, out_chars, "s--", color="#e74c3c", label="agent output", linewidth=2, markersize=5)
    ax3.set_xticks(x)
    ax3.set_xticklabels([d.strftime("%m/%d") for d in dates], rotation=45, ha="right")
    ax3.set_ylabel("Volume (K chars)")
    ax3.set_title("Input Sources vs Agent Output")
    ax3.legend(loc="upper left", fontsize=7)
    ax3.grid(True, alpha=0.2, axis="y")

    # === Plot 4: Message counts ===
    ax4 = axes[1, 1]
    in_msgs = [m.input_messages for m in daily]
    out_msgs = [m.agent_messages for m in daily]
    ax4.bar(x - 0.2, in_msgs, 0.4, label="input", color="#3498db", alpha=0.85)
    ax4.bar(x + 0.2, out_msgs, 0.4, label="agent", color="#e74c3c", alpha=0.85)
    ax4.set_xticks(x)
    ax4.set_xticklabels([d.strftime("%m/%d") for d in dates], rotation=45, ha="right")
    ax4.set_ylabel("Messages")
    ax4.set_title("Message Counts")
    ax4.legend(loc="upper left", fontsize=8)
    ax4.grid(True, alpha=0.2, axis="y")

    # Summary text box
    if valid_qr:
        mean_qr = sum(valid_qr) / len(valid_qr)
        mean_src = sum(eff_src) / len(eff_src) if eff_src else 0
        # Determine region for mean QR
        if mean_qr < 0.5:
            region = "absorbing"
        elif mean_qr < 1.5:
            region = "conversational"
        elif mean_qr < 3.0:
            region = "productive"
        elif mean_qr < 5.0:
            region = "chatty"
        else:
            region = "monologue"

        summary = (
            f"QR mean: {mean_qr:.2f} ({region})\n"
            f"Eff. sources: {mean_src:.1f}\n"
            f"Days: {len(daily)}"
        )
        props = dict(boxstyle="round", facecolor="white", alpha=0.8, edgecolor="#2c3e50", linewidth=1.5)
        fig.text(0.99, 0.99, summary, transform=fig.transFigure, fontsize=10,
                 verticalalignment="top", horizontalalignment="right",
                 bbox=props, family="monospace")

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    fig.suptitle(f"Activity Patterns ({generated})", fontsize=14, fontweight="bold")

    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Activity patterns dashboard — embedding-free conversation metrics.",
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Agent home repo path. Defaults to current working directory.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help="Number of days to analyze (default: 7).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output PNG path. Omit for text-only report.",
    )
    parser.add_argument(
        "--text-only",
        action="store_true",
        help="Print text report only, skip image generation.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = Path(args.repo_root).expanduser().resolve()

    events_path = repo_root / "logs" / "events.jsonl"
    if not events_path.exists():
        raise SystemExit(f"events.jsonl not found at {events_path}")

    events = load_events(events_path, days_back=args.days)
    if not events:
        raise SystemExit(f"No events found in the last {args.days} days.")

    daily = compute_daily_metrics(events)
    if not daily:
        raise SystemExit("No activity data found in events.")

    # Text report always printed
    report = render_text_report(daily)
    print(report)

    # Image generation
    if not args.text_only:
        if args.output:
            output_path = Path(args.output).expanduser()
            if not output_path.is_absolute():
                output_path = (repo_root / output_path).resolve()
        else:
            today = date.today().isoformat()
            output_path = repo_root / "state" / "dashboards" / f"{today}-activity.png"

        plot_dashboard(daily, output_path)
        print(f"\nDashboard saved to: {output_path}")


if __name__ == "__main__":
    main()

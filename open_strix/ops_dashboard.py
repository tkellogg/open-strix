"""Live ops dashboard — reads logs/events.jsonl on demand and renders an
operational overview at /ops. No caching; every request recomputes from the
log so what you see is always current.

Event vocabulary used:
- ``agent_invoke_start`` — turn started (with ``source_event_type`` and
  optional ``scheduler_name`` so we can attribute LLM invocations to
  pollers / web messages / discord, etc.)
- ``turn_timing`` — turn finished (with timing breakdown)
- ``tool_call`` — tool invocations during a turn
- ``event_queued`` — events entering the agent queue (covers all sources)
- ``event_deduped`` — events skipped (dedup hits)
- ``shell_job_complete`` — async shell jobs finishing
- failure-shaped: ``agent_turn_missing_send_message``,
  ``post_turn_block_validation_failed``, ``scheduler_invalid_*``
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .app import OpenStrixApp


def _parse_ts(text: str) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_events(events_log: Path, days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    out: list[dict[str, Any]] = []

    # Collect the live file plus all rotated siblings (events.jsonl.20260421T160744Z, etc.).
    # Rotated siblings sort chronologically among themselves by their timestamp suffix.
    # The live file sorts first (no suffix) but record-level ts filtering handles ordering.
    candidates = sorted(events_log.parent.glob(events_log.name + "*"))

    for candidate in candidates:
        try:
            fh = candidate.open()
        except OSError:
            # Sibling may have been mid-rotation (renamed away between glob and open).
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(record.get("timestamp", ""))
                if ts is None or ts < cutoff:
                    continue
                record["_ts"] = ts
                out.append(record)

    return out


def _hour_key(ts: datetime) -> str:
    return ts.replace(minute=0, second=0, microsecond=0).isoformat()


def _day_key(ts: datetime) -> str:
    return ts.date().isoformat()


def compute_stats(events: list[dict[str, Any]], days: int) -> dict[str, Any]:
    by_event: Counter[str] = Counter()
    tool_counts: Counter[str] = Counter()
    queued_by_source: Counter[str] = Counter()
    invoke_by_source: Counter[str] = Counter()
    invoke_by_scheduler: Counter[str] = Counter()
    invokes_by_day: Counter[str] = Counter()
    queued_by_day: Counter[str] = Counter()
    invokes_by_hour: Counter[str] = Counter()
    queued_by_hour: Counter[str] = Counter()
    deduped_by_source: Counter[str] = Counter()
    failures_by_kind: Counter[str] = Counter()
    turn_total_seconds: list[float] = []
    turn_invoke_seconds: list[float] = []
    tool_calls_in_session: defaultdict[str, int] = defaultdict(int)
    session_id_seen: set[str] = set()

    recent_failures: list[dict[str, Any]] = []
    failure_event_kinds = {
        "agent_turn_missing_send_message",
        "post_turn_block_validation_failed",
        "post_turn_block_validation_still_broken",
        "scheduler_invalid_cron",
        "scheduler_invalid_job",
        "scheduler_invalid_time",
        "shell_job_complete_enqueue_failed",
    }

    for record in events:
        kind = record.get("type", "unknown")
        ts: datetime = record["_ts"]
        by_event[kind] += 1

        if kind == "tool_call":
            tool = record.get("tool") or "unknown"
            tool_counts[tool] += 1
            sid = record.get("session_id")
            if sid:
                tool_calls_in_session[sid] += 1
        elif kind == "agent_invoke_start":
            source = record.get("source_event_type") or "unknown"
            invoke_by_source[source] += 1
            scheduler = record.get("scheduler_name")
            if scheduler:
                invoke_by_scheduler[scheduler] += 1
            invokes_by_day[_day_key(ts)] += 1
            invokes_by_hour[_hour_key(ts)] += 1
            sid = record.get("session_id")
            if sid:
                session_id_seen.add(sid)
        elif kind == "event_queued":
            source = record.get("source_event_type") or "unknown"
            queued_by_source[source] += 1
            queued_by_day[_day_key(ts)] += 1
            queued_by_hour[_hour_key(ts)] += 1
        elif kind == "event_deduped":
            deduped_by_source[record.get("key") or "unknown"] += 1
        elif kind == "turn_timing":
            total = record.get("total_seconds")
            invoke = record.get("agent_invoke_seconds")
            if isinstance(total, (int, float)):
                turn_total_seconds.append(float(total))
            if isinstance(invoke, (int, float)):
                turn_invoke_seconds.append(float(invoke))

        if kind in failure_event_kinds:
            failures_by_kind[kind] += 1
            if len(recent_failures) < 30:
                recent_failures.append({
                    "t": ts.isoformat(),
                    "kind": kind,
                    "scheduler_name": record.get("scheduler_name"),
                    "source_event_type": record.get("source_event_type"),
                    "detail": (
                        record.get("error")
                        or record.get("final_text")
                        or record.get("broken_blocks")
                        or ""
                    ),
                })

    recent_failures.sort(key=lambda x: x["t"], reverse=True)

    days_axis = sorted(set(list(invokes_by_day.keys()) + list(queued_by_day.keys())))
    timeseries = [
        {
            "day": d,
            "invokes": invokes_by_day.get(d, 0),
            "queued": queued_by_day.get(d, 0),
        }
        for d in days_axis
    ]

    tools_per_invocation = list(tool_calls_in_session.values())
    avg_tools = round(sum(tools_per_invocation) / len(tools_per_invocation), 2) if tools_per_invocation else 0.0

    def _avg(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 2) if xs else 0.0

    summary = {
        "total_events": sum(by_event.values()),
        "agent_invocations": by_event.get("agent_invoke_start", 0),
        "events_queued": by_event.get("event_queued", 0),
        "events_deduped": by_event.get("event_deduped", 0),
        "tool_calls": by_event.get("tool_call", 0),
        "shell_jobs_completed": by_event.get("shell_job_complete", 0),
        "failures": sum(failures_by_kind.values()),
        "avg_tools_per_invocation": avg_tools,
        "avg_turn_seconds": _avg(turn_total_seconds),
        "avg_invoke_seconds": _avg(turn_invoke_seconds),
        "queued_to_invoke_ratio": (
            round(by_event.get("event_queued", 0) / by_event.get("agent_invoke_start", 0), 2)
            if by_event.get("agent_invoke_start", 0) else None
        ),
    }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "summary": summary,
        "by_event": dict(by_event.most_common()),
        "top_tools": dict(tool_counts.most_common(20)),
        "invoke_by_source": dict(invoke_by_source.most_common()),
        "invoke_by_scheduler": dict(invoke_by_scheduler.most_common(20)),
        "queued_by_source": dict(queued_by_source.most_common()),
        "deduped_by_source": dict(deduped_by_source.most_common(20)),
        "failures_by_kind": dict(failures_by_kind.most_common()),
        "timeseries": timeseries,
        "recent_failures": recent_failures,
        "backlog": _backlog_items(),
    }


def _backlog_items() -> list[dict[str, str]]:
    return [
        {
            "id": "token-usage",
            "title": "Token usage by source / over time",
            "status": "Not instrumented",
            "blocker": (
                "Capture the Anthropic API ``usage`` field on each agent.ainvoke "
                "and emit a ``llm_usage`` event tied to session_id "
                "(input_tokens, output_tokens, cache_read_input_tokens, "
                "cache_creation_input_tokens)."
            ),
        },
        {
            "id": "llm-retries",
            "title": "LLM retry count tracking",
            "status": "Not currently logged as a discrete event",
            "blocker": (
                "Wrap or hook the SDK retry path to emit a ``llm_retry`` "
                "event with attempt number and exception type."
            ),
        },
        {
            "id": "tool-failure",
            "title": "Tool-call failure rate (vs raw counts)",
            "status": "Partial",
            "blocker": (
                "``tool_call`` events fire on dispatch but no matching "
                "``tool_result`` / ``tool_error`` event is emitted. Add an "
                "exit-status event so failure rate can be computed."
            ),
        },
    ]


def render_dashboard_html(stats: dict[str, Any]) -> str:
    return _DASHBOARD_HTML.replace("__DATA__", json.dumps(stats))


_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <meta name="robots" content="noindex,nofollow" />
    <title>Ops Dashboard</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
    <style>
      :root {
        --paper: #f5efe3;
        --paper-strong: #fffaf1;
        --ink: #1e2430;
        --muted: #5f6b76;
        --line: rgba(30, 36, 48, 0.12);
        --accent: #0d766e;
        --accent-soft: rgba(13, 118, 110, 0.12);
        --warn: #b76d0d;
        --warn-soft: rgba(183, 109, 13, 0.12);
      }
      * { box-sizing: border-box; }
      html, body {
        margin: 0;
        background:
          radial-gradient(circle at top left, rgba(13, 118, 110, 0.08), transparent 32rem),
          linear-gradient(180deg, #efe4cf 0%, #f7f2e7 36%, #f5efe3 100%);
        color: var(--ink);
        font-family: "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      }
      body { padding: 1rem 1.4rem 3rem; }
      .shell { max-width: 1100px; margin: 0 auto; }
      header.page-header {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 1rem;
        flex-wrap: wrap;
        padding-bottom: 0.6rem;
        border-bottom: 1px solid var(--line);
        margin-bottom: 1.2rem;
      }
      header.page-header h1 { margin: 0; font-size: 1.4rem; font-weight: 600; }
      header.page-header a.back { color: var(--accent); text-decoration: none; font-size: 0.9rem; }
      header.page-header a.back:hover { text-decoration: underline; }
      .meta { color: var(--muted); font-size: 0.82rem; }
      .summary-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
        gap: 0.6rem;
        margin-bottom: 1.4rem;
      }
      .stat {
        background: var(--paper-strong);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.75rem 0.9rem;
      }
      .stat .num { font-size: 1.4rem; font-weight: 600; color: var(--accent); }
      .stat .label { font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
      .tabs {
        display: flex;
        gap: 0.2rem;
        border-bottom: 1px solid var(--line);
        margin-bottom: 1rem;
        flex-wrap: wrap;
      }
      .tab {
        padding: 0.5rem 0.9rem;
        cursor: pointer;
        background: transparent;
        border: none;
        font-size: 0.92rem;
        color: var(--muted);
        font-family: inherit;
        border-bottom: 2px solid transparent;
        margin-bottom: -1px;
      }
      .tab:hover { color: var(--ink); }
      .tab.active { color: var(--accent); border-bottom-color: var(--accent); font-weight: 600; }
      .panel { display: none; }
      .panel.active { display: block; }
      .chart-wrap {
        background: var(--paper-strong);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 0.7rem;
        margin: 0.7rem 0;
      }
      canvas { max-height: 320px; }
      table { border-collapse: collapse; width: 100%; font-size: 0.9rem; margin: 0.6rem 0 1rem; }
      th, td { padding: 0.45rem 0.7rem; text-align: left; border-bottom: 1px solid var(--line); }
      th { background: rgba(13, 118, 110, 0.04); font-weight: 600; }
      td.num { text-align: right; font-variant-numeric: tabular-nums; }
      .backlog-item {
        background: var(--warn-soft);
        border-left: 3px solid var(--warn);
        padding: 0.7rem 0.9rem;
        margin: 0.5rem 0;
        border-radius: 4px;
      }
      .backlog-item h3 { margin: 0 0 0.25rem 0; font-size: 0.95rem; }
      .backlog-item .status { font-size: 0.8rem; color: var(--warn); font-weight: 500; }
      .backlog-item .blocker { font-size: 0.86rem; color: var(--muted); margin-top: 0.3rem; }
      details { margin: 0.6rem 0; }
      summary { cursor: pointer; color: var(--muted); font-size: 0.85rem; }
      pre { background: var(--paper-strong); border: 1px solid var(--line); padding: 0.6rem; border-radius: 4px; font-size: 0.78rem; overflow-x: auto; }
      .hint { color: var(--muted); font-size: 0.85rem; margin: 0.2rem 0 0.7rem; }
    </style>
  </head>
  <body>
    <main class="shell">
      <header class="page-header">
        <div>
          <h1>Ops Dashboard</h1>
          <div class="meta" id="meta"></div>
        </div>
        <a class="back" href="/">&larr; back to chat</a>
      </header>

      <section class="summary-grid" id="summary"></section>

      <nav class="tabs">
        <button class="tab active" data-panel="overview">Overview</button>
        <button class="tab" data-panel="invocations">Invocations</button>
        <button class="tab" data-panel="tools">Tools</button>
        <button class="tab" data-panel="failures">Failures</button>
        <button class="tab" data-panel="backlog">Backlog</button>
        <button class="tab" data-panel="raw">Raw</button>
      </nav>

      <section id="overview" class="panel active">
        <div class="chart-wrap"><canvas id="event-mix"></canvas></div>
        <div class="chart-wrap"><canvas id="invocations-timeseries"></canvas></div>
      </section>

      <section id="invocations" class="panel">
        <p class="hint">Source = the kind of event that triggered an agent turn. Scheduler = the named poller / cron that produced it (if any).</p>
        <div class="chart-wrap"><canvas id="invoke-by-source"></canvas></div>
        <h3>Agent invocations by scheduler</h3>
        <table id="scheduler-table"><thead><tr><th>Scheduler</th><th class="num">Invocations</th></tr></thead><tbody></tbody></table>
        <h3>Events queued by source</h3>
        <table id="queued-table"><thead><tr><th>Source event type</th><th class="num">Queued</th></tr></thead><tbody></tbody></table>
      </section>

      <section id="tools" class="panel">
        <p class="hint">Avg tool calls per invocation: <strong id="avg-tools"></strong>.</p>
        <div class="chart-wrap"><canvas id="top-tools"></canvas></div>
      </section>

      <section id="failures" class="panel">
        <p class="hint">Failure-shaped events: missing send_message, broken memory blocks, scheduler validation, shell-job enqueue failures.</p>
        <h3>Failures by kind</h3>
        <table id="failure-table"><thead><tr><th>Kind</th><th class="num">Count</th></tr></thead><tbody></tbody></table>
        <details open><summary>Recent failures (up to 30)</summary><pre id="recent-failures"></pre></details>
      </section>

      <section id="backlog" class="panel">
        <p class="hint">Data not yet captured. Each item describes the instrumentation needed.</p>
        <div id="backlog-list"></div>
      </section>

      <section id="raw" class="panel">
        <h3>All event types</h3>
        <table id="event-table"><thead><tr><th>Event type</th><th class="num">Count</th></tr></thead><tbody></tbody></table>
      </section>
    </main>

    <script id="data" type="application/json">__DATA__</script>
    <script>
      const D = JSON.parse(document.getElementById('data').textContent);

      document.getElementById('meta').textContent =
        'Window: ' + D.window_days + ' days · Generated ' + D.generated_at + ' · Live read of logs/events.jsonl';

      document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
        document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
        document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
        t.classList.add('active');
        document.getElementById(t.dataset.panel).classList.add('active');
      }));

      const summaryEl = document.getElementById('summary');
      const labels = {
        total_events: 'Total events',
        agent_invocations: 'Agent invocations',
        events_queued: 'Events queued',
        events_deduped: 'Events deduped',
        tool_calls: 'Tool calls',
        shell_jobs_completed: 'Shell jobs done',
        failures: 'Failures',
        avg_tools_per_invocation: 'Avg tools/invoke',
        avg_turn_seconds: 'Avg turn (s)',
        avg_invoke_seconds: 'Avg LLM time (s)',
        queued_to_invoke_ratio: 'Queued:invoke',
      };
      for (const [k, v] of Object.entries(D.summary)) {
        if (v === null || v === undefined) continue;
        const div = document.createElement('div');
        div.className = 'stat';
        div.innerHTML = '<div class="num">' + v + '</div><div class="label">' + (labels[k] || k) + '</div>';
        summaryEl.appendChild(div);
      }

      document.getElementById('avg-tools').textContent = D.summary.avg_tools_per_invocation;

      function fillTable(id, obj, emptyMsg) {
        const tbody = document.querySelector('#' + id + ' tbody');
        const entries = Object.entries(obj);
        if (entries.length === 0) {
          const tr = document.createElement('tr');
          tr.innerHTML = '<td colspan="2" style="color:var(--muted)">' + (emptyMsg || 'no data') + '</td>';
          tbody.appendChild(tr);
          return;
        }
        for (const [k, v] of entries) {
          const tr = document.createElement('tr');
          tr.innerHTML = '<td>' + k + '</td><td class="num">' + v + '</td>';
          tbody.appendChild(tr);
        }
      }
      fillTable('event-table', D.by_event);
      fillTable('scheduler-table', D.invoke_by_scheduler, 'no scheduler-attributed invocations');
      fillTable('queued-table', D.queued_by_source);
      fillTable('failure-table', D.failures_by_kind, 'no failures in window');

      document.getElementById('recent-failures').textContent =
        D.recent_failures.length ? JSON.stringify(D.recent_failures, null, 2) : '(none)';

      const backlogEl = document.getElementById('backlog-list');
      for (const item of D.backlog) {
        const div = document.createElement('div');
        div.className = 'backlog-item';
        div.innerHTML =
          '<h3>' + item.title + '</h3>' +
          '<div class="status">' + item.status + '</div>' +
          '<div class="blocker">' + item.blocker + '</div>';
        backlogEl.appendChild(div);
      }

      const accent = '#0d766e';
      const warn = '#b76d0d';
      const muted = '#5f6b76';

      const eventLabels = Object.keys(D.by_event).slice(0, 12);
      const eventValues = eventLabels.map(k => D.by_event[k]);
      new Chart(document.getElementById('event-mix'), {
        type: 'bar',
        data: { labels: eventLabels, datasets: [{ label: 'Events', data: eventValues, backgroundColor: accent }] },
        options: { plugins: { title: { display: true, text: 'Event mix (top 12)' }, legend: { display: false } } }
      });

      const days = D.timeseries.map(x => x.day);
      const invokes = D.timeseries.map(x => x.invokes);
      const queued = D.timeseries.map(x => x.queued);
      new Chart(document.getElementById('invocations-timeseries'), {
        type: 'line',
        data: { labels: days, datasets: [
          { label: 'Agent invocations', data: invokes, borderColor: accent, tension: 0.2 },
          { label: 'Events queued', data: queued, borderColor: warn, tension: 0.2 },
        ]},
        options: { plugins: { title: { display: true, text: 'Invocations vs queued events per day' } } }
      });

      const sourceLabels = Object.keys(D.invoke_by_source);
      const sourceValues = sourceLabels.map(k => D.invoke_by_source[k]);
      new Chart(document.getElementById('invoke-by-source'), {
        type: 'bar',
        data: { labels: sourceLabels, datasets: [{ label: 'Invocations', data: sourceValues, backgroundColor: accent }] },
        options: { indexAxis: 'y', plugins: { title: { display: true, text: 'Agent invocations by source event type' }, legend: { display: false } } }
      });

      const toolNames = Object.keys(D.top_tools);
      const toolValues = toolNames.map(k => D.top_tools[k]);
      new Chart(document.getElementById('top-tools'), {
        type: 'bar',
        data: { labels: toolNames, datasets: [{ label: 'Calls', data: toolValues, backgroundColor: accent }] },
        options: { indexAxis: 'y', plugins: { title: { display: true, text: 'Top tools' }, legend: { display: false } } }
      });
    </script>
  </body>
</html>
"""


def build_dashboard_payload(strix: "OpenStrixApp", days: int) -> dict[str, Any]:
    events = _load_events(strix.layout.events_log, days)
    return compute_stats(events, days)


_MAX_DAYS = 365


def parse_days_param(raw: str | None, default: int = 30) -> int:
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("days must be an integer") from exc
    if value < 1:
        raise ValueError("days must be >= 1")
    if value > _MAX_DAYS:
        raise ValueError(f"days must be <= {_MAX_DAYS}")
    return value

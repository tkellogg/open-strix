# Watchers — Configuration & Mechanics

How to declare, configure, and debug the watcher system. For understanding
**when and why** to use watchers for behavioral monitoring, see
[debugging-algedonic.md](debugging-algedonic.md).

## watchers.json

Skills declare watchers in a `watchers.json` file alongside `SKILL.md`:

```json
{
  "watchers": [
    {
      "name": "codex-bypass",
      "command": "python check_codex_usage.py",
      "trigger": "turn_complete"
    },
    {
      "name": "daily-health",
      "command": "python health_check.py",
      "cron": "0 12 * * *"
    }
  ]
}
```

Each watcher must have `name`, `command`, and exactly one of:
- **`cron`** — fires on a schedule (same syntax as pollers/scheduler jobs)
- **`trigger`** — fires on an agent event

Having both `cron` and `trigger` is invalid. Having neither is also invalid.
The scheduler rejects malformed entries at discovery time.

### Valid Triggers

| Trigger | When It Fires | Latency Impact |
|---------|--------------|----------------|
| `turn_complete` | After the agent finishes processing an event | Zero — fires after response is already sent |
| `session_start` | When the agent session begins | Zero — runs alongside startup |
| `session_end` | When the agent session ends | Zero — runs during teardown |

### Input Contract

Event-triggered watchers receive a JSON object on **stdin**:

```json
{
  "trigger": "turn_complete",
  "trace_id": "20260326T213000Z-a1b2c3d4",
  "events_path": "/home/user/agent/logs/events.jsonl"
}
```

The watcher then:
1. Reads `events_path`
2. Filters by `trace_id` to scope to the current turn
3. Runs its analysis
4. Emits JSONL findings to **stdout**

This is deliberately minimal — the watcher has full access to `events.jsonl` and
can read as much historical context as it needs.

### Output Contract

Each line of stdout is a JSON finding:

```json
{"signal": "codex_bypass", "severity": "warn", "message": "Edited 3 code files without delegating to Codex", "route": "operator"}
```

Fields:
- **`signal`** — identifier for the finding type
- **`severity`** — `info`, `warn`, or `error`
- **`message`** — human-readable description
- **`route`** — where the signal goes: `"log"` (default), `"agent"`, `"operator"`

### Routing

| Route | Behavior |
|-------|----------|
| `log` | Written to events.jsonl as `watcher_signal`. Passive — operator checks when they want. |
| `agent` | Enqueued as a new agent event. The agent sees the watcher's message in its next turn. |
| `operator` | Logged. (Operator notification channel is deployment-specific — configure via env vars.) |

### Environment Variables

Watchers receive the same env vars as pollers:

| Variable | Description |
|----------|-------------|
| `STATE_DIR` | The skill directory (writable, for state files) |
| `WATCHER_NAME` | The watcher's name from watchers.json |

Plus custom env vars from the `env` field and the agent's existing environment.

## Debugging Watchers

### Watcher Not Firing

1. **Is the watcher discovered?** Check startup logs for `"Discovered N watchers"`.
2. **Is watchers.json valid?** Must have exactly one of `cron` or `trigger`.
3. **Is the trigger correct?** Only `turn_complete`, `session_start`, `session_end`.
4. **Is the command path correct?** Relative to the skill directory.

### Watcher Fires But No Output

1. **Does the script read stdin?** Event-triggered watchers MUST read `sys.stdin.readline()`.
2. **Does it exit cleanly?** Non-zero exit codes are logged but don't crash the agent.
3. **Is the JSON output valid?** Each line must be valid JSON. Invalid lines are skipped.

### Watcher Errors

Check events.jsonl for `watcher_error` events:

```bash
jq -s 'map(select(.type == "watcher_error")) | sort_by(.timestamp) | .[-10:]' logs/events.jsonl
```

## Backward Compatibility

`pollers.json` files continue to work. The `watchers.json` format is the preferred
way to declare both scheduled and event-triggered monitors going forward. Internally,
pollers and watchers use the same `WatcherConfig` class.

## Suggested Patterns

### Pattern 1: Cron-Based Review Watcher

Weekly behavioral audit — scans the last 7 days of events for trends:

```json
{
  "watchers": [
    {
      "name": "weekly-behavior-review",
      "command": "python weekly_review.py",
      "cron": "0 9 * * 1"
    }
  ]
}
```

Good for: behavioral drift detection, metric tracking, regression checks after
model upgrades. See [debugging-algedonic.md](debugging-algedonic.md) §3
(Behavioral Shift After Changes) for what to measure.

### Pattern 2: Multi-Signal Watcher

A single watcher can emit multiple findings per turn:

```python
for finding in findings:
    print(json.dumps(finding))
```

This avoids the overhead of spawning separate processes for related checks.
Group logically related detections (e.g., one watcher for all communication
anomalies: silent session + excessive messaging + channel confusion).

### Pattern 3: Stateful Watcher

Watchers can maintain state across invocations using files in `STATE_DIR`:

```python
state_dir = Path(os.environ["STATE_DIR"])
history_file = state_dir / "watcher_state.json"
```

Use cases: tracking rolling averages, counting consecutive violations,
maintaining baselines for before/after comparison. State files persist between
invocations — the watcher builds a picture over time rather than evaluating
each turn in isolation.

### Pattern 4: Threshold Escalation

Start with logging, escalate to operator notification if a pattern persists:

```python
state = load_state()
state["consecutive_violations"] = state.get("consecutive_violations", 0) + 1
save_state(state)

route = "log"
if state["consecutive_violations"] >= 3:
    route = "operator"

print(json.dumps({
    "signal": "repeated_violation",
    "severity": "warn" if route == "log" else "error",
    "message": f"Violation #{state['consecutive_violations']}: ...",
    "route": route,
}))
```

This prevents alert fatigue — the operator only sees findings that persist,
not one-off anomalies.

### Pattern 5: Baseline Comparison (Before/After)

For detecting behavioral changes after model upgrades or block edits:

```python
# Load historical baseline from STATE_DIR
baseline = json.loads((state_dir / "baseline.json").read_text())

# Compute current metrics from recent events
current = compute_metrics(events, window_days=7)

# Compare
for metric, baseline_val in baseline.items():
    current_val = current.get(metric, 0)
    delta = current_val - baseline_val
    if abs(delta) > baseline_val * 0.3:  # >30% change
        print(json.dumps({
            "signal": "behavioral_shift",
            "severity": "warn",
            "message": f"{metric}: {baseline_val:.1f} → {current_val:.1f} ({delta:+.1f})",
            "route": "log",
        }))
```

Create the baseline file manually or with a separate setup script. Update it
intentionally when you *want* behavior to change (model upgrade, new block).

### Pattern 6: Event Frequency Anomaly

Detect unusual spikes or drops in specific event types:

```python
# Count events by type in last N turns
recent_counts = Counter(e.get("type") for e in recent_events)
historical_avg = load_historical_averages()

for event_type, count in recent_counts.items():
    avg = historical_avg.get(event_type, count)
    if avg > 0 and count > avg * 3:  # 3x spike
        print(json.dumps({
            "signal": "frequency_anomaly",
            "severity": "info",
            "message": f"{event_type} count {count} vs avg {avg:.0f} (3x+)",
            "route": "log",
        }))
```

Good for: detecting tool call loops, send_message floods, or unexplained drops
in file_read events (agent stopped consulting its own memory).

### Pattern 7: Cross-Session Consistency

Cron watcher that checks whether the agent's behavior is consistent across
sessions within a time window:

```json
{
  "watchers": [
    {
      "name": "consistency-check",
      "command": "python consistency.py",
      "cron": "0 20 * * *",
      "env": {"WINDOW_DAYS": "3", "MIN_SESSIONS": "5"}
    }
  ]
}
```

Measures variance in key metrics (tool call count, response length, silence
rate) across sessions. High variance suggests the agent is behaving differently
depending on context in ways that may not be intentional.

## Example Watchers

Working examples in this skill's
[`watcher-examples/`](watcher-examples/) directory:

- [`watcher-examples/silent_session.py`](watcher-examples/silent_session.py) —
  Detects turns with 10+ tool calls but no `send_message` or `react` calls.
  Configurable threshold via `TOOL_CALL_THRESHOLD`. Implements the silent
  session pattern from [debugging-algedonic.md](debugging-algedonic.md) §2.

- [`watcher-examples/codex_bypass.py`](watcher-examples/codex_bypass.py) —
  Detects code edits without delegation to a coding agent. Configurable via
  `DELEGATION_TOOL` (default: `"codex"`) and `CODE_EXTENSIONS` (default:
  `.py,.js,.ts,.rs,.go,.java,.rb,.cpp,.c,.h`). Implements the agreement
  violation pattern from [debugging-algedonic.md](debugging-algedonic.md) §1.

## Validating Skill Structure

Use the DAG lint script to verify all files in a skill are reachable from
the root:

```bash
python builtin_skills/scripts/dag_lint.py builtin_skills/introspection --root SKILL.md --strict
```

This catches unreferenced files (dead subtrees) that were added but never
linked from any document. Run it after adding new watcher examples or
companion guides. The `--strict` flag exits with code 1 if orphans are found.

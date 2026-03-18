# Poller Design Patterns

Practical patterns for writing reliable pollers. Read SKILL.md first for the basics.

## Stdout Format — The Contract

Each line of stdout must be a self-contained JSON object. The scheduler reads stdout line by line, parses each as JSON, and delivers the `prompt` field to the agent as an event.

### Required Fields

| Field | Type | Description |
|-------|------|-------------|
| `prompt` | string | The text delivered to the agent. This is the only field the scheduler reads. |

### Optional Fields

| Field | Type | Description |
|-------|------|-------------|
| `poller` | string | Poller name. The scheduler ignores this (it uses the registered name from pollers.json), but it's useful in local event logs for filtering with jq. |
| `source_platform` | string | The platform this notification originated from (e.g., `"bluesky"`, `"github"`). Passed through to the agent as `source_platform` on the event. Lets the agent know where to reply — without it, the agent may respond on the wrong platform (e.g., replying on Discord to a Bluesky mention). |

### Example Output

```
{"poller": "bluesky-mentions", "source_platform": "bluesky", "prompt": "@user.bsky.social replied to your post: \"interesting take\"\nReply URI: at://did:plc:abc/app.bsky.feed.post/123 | CID: bafyabc"}
{"poller": "bluesky-mentions", "source_platform": "bluesky", "prompt": "@other.bsky.social mentioned you: \"cc @you.bsky.social\"\nPost URI: at://did:plc:def/app.bsky.feed.post/456 | CID: bafydef"}
```

### What the Scheduler Does With It

1. Reads stdout after the process exits (up to 200 lines)
2. Splits on newlines, skips blanks
3. Parses each line as JSON — invalid JSON is logged and skipped
4. Extracts the `prompt` field (must be a non-empty string)
5. Wraps it in an `AgentEvent(event_type="poller", prompt=..., scheduler_name=<registered name>)`
6. Delivers to the agent's event queue

Lines without a `prompt` field (or with an empty one) are silently dropped. No error, no event — just gone.

### Silence = Nothing to Report

If the poller has nothing actionable, output nothing to stdout. Zero lines = zero events. Don't emit `{"poller": "x", "prompt": "No new items"}` — that wastes an LLM call to process a non-event.

## State Management

Pollers are stateless processes that run on a cron schedule. Between runs, they need to remember what they've already seen. This is entirely the poller's responsibility — the scheduler doesn't track state for you.

### The Cursor Pattern

Every poller needs a cursor — a marker for "where I left off." Store it in `STATE_DIR`.

```python
STATE_DIR = Path(os.environ.get("STATE_DIR", "."))
CURSOR_FILE = STATE_DIR / "cursor.json"

def load_cursor():
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}

def save_cursor(cursor):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))
```

**Use timestamps, not IDs.** URIs and IDs can be deleted, reordered, or non-monotonic. Timestamps (`indexed_at`, `created_at`, `updated_at`) are stable and monotonically increasing.

```python
# Good — timestamp cursor
cursor = load_cursor()
last_seen = cursor.get("last_indexed_at")
for item in items:
    if last_seen and item.indexed_at <= last_seen:
        continue
    # process item...
    cursor["last_indexed_at"] = item.indexed_at
save_cursor(cursor)

# Bad — URI cursor (fragile)
if item.uri == last_uri:
    break  # What if this URI was deleted?
```

### Always Save the Cursor

Save cursor state even when there are no new items. This prevents re-processing if the cursor file was missing.

```python
# Always save, even on empty runs
save_cursor(cursor)
```

### Cursor Recovery

On first run (no cursor file), either:
- **Process nothing** — safest default, avoids flooding the agent with old items
- **Process last N items** — if you want to bootstrap with recent history

```python
cursor = load_cursor()
if not cursor:
    # First run: only process items from the last hour
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cursor["last_indexed_at"] = cutoff
```

### Use External Service State When Available

Some services already track what's been processed. When the external service provides per-application state tracking, use it instead of (or alongside) a local cursor — it's one fewer thing to manage and it stays consistent if your cursor file gets deleted.

**Examples of useful external state:**

- **Bluesky `updateSeen`** — marks notifications as seen. `listNotifications` returns a `seenAt` timestamp. Your poller can call `updateSeen` after processing, then on the next run, compare `notification.indexed_at > seenAt` to skip old ones.
- **GitHub "If-Modified-Since"** — returns 304 Not Modified when nothing changed. Saves you from parsing an unchanged response.
- **RSS `ETag` / `Last-Modified`** — same pattern as GitHub. The server tells you whether to bother.

```python
# Bluesky: use the service's seenAt as a secondary cursor
response = client.app.bsky.notification.list_notifications()
seen_at = response.seen_at  # server tracks this

for notif in response.notifications:
    if seen_at and notif.indexed_at <= seen_at:
        continue
    # process...

# Tell the service we've processed everything
client.app.bsky.notification.update_seen({"seenAt": now_iso()})
```

**When to use external state:**
- The service provides per-app or per-token tracking (not shared across all clients)
- You want resilience against local state loss (cursor file deleted, skill reinstalled)
- The service's state semantics match what you need (seen = processed)

**When to keep your own cursor:**
- The service's state is shared across clients you don't control (browser, mobile app)
- You need finer-grained tracking than the service offers
- The service doesn't provide state tracking at all

You can also use both — local cursor as primary, external state as a fallback. If the cursor file is missing on startup, check the service for a "last seen" marker instead of defaulting to "process nothing."

## Filtering

Pollers should be selective. The agent gets one event per stdout line, so noise = wasted LLM calls.

### Filter by Type

Most APIs return mixed notification types. Only emit the ones your agent can act on.

```python
# Good — selective
ACTIONABLE_TYPES = {"reply", "mention", "quote"}
for notif in notifications:
    if notif.reason not in ACTIONABLE_TYPES:
        continue

# Bad — everything including likes, follows, reposts
for notif in notifications:
    emit(format_notification(notif))  # Agent can't do anything with "New like from @user!"
```

### Don't Filter on Shared Read/Seen Status

Many APIs have an `is_read` or `seen` flag. If it's shared across all clients (browser, mobile, your poller), don't use it — it changes when anyone views the resource.

```python
# Bad — breaks if you view profile in a browser
if notif.is_read:
    continue

# Good — use your own cursor
if last_seen and notif.indexed_at <= last_seen:
    continue
```

**Exception:** If the service provides per-application state (e.g., Bluesky's `updateSeen` via your bot's auth token), that *is* safe to use. See "Use External Service State When Available" above.

## Notification Noise — Pain Adds Up

Every event you emit costs an LLM call. That's real money and real latency. But the deeper problem is **pain** — not yours, the agent's operator.

A poller that fires on likes, follows, and reposts doesn't just waste tokens. It trains the operator to ignore poller output. After the 50th "someone liked your post" notification they didn't ask for, they stop reading any of them — including the reply that actually needed a response.

This is the same dynamic as alert fatigue in ops. Too many pages and the on-call stops responding. The fix isn't better alert routing, it's fewer alerts.

**Concrete rules:**
- Only emit events the agent can meaningfully act on (reply, investigate, escalate)
- "Someone liked your post" is never actionable — don't emit it
- If you're unsure whether a notification type is actionable, leave it out. You can always add it later; you can't un-annoy the operator.
- Measure: if >50% of your emitted events result in no agent action, your filter is too loose

## Prompt Quality

The `prompt` field is what the agent sees. Make it actionable.

### Include Context for Action

If the agent needs to reply, it needs URIs and CIDs. If it needs to close an issue, it needs the issue number.

```python
# Good — agent has everything it needs to act
prompt = f'@{handle} replied to your post: "{text}"'
prompt += f"\nReply URI: {notif.uri} | CID: {notif.cid}"
prompt += f"\nOriginal post URI: {notif.reason_subject}"

# Bad — agent knows something happened but can't do anything
prompt = f"@{handle} replied: {text}"
```

### Don't Truncate

The urge to truncate comes from traditional apps with noisy neighbor problems — one user's data shouldn't crowd out another's. Pollers don't have this problem. Your context window is large and the data you're emitting is the signal the agent needs to act on.

Truncated text loses context that an LLM would use. A 300-character snippet of a reply thread strips the setup that makes the reply make sense. The agent doesn't degrade gracefully — it confidently misinterprets what's left.

```python
# Bad — solving a problem you don't have
text = (record.text[:300] + "...") if len(record.text) > 300 else record.text

# Good — just include the text
prompt = f'@{handle} replied: "{record.text}"'
```

If context size is genuinely a concern, the right fix is filtering at the source (emit fewer events), not trimming the content of each event. Dropping entire low-value notifications preserves full context on the ones that matter.

## Error Handling

Pollers run unattended. The key rule: **never emit on error.** A malformed event wastes an LLM call.

**Don't wrap everything in try/except.** Let Python crash naturally. The scheduler captures stderr and logs non-zero exits as `poller_nonzero_exit`. An unhandled exception gives you the full traceback — line numbers, call stack, variable context. A `print(f"API call failed: {e}")` gives you almost nothing.

```python
def main():
    client = create_client()  # Let it crash — traceback tells you why
    response = client.fetch_notifications()

    for item in process(response):
        emit(item)
```

If you do need to catch an exception (e.g., to save cursor state before exiting), **always include the traceback:**

```python
import traceback

try:
    response = client.fetch_notifications()
except Exception:
    traceback.print_exc()  # Full traceback to stderr, not just the message
    sys.exit(1)
```

## High-Frequency Pollers — Use Rust

If a poller runs very frequently (every minute or more), write it in Rust instead of Python.

The reason is **reliability**, not speed. A Python poller spins up a full VM on every invocation — interpreter, imported modules, garbage collector. For a poller that runs every 5 minutes, that overhead is negligible. For one that runs every 30 seconds, you're paying VM startup costs constantly. When the system is already low on memory or CPU, that thick process makes the poller itself less reliable — slower to start, more likely to get OOM-killed, more likely to miss its window.

Rust binaries have no runtime. A compiled poller starts, does its work, and exits. The memory footprint during execution is just the data it's processing. On a resource-constrained system, this is the difference between a poller that runs reliably at high frequency and one that degrades under load.

The poller contract is the same either way: read stdin/env, write JSONL to stdout, exit. The scheduler doesn't care what language produced the binary.

```toml
# pollers.json — Rust poller
{
  "my-fast-monitor": {
    "command": "./monitor",
    "cron": "*/1 * * * *",
    "env": {}
  }
}
```

**When to reach for Rust:**
- Polling interval ≤ 1 minute
- System is memory-constrained (small VPS, shared host)
- Poller is simple enough that Python's ecosystem advantages don't matter (most pollers are: fetch API, compare cursor, emit JSONL)

**When Python is fine:**
- Polling interval ≥ 5 minutes — VM startup cost is amortized
- Complex API client libraries only available in Python
- Rapid prototyping — get it working first, optimize later if frequency demands it

## Local Event Log

Optionally write events to a local JSONL file for debugging and history. This is separate from stdout (which the scheduler reads).

```python
EVENTS_FILE = STATE_DIR / "events.jsonl"

def emit(prompt):
    event = {"poller": POLLER_NAME, "prompt": prompt}
    # Stdout — scheduler picks this up
    print(json.dumps(event), flush=True)
    # Local log — stays for debugging
    with open(EVENTS_FILE, "a") as f:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        f.write(json.dumps(event) + "\n")
```

## File Layout

```
skills/my-monitor/
├── SKILL.md           ← skill metadata + docs
├── pollers.json       ← declares pollers (scheduler reads this)
├── poller.py          ← the script
├── cursor.json        ← cursor state (written by poller)
├── events.jsonl       ← optional local event log
└── requirements.txt   ← if the poller has Python dependencies
```

Keep all poller state in `STATE_DIR`. Don't write to random locations — it makes debugging hard and breaks if the skill is moved.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| URI-based cursors | URIs can be deleted or reordered | Use timestamps |
| Filtering on shared `is_read` | Changes when any client views the resource | Use your own cursor, or per-app service state (see "Use External Service State") |
| Emitting likes/follows | Agent can't act on these, trains operator to ignore output | Filter to actionable types |
| Truncating event text | LLM confidently misinterprets partial context | Include full text, filter at the source instead |
| Missing URI/CID in prompts | Agent can't reply or take action | Include identifiers |
| Wrapping everything in try/except | Swallows tracebacks, makes debugging impossible | Let it crash — stderr has the traceback |
| `except Exception as e: print(e)` | Loses line numbers, call stack, context | Use `traceback.print_exc()` if you must catch |
| Hardcoded paths | Breaks when moved or run by scheduler | Use STATE_DIR env var |
| Writing state outside STATE_DIR | Hard to debug, breaks portability | Keep everything in STATE_DIR |
| Emitting `"No new items"` to stdout | Wastes an LLM call on a non-event | Output nothing when there's nothing to report |
| Extra fields beyond `prompt` | Scheduler ignores them — false sense of structure | Only `prompt` and `source_platform` matter; use local event log for extras |
| Missing `source_platform` | Agent doesn't know where notification came from, may reply on wrong platform | Always include `source_platform` so the agent can route responses correctly |
| Python poller at ≤1min interval | VM startup overhead on every invocation — under resource pressure, thick process makes the poller unreliable | Write it in Rust — no runtime, reliable under load |
| Forgetting `reload_pollers` | Poller exists but scheduler doesn't know about it | Always call after creating/updating pollers.json |

# Events API

open-strix logs every significant event to `logs/events.jsonl`. This is the agent's self-diagnosis backbone — a complete record of what happened, when, and why. The agent can read its own event log, and the [introspection skill](../open_strix/builtin_skills/introspection/SKILL.md) teaches it how.

Conversation history is also persisted separately in `logs/chat-history.jsonl` as an append-only transcript of messages and reactions across Discord, the local web UI, and stdin sessions.

## Event Format

Every event is a single JSON line:

```json
{
  "timestamp": "2026-03-01T14:30:00.123456+00:00",
  "type": "tool_call",
  "session_id": "20260301T140000Z-a1b2c3d4",
  "...": "event-specific fields"
}
```

**Common fields (all events):**

| Field | Type | Description |
|-------|------|-------------|
| `timestamp` | string | ISO 8601 UTC timestamp |
| `type` | string | Event type identifier |
| `session_id` | string | `{UTC timestamp}-{8 hex chars}`, unique per process |

## Event Types

### Lifecycle Events

Events that track the agent process starting, connecting, and shutting down.

**`app_started`** — Process initialized. First event in any session.
```json
{"type": "app_started", "home": "/home/botuser/my-agent", "session_logs_cleaned": 0}
```

**`discord_connecting`** — About to connect to Discord.

**`discord_ready`** — Connected and operational.
```json
{"type": "discord_ready", "user": "MyAgent#1234"}
```

**`api_started`** — Loopback REST API is listening.
```json
{"type": "api_started", "port": 8082}
```

**`web_ui_started`** — Built-in local web chat is listening.
```json
{"type": "web_ui_started", "host": "127.0.0.1", "port": 8084, "channel_id": "local-web"}
```

**`app_shutdown_start`** / **`app_shutdown_complete`** — Graceful shutdown.

**`stdin_mode_start`** / **`stdin_mode_eof`** — Running without Discord (local testing).

### Message Events

**`discord_message`** — Incoming Discord message received.
```json
{
  "type": "discord_message",
  "channel_id": "1474541386467377273",
  "author": "realkellogh#0",
  "author_id": "1280588088925753364",
  "is_bot": false,
  "content": "hey check this out"
}
```
The `content` field contains the full message text.

**`web_message`** — Incoming message from the built-in local web chat.
```json
{
  "type": "web_message",
  "channel_id": "local-web",
  "author": "local_user",
  "author_id": "local-web-user",
  "channel_name": "Local Web",
  "channel_conversation_type": "dm",
  "channel_visibility": "private",
  "attachment_names": ["state/attachments/web/web-abc123-1-photo.png"],
  "content": "check this screenshot"
}
```

### Queue Events

Every event (messages, scheduler, API) goes through a queue before processing.

**`event_queued`** — Event added to the processing queue.
```json
{
  "type": "event_queued",
  "source_event_type": "scheduler",
  "channel_id": null,
  "scheduler_name": "morning-check",
  "queue_size": 1
}
```

**`event_deduped`** — Duplicate scheduler event dropped (same job already queued).
```json
{"type": "event_deduped", "key": "scheduler:morning-check"}
```

### Agent Invocation Events

**`agent_invoke_start`** — Agent begins processing an event. This is when the LLM call starts.
```json
{
  "type": "agent_invoke_start",
  "source_event_type": "discord_message",
  "channel_id": "1474541386467377273",
  "scheduler_name": null
}
```

**`agent_final_message_discarded`** — Agent's last text output was discarded (standard behavior — agents communicate via tools, not final text).
```json
{
  "type": "agent_final_message_discarded",
  "source_event_type": "scheduler",
  "channel_id": null,
  "final_text": "Task complete. Updated state files."
}
```

### Tool Call Events

Every tool invocation is logged. This is the most frequent event type.

**`tool_call`** — Successful tool execution.
```json
{
  "type": "tool_call",
  "tool": "send_message",
  "channel_id": "1474541386467377273",
  "text_length": 142,
  "attachment_names": []
}
```

Tool-specific fields vary:

| Tool | Extra Fields |
|------|-------------|
| `send_message` | `channel_id`, `text_length`, `attachment_names` |
| `list_messages` | `channel_id`, `limit`, `source` (`discord_api` or `remember_db`), `returned` |
| `bash` | `exit_code`, `stdout_length`, `stderr_length` |
| `read_file` / `write_file` | (args logged directly) |
| `react` | `emoji`, `channel_id`, `message_id` |
| `journal` | (no extra fields) |
| `fetch_url` | `url`, `status_code`, `content_length` |
| `web_search` | `query`, `result_count` |
| `lookup` | `query`, `results` |
| Memory tools | `block_id` |
| Schedule tools | `name`, `count` |

**`tool_call_error`** — Tool execution failed.
```json
{
  "type": "tool_call_error",
  "tool": "fetch_url",
  "url": "https://example.com/page",
  "error_type": "validation_error",
  "error": "download exceeded max_bytes=15000"
}
```

Error types: `timeout`, `missing_shell_binary`, `validation_error`, `empty_message`, HTTP errors.

### Communication Safety Events

**`send_message_loop_detected`** — Agent sent multiple messages in quick succession (soft limit, warning).
```json
{
  "type": "send_message_loop_detected",
  "tool": "send_message",
  "channel_id": "1474541386467377273",
  "streak": 5
}
```

**`send_message_loop_hard_stop`** — Agent hit the hard limit (10 messages). Turn terminated.
```json
{
  "type": "send_message_loop_hard_stop",
  "tool": "send_message",
  "channel_id": "1474541386467377273",
  "streak": 10
}
```

### Git Events

**`git_sync_after_turn`** — Post-turn git commit and push.
```json
{
  "type": "git_sync_after_turn",
  "source_event_type": "discord_message",
  "channel_id": "1474541386467377273",
  "git_sync": "ok: committed and pushed"
}
```

Possible `git_sync` values: `"ok: committed and pushed"`, `"ok: nothing to commit"`, or an error string.

### Scheduler Events

**`scheduler_reloaded`** — Scheduler re-read `scheduler.yaml` (happens on startup and after schedule changes).
```json
{"type": "scheduler_reloaded", "jobs": 3}
```

**`scheduler_invalid_job`** / **`scheduler_invalid_cron`** / **`scheduler_invalid_time`** — Job definition errors.

### Infrastructure Events

**`phone_book_populated`** — Phone book auto-populated from Discord guild data.
```json
{"type": "phone_book_populated", "entries": 12}
```

**`discord_history_refreshed`** — Discord message history fetched for remember DB.
```json
{
  "type": "discord_history_refreshed",
  "channel_id": "1474541386467377273",
  "added": 47
}
```

**`discord_history_refresh_error`** — Failed to fetch Discord history.

**`typing_indicator_start`** / **`typing_indicator_stop`** — Discord typing indicator lifecycle.

### Error Events

**`error`** — Unhandled exception during event processing.
```json
{
  "type": "error",
  "source_event_type": "discord_message",
  "error": "ConnectionResetError: ...",
  "reacted_to_last_user_message": true
}
```

**`warning`** — Non-fatal issues. Includes `where` and `warning_type` fields.
```json
{
  "type": "warning",
  "where": "event_worker",
  "warning_type": "send_message_loop_hard_stop",
  "source_event_type": "scheduler",
  "error": "..."
}
```

## Querying Events

Events are append-only JSONL. Use `jq` for analysis:

```bash
# Recent events
tail -20 logs/events.jsonl | jq '.'

# Count events by type
cat logs/events.jsonl | jq -r '.type' | sort | uniq -c | sort -rn

# Find errors
jq 'select(.type == "error" or .type == "tool_call_error")' logs/events.jsonl

# Tool call frequency
jq -r 'select(.type == "tool_call") | .tool' logs/events.jsonl | sort | uniq -c | sort -rn

# Events from a specific session
jq --arg sid "20260301T140000Z-a1b2c3d4" 'select(.session_id == $sid)' logs/events.jsonl

# Messages in a specific channel
jq --arg ch "1474541386467377273" 'select(.type == "discord_message" and .channel_id == $ch)' logs/events.jsonl
```

## REST API

When `api_port` is set in `config.yaml` (default: `0` = disabled), open-strix runs a loopback HTTP server for injecting events programmatically.

### `POST /api/event`

Send an event to the agent's processing queue.

```bash
curl -X POST http://127.0.0.1:8082/api/event \
  -H "Content-Type: application/json" \
  -d '{"source": "my-script", "prompt": "Check the latest deployment status"}'
```

**Request body:**

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | yes | The instruction for the agent |
| `source` | no | Label for the event source (appears in logs as `api:{source}`) |
| `channel_id` | no | Discord channel to respond in (if omitted, agent responds via tools only) |

**Response:**
```json
{"status": "queued", "source": "my-script"}
```

The event enters the same queue as Discord messages and scheduler triggers. It's processed in order — if the agent is busy, it waits.

### `GET /api/health`

```bash
curl http://127.0.0.1:8082/api/health
```

Returns `{"status": "ok"}` if the agent is running.

### Configuration

In `config.yaml`:
```yaml
api_port: 8082  # 0 = disabled (default)
```

The API binds to `127.0.0.1` only — not accessible from the network. For external access, put it behind a reverse proxy with authentication.

## Local Web UI

When `web_ui_port` is set in `config.yaml` (default: `0` = disabled), open-strix serves a built-in 1:1 chat UI.

### `GET /`

Returns the browser chat UI.

### `GET /api/messages`

Returns the current `local-web` transcript plus a boolean `is_processing` flag.

### `POST /api/messages`

Queues a user message from the browser. Supports either JSON:

```json
{"text": "hello from the browser"}
```

or multipart form data with `text` plus one or more `files`.

### `GET /files/{path}`

Serves a file attachment that was already shared in the `local-web` conversation.

### Configuration

In `config.yaml`:
```yaml
web_ui_port: 8084          # 0 = disabled (default)
web_ui_host: 127.0.0.1     # set 0.0.0.0 if you want LAN access
web_ui_channel_id: local-web
```

### Use Cases

- **Bluesky pollers** — external script monitors notifications, sends relevant ones to the agent via API
- **CI/CD hooks** — trigger agent review on pull requests
- **Cross-agent communication** — one agent sends events to another
- **Monitoring alerts** — external systems notify the agent of issues

## Session Logs

In addition to events.jsonl, each agent turn creates a detailed session log at `logs/sessions/{session_id}/{timestamp}_{event_type}.json`. These contain:

- The full rendered prompt
- Complete message trace (all LLM messages and tool calls)
- Event metadata

Session logs auto-clean after `session_log_retention_days` (default: 30 days).

## Chat History (chat-history.jsonl)

This file is append-only. Each line is either a message record or a reaction record.

Message example:

```json
{
  "timestamp": "2026-03-01T14:30:00.123456+00:00",
  "type": "message",
  "channel_id": "local-web",
  "message_id": "web-abc123",
  "author": "local_user",
  "is_bot": false,
  "source": "web",
  "content": "check this screenshot",
  "attachments": ["state/attachments/web/web-abc123-1-shot.png"]
}
```

Reaction example:

```json
{
  "timestamp": "2026-03-01T14:31:00.123456+00:00",
  "type": "reaction",
  "channel_id": "local-web",
  "message_id": "web-abc123",
  "emoji": "👍"
}
```

## Journal (journal.jsonl)

Separate from events. The journal is the agent's own narrative log, written via the `journal` tool:

```json
{
  "timestamp": "2026-03-01T14:30:00.123456+00:00",
  "session_id": "20260301T140000Z-a1b2c3d4",
  "channel_id": "1474541386467377273",
  "user_wanted": "Check Bluesky engagement on recent posts",
  "agent_did": "Scanned 15 posts, updated tracking file, sent summary",
  "predictions": "Sycophancy thread will cross 20 likes by Friday"
}
```

The last N journal entries (configurable via `journal_entries_in_prompt`) appear in every prompt, giving the agent temporal context. Predictions are revisited later — this is the calibration loop.

## Log Rotation

Events and journal logs grow indefinitely. For long-running agents:

```bash
# Check size
wc -l logs/events.jsonl logs/journal.jsonl

# Archive old events (keep last 10K)
tail -10000 logs/events.jsonl > logs/events.jsonl.tmp && mv logs/events.jsonl.tmp logs/events.jsonl
```

Session logs auto-clean, but events.jsonl and journal.jsonl do not. Plan to rotate them when they get large (>1MB is a reasonable threshold).

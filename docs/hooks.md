# Hooks

Hooks are command-line extensions declared by skills. They let a skill observe or modify runtime events without changing open-strix core code.

Hooks are discovered from `skills/**/hooks.json` at startup and whenever the agent calls `reload_hooks`.

## hooks.json Schema

```json
{
  "hooks": [
    {
      "name": "message-policy",
      "command": "uv run python hook.py",
      "events": ["pre_tool_call", "post_tool_call", "pre_prompt"],
      "env": {
        "POLICY_MODE": "strict"
      },
      "timeout_seconds": 10,
      "include_conversation": false
    }
  ]
}
```

Fields:

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Stable hook name used in logs and `HOOK_NAME`. |
| `command` | yes | Shell command to run from the skill directory. |
| `events` | yes | Hook events to receive. Use an array. A single `event` string is also accepted. |
| `env` | no | Extra non-secret environment variables. The process environment is inherited. |
| `timeout_seconds` | no | Per-hook timeout. Defaults to `10`. Invalid or non-positive values fall back to `10`. |
| `include_conversation` | no | When true, include full in-memory conversation history in the hook event. Defaults to `false`. |

Valid hook events:

| Event | When it runs | Can modify |
|-------|--------------|------------|
| `pre_tool_call` | Before every registered tool call, after tool input has passed schema validation. | `args` |
| `post_tool_call` | After every successful tool call, or after a tool raises. | `result` on success |
| `pre_prompt` | After open-strix renders the turn prompt, before the LLM is invoked. | `prompt`, `append_prompt` |
| `pre_startup` | Early in `OpenStrixApp.run()`, before MCP, scheduler, UI plugins, and APIs start. | No runtime effect |
| `post_startup` | After scheduler/UI/API startup and `app_started`, before Discord/web/stdin blocking mode. | No runtime effect |
| `pre_shutdown` | At the start of shutdown, after `app_shutdown_start` is logged. | No runtime effect |
| `post_shutdown` | After services stop and caches are cleaned, before `app_shutdown_complete`. | No runtime effect |

There are deliberately no tool-name filters in `hooks.json`. If a hook only cares about `send_message`, register for `pre_tool_call` or `post_tool_call` and filter inside the script:

```python
import json
import sys

event = json.loads(sys.stdin.readline())
if event.get("tool") != "send_message":
    print(json.dumps(event))
    raise SystemExit

event["args"]["text"] = event["args"]["text"].strip()
print(json.dumps(event))
```

## Runtime Contract

For each matching hook, open-strix starts the hook command with:

| Environment variable | Value |
|----------------------|-------|
| `STATE_DIR` | Absolute path to the directory containing `hooks.json`. |
| `HOOK_NAME` | The hook's `name`. |
| `HOOK_EVENT` | The current hook event type. |

The command receives exactly one JSON object plus a newline on stdin. If it writes a JSON object to stdout, that object replaces the event for the next hook in the chain. If it writes nothing to stdout, open-strix treats the hook as a no-op.

Stderr is captured into `hook_stderr` events. Non-zero exit, timeout, invalid JSON output, or malformed mutations are logged and ignored; the original event continues through the pipeline.

## Conversation Context

Hooks do not receive full conversation history by default. Set `include_conversation: true` on a hook entry to opt in:

```json
{
  "hooks": [
    {
      "name": "retrieval-context",
      "command": "uv run python retrieve.py",
      "events": ["pre_prompt"],
      "include_conversation": true
    }
  ]
}
```

The event then includes:

```json
{
  "conversation": {
    "current_channel_id": "123",
    "channel_messages": [
      {
        "timestamp": "2026-03-01T14:29:00+00:00",
        "author": "alice",
        "content": "what did we decide last week?",
        "source": "discord"
      }
    ],
    "all_messages": []
  }
}
```

When `logs/chat-history.jsonl` exists, `all_messages` is loaded from that persisted chat transcript and `channel_messages` is filtered from it. During very early startup or in tests without the log file, open-strix falls back to the in-memory conversation buffers.

## Tool Events

`pre_tool_call` receives:

```json
{
  "type": "pre_tool_call",
  "timestamp": "2026-03-01T14:30:00.123456+00:00",
  "session_id": "20260301T140000Z-a1b2c3d4",
  "tool": "send_message",
  "args": {
    "text": "hello",
    "channel_id": "123"
  },
  "channel_id": "123",
  "current_event": "discord_message"
}
```

If the hook returns a JSON object with an `args` object, those args are used for the actual tool call. `args` must remain a JSON object. If it does not, open-strix logs `hook_invalid_mutation` and keeps the original args.

`post_tool_call` receives successful results like:

```json
{
  "type": "post_tool_call",
  "tool": "send_message",
  "args": {
    "text": "hello",
    "channel_id": "123"
  },
  "status": "success",
  "result": "send_message complete (sent=True, chunks=1, attachments=0, git_sync=deferred)",
  "duration_seconds": 0.034812,
  "channel_id": "123",
  "current_event": "discord_message"
}
```

If a successful post hook returns a JSON object with `result`, that value is returned to the agent as the tool result.

If the tool raises, `post_tool_call` receives:

```json
{
  "type": "post_tool_call",
  "tool": "fetch_url",
  "args": {
    "url": "https://example.invalid"
  },
  "status": "error",
  "error": "network failed",
  "error_class": "RuntimeError",
  "channel_id": "123",
  "current_event": "discord_message"
}
```

Error post hooks are observational. Returning a modified event does not suppress the original exception.

## Prompt Events

`pre_prompt` receives the fully rendered prompt before the model is invoked:

```json
{
  "type": "pre_prompt",
  "prompt": "Current event: ...",
  "source_event_type": "discord_message",
  "channel_id": "123",
  "author": "alice"
}
```

A hook can replace the prompt:

```python
import json
import sys

event = json.loads(sys.stdin.readline())
event["prompt"] = event["prompt"] + "\n\nRelevant memory:\n- ..."
print(json.dumps(event))
```

Or append context without copying the prompt:

```python
import json
import sys

event = json.loads(sys.stdin.readline())
event["append_prompt"] = "Relevant similar conversations:\n- ..."
print(json.dumps(event))
```

This is the intended OOB retrieval path: a `pre_prompt` hook can read the conversation, run a local vector search, and append relevant context before the agent spends any tool calls.

## Lifecycle Events

Lifecycle events carry `home`, plus stage-specific fields:

```json
{
  "type": "post_startup",
  "home": "/home/bot/my-agent",
  "api_port": 8082,
  "web_ui_port": 8084,
  "scheduler_running": true
}
```

Use lifecycle hooks for local setup and teardown: starting sidecar processes that are not UI plugins, writing readiness files, registering local OS affordances, or flushing metrics.

## Reloading

After adding or editing a hook manifest, ask the agent to call:

```text
reload_hooks()
```

The tool re-scans all `skills/**/hooks.json` files. Already wrapped tools reference the same manager, so new hook registrations take effect without recreating the agent.

## Design Notes

- Hooks are ordered by sorted `hooks.json` path, then by entry order within each file.
- Each hook command is short-lived. Long-running daemons should be UI plugins, supervisor jobs, OS services, or pollers.
- Hooks run for all registered event types. Keep filtering in the script explicit.
- Keep hook output to one JSON object. Log debugging details to stderr.
- Store secrets in the agent environment or `.env`, not in `hooks.json`.

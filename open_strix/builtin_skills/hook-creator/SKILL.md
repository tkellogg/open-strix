---
name: hook-creator
description: Create and manage runtime command hooks declared in skills/*/hooks.json. Use when the user asks to intercept tool calls, augment prompts, audit or mutate tool arguments/results, add startup/shutdown scripts, or debug hook behavior.
---

# Hooks

Hooks are short-lived commands that receive one runtime event as JSON on stdin and may write one JSON object to stdout to replace it. They live in skills as `hooks.json` files and are discovered at startup and when `reload_hooks` is called.

Use hooks for small runtime adapters:

- pre/post tool-call policy
- prompt augmentation before the model is invoked
- send_message shaping or audit
- metrics and local logging
- startup/shutdown setup and teardown

Do not use hooks for long-running services. Use UI plugins, pollers, supervisor jobs, or OS services for that.

## hooks.json

```json
{
  "hooks": [
    {
      "name": "message-policy",
      "command": "uv run python hook.py",
      "events": ["pre_tool_call", "post_tool_call", "pre_prompt"],
      "env": {},
      "timeout_seconds": 10,
      "include_conversation": false
    }
  ]
}
```

Valid events:

- `pre_tool_call`
- `post_tool_call`
- `pre_prompt`
- `pre_startup`
- `post_startup`
- `pre_shutdown`
- `post_shutdown`

There are no tool-name filters in `hooks.json`. Filter explicitly in the hook script:

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

## Contract

The hook process runs from the directory containing `hooks.json`.

Environment:

- `STATE_DIR`: the skill directory
- `HOOK_NAME`: the hook name
- `HOOK_EVENT`: the event type

Input:

- exactly one JSON object plus a newline on stdin

Output:

- stdout empty means no-op
- stdout containing a JSON object replaces the event for the next hook
- stderr is logged as `hook_stderr`

For `pre_tool_call`, returning an event with an `args` object changes the tool args before execution. For `post_tool_call`, returning an event with `result` changes the result shown to the agent after a successful tool call. For `pre_prompt`, returning `prompt` replaces the prompt, and returning `append_prompt` appends text to the prompt.

### Blocking a tool call with `_block`

A `pre_tool_call` hook may **reject the call entirely** by returning an event with an `_block` object:

```python
import json
import sys

event = json.loads(sys.stdin.readline())
if event.get("tool") == "send_message" and looks_bad(event["args"]):
    event["_block"] = {
        "reason": "shell-leak-prefix",
        "result": "[hook blocked] send_message text starts with `$(...)` — that ships as a literal string, not a shell command. Read the file and inline the contents, or attach via attachment_paths.",
    }
print(json.dumps(event))
```

When `_block` is set with both `reason` (string) and `result` (string), the wrapped tool is **not invoked**. The agent receives `result` as if it were the tool's own return value — so the agent sees the diagnostic next turn and can self-correct. A `hook_blocked_tool_call` event is logged, and post_tool_call hooks see `status="blocked"` with the same synthetic result.

This is the right verb when a hook needs to **prevent** a bad call rather than mutate it. Args-mutation is fine when the call should still happen but with different inputs; `_block` is for "this call must not happen — tell the agent why."

Invalid `_block` specs (missing fields, wrong types) are logged via `hook_invalid_block` and the call proceeds normally. This guards against silent drops from malformed hook output.

Set `include_conversation: true` only for hooks that need transcript context. The hook event then includes:

- `conversation.all_messages`
- `conversation.channel_messages`
- `conversation.current_channel_id`

When `logs/chat-history.jsonl` exists, this is loaded from the persisted chat transcript. This is intentionally opt-in because full transcripts can be large.

## Reload

After creating or editing `hooks.json`, call:

```text
reload_hooks()
```

## Debugging

Check `logs/events.jsonl` for:

- `hook_invalid_json`
- `hook_invalid_format`
- `hook_missing_fields`
- `hook_timeout`
- `hook_exec_error`
- `hook_stderr`
- `hook_nonzero_exit`
- `hook_blocked_tool_call`
- `hook_invalid_block`
- `hook_invalid_mutation`
- `hook_invalid_output`
- `hook_invalid_mutation`

Keep hooks small and deterministic. Short scripts are easier to reason about than daemons hiding in the tool path.

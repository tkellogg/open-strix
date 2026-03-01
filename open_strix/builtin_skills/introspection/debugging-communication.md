# Debugging Communication Patterns

## Quick Health Check

```bash
# Recent send_message events
jq -s 'map(select(.tool == "send_message")) | sort_by(.timestamp) | .[-10:]' logs/events.jsonl

# Any circuit breaker triggers?
jq -s 'map(select(.type | test("send_message_loop")))' logs/events.jsonl

# Messages per session (find chatty sessions)
jq -s '[.[] | select(.tool == "send_message" and .sent == true)] | group_by(.session_id) | map({session: .[0].session_id, messages: length}) | sort_by(-.messages)' logs/events.jsonl
```

## Common Problems

### Silent Failure (Agent Ran But Sent Nothing)

**Symptoms:** Session exists in events.jsonl but no message was sent to Discord.

This is one of the most frustrating failure modes. The agent appears to ignore
the user entirely.

**Diagnosis steps:**

1. **Find sessions without send_message:**
   ```bash
   jq -s '
     group_by(.session_id) | map({
       session: .[0].session_id,
       events: length,
       sent: [.[] | select(.tool == "send_message" and .sent == true)] | length,
       errors: [.[] | select(.type | test("error"))] | length
     }) | map(select(.sent == 0 and .events > 3)) | sort_by(-.events)
   ' logs/events.jsonl
   ```

2. **Trace the silent session:**
   ```bash
   jq -s 'map(select(.session_id == "SESSION_ID")) | sort_by(.timestamp)' logs/events.jsonl
   ```
   Look for:
   - Tool errors that terminated the session
   - Timeout events
   - The session starting but never reaching a send_message call
   - Heavy parallel tool calls (WebFetch, WebSearch) that may have timed out

3. **Check if the agent journaled:**
   ```bash
   jq -s 'map(select(.session_id == "SESSION_ID"))' logs/journal.jsonl
   ```
   If journal exists, the agent completed its turn — it just didn't send a message.

**Prevention:** Always send an acknowledgment message before starting heavy work
(web fetches, file processing, subagent tasks). If the heavy work fails, the user
at least knows you received the request.

### Circuit Breaker Triggered

**Symptoms:** Agent stops sending messages mid-conversation. May react with a
warning emoji (⚠️) or error emoji.

**Diagnosis:**

```bash
# Find all circuit breaker events
jq -s 'map(select(.type | test("send_message_loop"))) | sort_by(.timestamp)' logs/events.jsonl
```

The circuit breaker fires when consecutive send_message calls have high text
similarity (the agent is repeating itself). Two levels:

- **Soft limit** — Messages are paused for the current turn. Agent gets a warning.
- **Hard limit (streak=10)** — Turn is terminated entirely. Error emoji reaction.

**Common causes:**
- Agent stuck in a retry loop (same message failing, re-attempting)
- Agent generating very similar messages in a loop
- Overly long responses being chunked identically

**Fix:** The circuit breaker is a safety mechanism. If it fires, the agent's
behavior in that session was genuinely problematic. Check the session's
`text_preview` fields to see what was being repeated.

### Message Sent to Wrong Channel

**Symptoms:** Message appears in unexpected channel, or doesn't appear where expected.

**Diagnosis:**

```bash
# Check channel_id for recent sends
jq -s 'map(select(.tool == "send_message")) | sort_by(.timestamp) | .[-10:] | map({ts: .timestamp, channel: .channel_id, preview: .text_preview[:80]})' logs/events.jsonl
```

**Common causes:**
- `channel_id` parameter not passed to send_message (defaults to current event channel)
- Scheduler job fired without a `channel_id` (may default to last active channel or fail)
- Hardcoded channel ID that's wrong or outdated

**Fix:** Always pass explicit `channel_id` when sending to a specific channel.
For scheduler jobs, include `channel_id` in the job definition.

### Duplicate Messages

**Symptoms:** Same or near-identical message appears twice in Discord.

**Diagnosis:**

```bash
# Find sends with similar previews in same channel
jq -s '
  [.[] | select(.tool == "send_message" and .sent == true)] |
  sort_by(.timestamp) |
  group_by(.channel_id) |
  map(map({ts: .timestamp, preview: .text_preview[:100], session: .session_id}))
' logs/events.jsonl
```

**Common causes:**
- Agent ran twice for the same trigger (scheduler overlap, double Discord event)
- Long message chunked by Discord's 2000-char limit — each chunk is a separate
  send_message but this is expected behavior
- Session crashed and restarted, re-executing the same work

**Check chunks field:** `sent_chunks > 1` means the message was split. This is
normal for long messages.

### React Not Working

**Symptoms:** Agent calls react but no emoji appears on the message.

**Diagnosis:**

```bash
jq -s 'map(select(.tool == "react")) | sort_by(.timestamp) | .[-10:]' logs/events.jsonl
```

**Common causes:**
- Invalid `message_id` — message was deleted or ID is wrong
- Invalid `channel_id` — channel doesn't exist or bot lacks access
- Emoji not supported — custom server emojis may not be available to the bot
- Bot lacks "Add Reactions" permission in the target channel

## Pattern Analysis

### Engagement Audit

How much is the agent talking vs listening?

```bash
# Messages sent per channel per day
jq -s '
  [.[] | select(.tool == "send_message" and .sent == true)] |
  map({
    date: (.timestamp | split("T")[0]),
    channel: .channel_id
  }) |
  group_by(.date) |
  map({
    date: .[0].date,
    total: length,
    by_channel: (group_by(.channel) | map({channel: .[0].channel, count: length}))
  })
' logs/events.jsonl
```

### Response Time

How quickly does the agent respond after receiving a message?

```bash
# Pair scheduler/message triggers with first send_message in same session
jq -s '
  group_by(.session_id) | map(
    {
      session: .[0].session_id,
      start: (map(.timestamp) | sort | first),
      first_send: ([.[] | select(.tool == "send_message" and .sent == true) | .timestamp] | sort | first)
    }
  ) | map(select(.first_send != null))
' logs/events.jsonl
```

### Over-Communication Detection

Is the agent talking too much relative to meaningful content?

```bash
# Sessions with high message counts
jq -s '
  [.[] | select(.tool == "send_message" and .sent == true)] |
  group_by(.session_id) |
  map({session: .[0].session_id, count: length}) |
  sort_by(-.count) |
  .[:10]
' logs/events.jsonl
```

If a session has 5+ messages, review the text_preview fields. Might be appropriate
(multi-step task) or might be over-communicating (could have been one message).

### Channel Confusion Audit

Is the agent mixing up which channel to talk in?

```bash
# Unique channels per session
jq -s '
  [.[] | select(.tool == "send_message")] |
  group_by(.session_id) |
  map({
    session: .[0].session_id,
    channels: ([.[].channel_id] | unique)
  }) |
  map(select(.channels | length > 1))
' logs/events.jsonl
```

Sessions sending to multiple channels may be correct (cross-channel work) or
confused (responding in the wrong place).

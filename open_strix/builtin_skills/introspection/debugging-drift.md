# Debugging Behavioral Drift

Behavioral drift is when an agent's behavior changes gradually — often after model swaps,
block updates, or scaffolding edits — without any single event being obviously wrong.
It's harder to catch than a crash or a silent failure because each individual session
looks fine. The problem only appears in aggregate.

## When to Suspect Drift

- After a model version change (e.g., M2.5 → M2.7)
- After significant memory block or persona edits
- When someone says the agent "sounds different"
- When the agent is unexpectedly silent or unexpectedly chatty

## Response Rate Tracking

The most reliable drift signal is **messages sent vs messages seen**. An agent that
suddenly responds to 20% of messages instead of 60% has drifted — even if every
individual "stayed quiet" decision seems reasonable.

### Measure It

```bash
# Messages sent per day (last 7 days)
jq -s '
  [.[] | select(.tool == "send_message" and .sent == true)] |
  map({date: (.timestamp | split("T")[0])}) |
  group_by(.date) |
  map({date: .[0].date, sent: length})
' logs/events.jsonl

# Journal entries with "stayed quiet" or "no action" (silence decisions)
jq -s '
  [.[] | select(.agent_did | test("(?i)stayed quiet|no action|no message|silence|no reply"))] |
  map({date: (.timestamp | split("T")[0])}) |
  group_by(.date) |
  map({date: .[0].date, silent: length})
' logs/journal.jsonl

# Ratio: silence decisions / total sessions per day
jq -s '
  group_by(.timestamp | split("T")[0]) |
  map({
    date: .[0].timestamp | split("T")[0],
    total: length,
    silent: [.[] | select(.agent_did | test("(?i)stayed quiet|no action|no message"))] | length
  }) |
  map(. + {silence_rate: (if .total > 0 then (.silent / .total * 100 | round) else 0 end)})
' logs/journal.jsonl
```

### What to Look For

- **Sudden silence rate increase after model change** — the new model is interpreting
  engagement rules more literally. Fix: loosen the trigger threshold in communication blocks.
- **Gradual silence rate increase over weeks** — memory blocks are accumulating
  "be careful" instructions without corresponding "engage when" instructions.
- **Silence rate differs by channel** — the agent may have learned to avoid one context.

## Cross-Platform Routing Audit

When a poller delivers a notification from Platform A, did the agent respond on
Platform A or somewhere else?

### Measure It

```bash
# Find poller events and the first send_message in the same session
jq -s '
  group_by(.session_id) |
  map({
    session: .[0].session_id,
    poller_source: ([.[] | select(.type == "turn_start" and .event.event_type == "poller")] | first | .event.scheduler_name // "none"),
    source_platform: ([.[] | select(.type == "turn_start" and .event.source_platform)] | first | .event.source_platform // "none"),
    responded_channel: ([.[] | select(.tool == "send_message" and .sent == true)] | first | .channel_id // "none")
  }) |
  map(select(.poller_source != "none"))
' logs/events.jsonl
```

### What to Look For

- **Bluesky notification → Discord response** — the agent defaulted to its primary
  channel instead of replying on the originating platform. This is the most common
  routing bug. Fix: include `source_platform` in the poller output (see pollers
  design-patterns.md), and add explicit routing instructions to the agent's blocks.
- **Consistent routing to one platform** — the agent may not have the tools or
  instructions to respond on other platforms at all.

## Model Change Comparison

After swapping models, compare behavioral patterns before and after.

### Quick Before/After

```bash
# Find the approximate changeover timestamp (check git log or journal)
# Then compare message volume before vs after

# Messages per session — before changeover
jq -s '
  [.[] | select(.timestamp < "CHANGEOVER_ISO" and .tool == "send_message" and .sent == true)] |
  group_by(.session_id) |
  map({session: .[0].session_id, messages: length}) |
  {sessions: length, total_messages: (map(.messages) | add), avg: (map(.messages) | add / length)}
' logs/events.jsonl

# Messages per session — after changeover
jq -s '
  [.[] | select(.timestamp >= "CHANGEOVER_ISO" and .tool == "send_message" and .sent == true)] |
  group_by(.session_id) |
  map({session: .[0].session_id, messages: length}) |
  {sessions: length, total_messages: (map(.messages) | add), avg: (map(.messages) | add / length)}
' logs/events.jsonl
```

### Deeper: Topic Engagement Shift

```bash
# What topics does the agent engage with before vs after?
# Uses journal topics field

# Before
jq -s '
  [.[] | select(.timestamp < "CHANGEOVER_ISO")] |
  map(.topics // "" | split(",") | map(ltrimstr(" "))) | flatten |
  group_by(.) | map({topic: .[0], count: length}) | sort_by(-.count) | .[:20]
' logs/journal.jsonl

# After
jq -s '
  [.[] | select(.timestamp >= "CHANGEOVER_ISO")] |
  map(.topics // "" | split(",") | map(ltrimstr(" "))) | flatten |
  group_by(.) | map({topic: .[0], count: length}) | sort_by(-.count) | .[:20]
' logs/journal.jsonl
```

### What to Look For

- **Lower messages-per-session after model swap** — new model is more conservative.
  This is the Motley M2.5→M2.7 pattern: same voice, higher trigger threshold.
- **Topic distribution shift** — the agent engages with different subjects. May be
  fine (new model has different strengths) or may indicate drift from persona.
- **Tool usage shift** — the new model may prefer different tools or use them differently.

## The Key Insight

Introspection currently treats the agent as **static** — "here's how to debug what
happened in this session." But agents **change over time**. Model swaps are the most
obvious change, but scaffolding edits, block updates, and even conversation patterns
can shift behavior.

The queries above give you a **longitudinal view** — comparing this week's patterns
to last week's. This catches drift before someone notices "they sound different" in chat.

## Checklist: After a Model Change

1. Note the changeover timestamp
2. Run response rate comparison (before vs after)
3. Check silence rate trend
4. Compare topic engagement distribution
5. Monitor for 48 hours before concluding the new model is stable
6. If drift detected: adjust blocks/persona, don't revert the model immediately —
   the issue is usually instruction interpretation, not model capability

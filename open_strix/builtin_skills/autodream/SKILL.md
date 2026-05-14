---
name: autodream
description: Background memory consolidation that runs between sessions. Merges redundant blocks, prunes stale content, resolves contradictions, and surfaces patterns from session transcripts. Fires via turn_complete watcher with gate conditions.
---

# autodream — Background Memory Consolidation

You are a stateful agent whose memory blocks accumulate noise over time. New
information gets appended, old facts go stale, contradictions creep in, and
blocks grow until they dilute everything else in your prompt. Left unchecked,
your memory degrades your own cognition.

autodream is the maintenance cycle that prevents this. It runs in the background
after enough activity has accumulated, consolidating what you've learned into
denser, more accurate memory.

## When It Fires

The `autodream-gate` watcher runs on every `turn_complete`. It checks gate
conditions and only produces a finding when ALL conditions are met:

1. **Enough sessions** — at least 5 distinct session IDs in `logs/events.jsonl`
   since the last dream
2. **Enough time** — at least 24 hours since the last dream
3. **Not currently dreaming** — a lock file prevents concurrent dreams

The gate script is lightweight (reads a timestamp file + counts session IDs).
Most turns, it exits silently.

When the gate opens, the watcher emits a finding routed to the agent. The agent
then executes the dream using this skill's instructions.

## Lock & State

- **Lock file:** `state/autodream.lock` — contains PID and start timestamp.
  Stale if mtime > 1 hour (the dream process died).
- **Last dream timestamp:** `state/autodream-last.txt` — ISO 8601 UTC timestamp
  of last successful dream completion.
- **Dream log:** `logs/autodream.jsonl` — append-only record of every dream:
  what was merged, pruned, or flagged.

## The Four Phases

When triggered, execute these phases in order. Each phase should be a distinct
step in your reasoning.

### Phase 1: Orient

Read your current state to understand what you're working with.

1. `list_memory_blocks` — get all blocks with sizes
2. Read `state/autodream-last.txt` for when you last dreamed
3. Count sessions since last dream (from `logs/events.jsonl`)
4. Read `logs/journal.jsonl` — last 50 entries for recent context

**Output:** A mental model of what's changed since last dream.

### Phase 2: Gather Signal

Scan session transcripts for information that should be in memory but isn't,
or that contradicts what's currently stored.

1. List session directories in `logs/sessions/` newer than last dream
2. For each session, read the turn files (JSON with message history)
3. Look for:
   - **New facts** — people, channels, IDs, schedules, preferences
   - **Corrections** — "actually it's X not Y", "that changed", explicit updates
   - **Patterns** — recurring topics, repeated tool failures, behavioral shifts
   - **Stale references** — files/paths/IDs mentioned in blocks that no longer exist

**Important:** You're gathering signal, not acting yet. Note what you find.

### Phase 3: Consolidate

Apply changes to memory blocks and state files. Each change should be one of:

#### Merge
Two blocks cover overlapping territory. Combine into one denser block.
- Keep the more specific block ID
- Delete the redundant block
- Log: `{"action": "merge", "from": "block-a", "into": "block-b", "reason": "..."}`

#### Prune
A block contains information that is:
- **Derivable** — grep/git can answer it (file paths, code patterns, git history)
- **Stale** — references things that no longer exist
- **Verbose** — prose where facts would suffice

Rewrite the block to be denser. Don't delete useful information — compress it.
- Log: `{"action": "prune", "block": "block-id", "removed_chars": N, "reason": "..."}`

#### Update
A block contains outdated information contradicted by recent sessions.
- Update the specific lines
- Log: `{"action": "update", "block": "block-id", "field": "...", "reason": "..."}`

#### Surface
Session transcripts revealed something that should be in a block but isn't.
- Create a new block or add to an existing one
- Log: `{"action": "surface", "block": "block-id", "source": "session-id", "reason": "..."}`

#### Flag
Something needs human attention — a contradiction you can't resolve, a decision
that requires operator input, or a pattern that might indicate drift.
- Don't resolve it yourself. Note it in the dream log with `"action": "flag"`.
- If the agent has a channel for operator communication, mention it there.

### Phase 4: Record

1. Append all actions to `logs/autodream.jsonl` as one entry:
   ```json
   {
     "timestamp": "2026-03-31T12:00:00Z",
     "sessions_processed": 7,
     "actions": [...],
     "blocks_before": 12,
     "blocks_after": 11,
     "total_block_chars_before": 15000,
     "total_block_chars_after": 12000
   }
   ```
2. Write current UTC timestamp to `state/autodream-last.txt`
3. Remove `state/autodream.lock`
4. Journal the dream: what changed, what was flagged, net compression

## What NOT to Consolidate

- **Journal entries** — these are temporal narrative. Never edit or delete them.
- **events.jsonl** — immutable append-only log. Never touch it.
- **Session transcripts** — read-only during dreams.
- **Active task state** — today.md, inbox.md, commitments.md are operator-managed.
  Flag stale content but don't edit these without operator intent.

## What to Prioritize

The most valuable dream actions, in order:

1. **Resolve contradictions** — two blocks saying different things is worse than
   either being slightly stale
2. **Prune derivable content** — if `grep` or `git log` can answer it, it
   doesn't belong in a memory block
3. **Surface missing context** — things the agent keeps re-discovering every
   session because they're not in memory
4. **Compress verbose blocks** — prose → facts, paragraphs → bullet points
5. **Merge overlapping blocks** — fewer, denser blocks > many sparse ones

## Behavioral Guidelines

- **Conservative by default.** When unsure whether to prune something, don't.
  It's cheaper to carry slight redundancy than to lose something useful.
- **One dream at a time.** The lock file prevents concurrent dreams. If you find
  a stale lock (mtime > 1 hour), remove it and proceed.
- **Don't dream during active conversation.** If the operator is actively
  messaging, defer the dream to the next quiet period.
- **Log everything.** Every change gets a reason. The dream log is how operators
  audit what happened to their agent's memory.
- **Respect operator blocks.** Some blocks are operator-written (often the init
  block or identity blocks). Be extra conservative with these — compress
  your own additions, but don't rewrite the operator's words.

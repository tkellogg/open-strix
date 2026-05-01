# Journal as Breadcrumbs

You are required to call `journal` exactly once per turn. The journal entry is durable —
it lands in `logs/journal.jsonl`, queryable forever via the `introspection` skill. The
return value of `journal` is the contents of `~/checkpoint.md`, injected back as the
tool result. So journal is doing two jobs at once: **a letter to future-you** that
survives every context boundary, and **a hook for end-of-turn reminders** via
checkpoint.

This file is about the first job — *how to write entries that future-you will actually
find useful*. For the checkpoint hook see `try-harder.md`.

## What "future-you" means

Future-you reads the journal in three situations:

1. **The next turn after a context boundary** — you woke up from an async job, a
   schedule fire, a poller event, a sub-agent return. Your variables are gone; the
   journal is your only memory of why anything was happening.
2. **A debugging session** via `introspection` — something went wrong; you query
   journal history to see what you thought you were doing.
3. **A five-whys analysis** — you (or your operator) is reconstructing a chain of
   events to find a root cause.

Write for whichever of these is most likely to need this entry. Most often it's #1.

## The shape of a useful breadcrumb

A good entry answers four questions:

1. **What did I just do, named with handles?** Not "spawned a job" — "spawned
   `j_abc123` running `cargo build --release` for PR #45."
2. **Why?** The intent that future-you can't reconstruct from the action alone.
3. **What's the success path?** Concrete next step if things worked.
4. **What's the failure path?** Likely failure modes and where to look.

The handles matter most. Future-you can search `logs/events.jsonl` for `j_abc123` and
find every event tied to it. Without the handle, the entry is unanchored.

```
user_wanted: triage the rate-limit regression on PR #45
agent_did: spawned j_abc123 (acpx) to investigate. Output → /tmp/rl.md.
           Pre-checked: regression first appeared at commit a3f2; suspect is the
           new UserPagination middleware.
predictions: on success, post a summary comment to PR #45 and tag @oncall.
             on failure, likely will say "no clear cause" — fall back to bisect
             between a3f2 and HEAD.
```

When the wake-up arrives, this entry plus the wake-up prompt's exit code is enough to
pick up cleanly.

## When to leave a breadcrumb (beyond the mandated once-per-turn)

The mandate is one per turn. But the *content* of that one entry should be chosen for
when it most matters. High-value moments to fold into the entry:

* **Before crossing any context boundary** — async spawn, sub-agent delegation, schedule
  registration, end-of-turn pause. (See `context-boundaries.md`.)
* **Before destructive operations** — file deletion, repo reset, mass message send.
  Future-you should be able to reconstruct the pre-state if needed.
* **After a non-obvious decision** — especially paths-not-taken. "Considered X, rejected
  because Y" survives forever in journal; the alternative is rediscovering Y from scratch
  next time.
* **After a surprising finding** — something you didn't expect. Tag it for
  `prediction-review` (the `predictions` field is for this).
* **When you're confused** — write down what you don't understand. Future-you can pick
  up the investigation; or another agent can.

## What *not* to put in journal

* **Things that belong in memory blocks** — facts about the world that you'll need
  every turn. Blocks are higher-priority surface; journal is searchable history. Don't
  use journal as a memory block.
* **Things that belong in state files** — structured data, lists, plans. Journal is
  prose; files are databases.
* **Verbose retellings of what's already in events.jsonl** — events are ground truth;
  journal is your *interpretation*. Don't duplicate the log.
* **Apologies, hedges, or self-criticism without an action.** Future-you can't act on
  "I should have done better." If the lesson is real, encode it via `try-harder.md` —
  edit a memory block or checkpoint.md.
* **Shield-tic / filler predictions.** A prediction whose options all have similar
  probabilities and similar shapes (`Meta-1 silence-correct 0.95, Meta-2 logging-correct
  0.95, Meta-3 verification-correct 0.95`) is not a prediction — it's calibration noise
  that drowns the real signal in `prediction-review`. If every option resolves the same
  way regardless of what happens, drop it. The test: would the resolution of this
  prediction *change* what you'd do next? If no, it's filler.
* **Re-narrating what's already in the predictions field as prose.** If you wrote
  `predictions: a 0.45, b 0.30...` then said in `agent_did` "I think a is most likely",
  pick one surface. The structured field is what `prediction-review` reads. Prose
  hedges in `agent_did` aren't readable by it.

## Composing with other skills

* **`introspection`** — journal is one of its primary inputs. Write entries the
  introspection queries can use: include handles, types, intent. Future debugging-you
  will thank present-you.
* **`prediction-review`** — the `predictions` field of every entry is read by this
  skill. Make predictions *concrete enough to verify* ("the build will pass" not "I
  think it'll be fine").
* **`five-whys`** — when something's wrong, the chain of journal entries is often the
  best evidence for "what was the agent thinking?" Good journal entries make
  five-whys faster.
* **`memory`** — facts go in blocks; reflection goes in journal. Don't confuse them.

## A pre-spawn journal template

When in doubt, this template works for any spawn / delegate / async-block:

```
user_wanted: <the task this serves>
agent_did: spawned <handle> running <command> for <larger goal>.
           Pre-spawn state: <what was true when I left>.
           Routing: result lands at <file/channel/etc>.
predictions: on success → <next concrete step>.
             on failure → <likely modes + where to look>.
```

Five seconds of writing now saves five minutes of confused archaeology later.

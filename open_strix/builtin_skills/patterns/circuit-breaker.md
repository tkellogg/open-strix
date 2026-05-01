# Circuit Breaker: Recognizing When to Stop Yourself

The harness has machinery to catch some runaway behaviors — `send_message_loop_detected`
fires when you're sending the same message repeatedly; `send_message_loop_hard_stop`
terminates the turn if it gets really bad. But the harness only catches a narrow set of
loops. The agent-side discipline of *recognizing your own runaway* and stopping is
broader and worth its own pattern.

The system prompt's "Escalation Rule" is the seed: *after 2 same-class failures, STOP.
Ask: what am I assuming that could be wrong?* This file is the menu of what
"same-class" means and what to do when you've stopped.

## The trip conditions

Stop when any of these is true:

* **Same tool call with same args N times.** Often N=3. Re-running a `read_file` on the
  same file isn't going to change the result.
* **Same kind of error N times in a turn.** Three `tool_call_error`s of the same
  `error_type` is a signal, not noise.
* **Same correction from the same human N times.** Two corrections of the same kind in
  one conversation means you're misunderstanding something fundamental, not just the
  surface answer.
* **Trying "one more variation" of an approach that hasn't worked.** When you find
  yourself thinking "let me just try X with a small tweak," and you've already tried
  three small tweaks, the approach is wrong — not the tweak.
* **A loop that should converge isn't converging.** Iteration N of a refinement should
  produce smaller deltas than iteration N-1. If it doesn't, you're not converging —
  you're orbiting.
* **You've sent the same message phrased three different ways.** Rephrasing is not
  progress. The receiver isn't responding because they didn't see it, or they don't
  agree, or they're not there.

The harness catches the last one for `send_message`; you catch the others.

## What "stop" actually means

Stopping is not slowing down. It's switching from *action* to *inquiry*. The wrong
moves are: try harder, try a small variation, retry with backoff. The right moves are
all *information-gathering*:

1. **Read your own logs.** Use `introspection` on `logs/events.jsonl`. What does the
   actual sequence of events look like? Where did the assumption break?
2. **Ask: "What am I assuming that could be wrong?"** This phrase appears verbatim in
   the system prompt's Escalation Rule. Make it a real question, not a rhetorical one.
   List the assumptions. Test the most load-bearing one directly.
3. **Look for contradictions.** Two state files disagreeing, a memory block saying
   one thing and behavior reflecting another, instructions out of sync with reality.
   The `try-harder.md` pattern of identifying conflicts is exactly this.
4. **Read the source.** When a tool keeps failing, read its actual code, not your model
   of it. Models drift; code doesn't lie.
5. **Ask the human.** "I think I'm misunderstanding something fundamental — can you
   restate what you need?" The system prompt's Correction Protocol explicitly endorses
   this after two same-class corrections.
6. **File a five-whys.** When the loop is structural — same kind of failure recurring
   across sessions — the action item is a structural fix, not another retry.

## The anti-grit principle

open-strix is anti-grit on purpose. The right move when stuck is almost never "push
harder with the same approach." It's:

* Gather information you didn't have before
* Change the approach
* Encode a structural fix so future-you doesn't hit the same wall (`try-harder.md`)
* Stop and ask the human

"Trying harder" in the human sense — applying more focus and effort to the same task —
doesn't even work for humans. For agents, it usually just burns tokens and makes the
context worse for the next attempt.

## Recognizing the trip in real time

Tells that you're already in a loop you haven't broken:

* You catch yourself starting a sentence with "Let me try..." for the third time.
* Each new tool call is a small variation on the previous one rather than a different
  approach.
* You're hedging: "this might work" rather than "this will work because X."
* The user's last few messages are getting shorter and more clipped.
* You're explaining *why* the failure was reasonable instead of investigating *what
  caused it*.

If two of these are true, the breaker should already have tripped.

## What to do after the stop

The breaker is a *signal* — it tells you to switch from action to inquiry, then to a
structural fix. The fixes themselves live in `try-harder.md`. Cross-references rather
than restatement:

* **Communicate the stop to the human** if they're waiting on this task. Don't silently
  pivot — tell them you hit the breaker, what you saw, what you're doing instead.
* **Leave a journal entry** naming the loop and what you noticed (`journal-as-breadcrumbs.md`).
* **Pick the structural fix from `try-harder.md`.** That's the menu. Common picks after
  a breaker trip:
  * Item 4 — identify conflicting files (loops are often state disagreements)
  * Item 5 — read your own behavior via `introspection`
  * Item 6 — file a five-whys when the loop is recurring across sessions
  * Item 7 — update the failing skill if a skill's guidance produced the loop
* **If you don't yet know which `try-harder` move applies**, run `introspection` on
  `logs/events.jsonl` to see the actual loop shape. Trust events > journal > blocks.
* **Log a chainlink `interest` issue** if you can't act on the fix in this turn
  (`interest-backlog.md`). The drain poller surfaces it on its own cadence.

## Composing with other patterns

* **`introspection`** — your primary diagnostic tool after the breaker trips. Read
  `logs/events.jsonl` to see your actual loop. Trust events > journal > blocks.
* **`five-whys`** — when the loop is structural (recurring across sessions), this is
  the right next step. The breaker is *catching* the problem; five-whys is
  *understanding* it.
* **`try-harder.md`** — the inverse / counterpart. The breaker tells you to stop;
  try-harder tells you what *non-grit* moves are available instead.
* **`fallback-chains.md`** — sometimes the breaker should fall through to a backup
  rather than fully stopping. But only if the fallback covers a *different* failure
  mode than the loop you just broke.
* **`journal-as-breadcrumbs.md`** — the breaker trip is a high-value moment to journal.
  Future-you (and introspection-you) will want to know why this turn ended where it did.

## Anti-patterns

* **Hidden retries.** The breaker fired but you "just want to try one more thing." The
  one more thing is the loop continuing.
* **Blaming the tool.** "The tool isn't working" is rarely the bedrock cause and almost
  never the right action item. Investigate before attributing.
* **Stopping silently.** If a human is waiting on you, the stop has to be communicated.
  Otherwise it looks like you ghosted.
* **Restarting the loop in the next turn.** A fresh turn doesn't reset the
  fundamentals; if the structural cause is unaddressed, the loop will resume.

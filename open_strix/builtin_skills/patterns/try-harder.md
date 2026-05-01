# Try Harder

When a human is told to "try harder," it doesn't really work — even for humans. Effort
without structural change is just a tax on the next attempt. For an agent it works even
worse: more tokens, worse context, same structural cause still in place.

But "try harder" *does* mean something for an agent — it just translates to a set of
**non-human moves** that humans don't have. This file is the menu of those moves. When
you find yourself reaching for grit, reach for one of these instead.

## The non-human moves

You can edit your own state. A human can't. This makes the entire shape of
"try harder" different.

### 1. Edit a memory block

Memory blocks are rendered into your prompt every single turn. If you keep forgetting X
— or keep doing X wrong — the structural fix is to put the right behavior into a block.

```
"I keep sending messages in the wrong channel" →
  edit the block that holds channel-routing rules.

"I keep using sync shell when async would be better" →
  add a line to the operational block about async defaults.

"I keep apologizing instead of investigating" →
  edit the demeanor block.
```

Test: would seeing this line in your prompt every turn make the next attempt go right?
If yes, that's the edit.

**But check item 7 first.** If the rule you're about to put in a block came from a skill
that should have prevented this failure, the block is the wrong place — edit the skill
that's the proximate cause. Blocks bloat fast; skills are versioned and shareable. See
item 7 below.

### 2. Edit `~/checkpoint.md`

`checkpoint.md` is returned as the result of every `journal` call (and `journal` is
mandated once per turn). Whatever's in checkpoint.md is effectively injected near the
end of every turn. It's the single highest-leverage place to drop a reminder that
*always* gets seen.

The default content guides journaling itself. Add to it for any end-of-turn discipline
you want to enforce:

```
When you write a journal entry, think through:
- What did the user want, exactly?
- ...
- Did you remember to update state/active-projects.md if a project status changed?
- If you spawned an async job this turn, did you leave a pre-spawn breadcrumb?
- If you were corrected, did you update the relevant memory block?
```

This file isn't sacred — edit it. It exists to be edited. That's the whole point.

### 3. Log an interest item

When the lesson is "I should look into X later" rather than "I should change Y now,"
the right artifact is a chainlink `interest` issue (see
`patterns/interest-backlog.md`). The bar is intentionally low: any time something feels
odd, broken, surprising, or annoying, log a one-line item. A drain poller will surface
it for triage on its own cadence — no need to act *now* and no need to remember.

This sits between the memory-block-edit move (act now) and the journal entry (note now,
read later) — interest items are *commitments to act later, captured durably*. Future-you
discovers them via the drain, not by accidentally remembering.

### 4. Identify conflicting files

A surprising fraction of "I can't seem to do X right" turns out to be that two parts of
your own state disagree:

* Two memory blocks contradict each other
* A block says one thing; a state file says another
* checkpoint.md guides one behavior; an instruction in a skill guides the opposite
* Phone book says channel X has purpose Y, but actual messages there are about Z
* A schedule fires expecting state that's been moved or renamed

The "trying harder" move is to **stop and read your own state for contradictions**. This
is genuinely a non-human move — humans can't see their own contradicting beliefs nearly
as cleanly as you can read your own files. Use:

* `glob` to find relevant blocks and files
* `introspection` to query `logs/events.jsonl` for behavior that contradicts a stated
  intent
* The `prediction-review` skill to find drift between predicted and actual outcomes

When you find a conflict, fix the source. Don't add a new note about which to follow —
delete or rewrite the wrong one. If the same file keeps drifting back to a wrong state,
that's *oscillation* — two writers fighting over it. See `coordination.md` "Recognizing
oscillation" for the find-the-second-writer move.

### 5. Read your own behavior

`logs/events.jsonl` is ground truth. Your past actions are on disk. When something keeps
not working, the most informative move is often to *look at what you actually did*, not
to plan what to do next. Use the `introspection` skill — it exists exactly for this.

The introspection-then-fix flow:

1. Query events.jsonl: what *actually* happened on the failing turns?
2. Compare to journal entries: what did you *think* was happening?
3. The gap is where the structural fix lives.

### 6. File a five-whys

When the same kind of thing keeps failing, the cause is structural, not effort-shaped.
The `five-whys` skill is built to convert "this keeps happening" into a concrete file
edit. Its Step 5 even has a table of the kinds of S4-shaped fixes that often come out
the other side.

This is the meta-move that produces the other moves on this page.

### 7. Update the skill that's failing (often the right move BEFORE editing a block)

Skills are editable. If a skill's guidance led you wrong — or its absence let you go
wrong — **edit the skill before adding to a memory block.** This ordering matters:

* Skills are versioned, shareable, and survive across humans and agents. A block edit
  only helps the agent that already has that block.
* Block bloat is a real failure mode. Adding a block paragraph for every recurring
  miss produces ~600-line blocks that nobody reads end-to-end. The skill is the right
  surface for "rule that should fire when shape X comes up."
* If the skill is the *proximate cause* of the failure (it told you to do X, and X
  was wrong; or it was silent on a case it should have covered), the skill is what
  has to change. Putting the rule in a block on top of a wrong skill is paving over
  the cause.

Two caveats on the edit itself:

* Don't edit `.open_strix_builtin_skills/` directly — those are upstream. PR the repo,
  or make a custom skill with overriding guidance.
* Edit the skill *with the lesson encoded*, not as a rant. The next reader (you, in a
  future turn, with no context) needs the *rule*, not the *story*.

### 8. Add a poller

If the recurring failure is "I didn't notice X happened," the structural fix is a poller
that emits an event when X happens. (See `world-scanning.md`.) "Try harder to notice X"
doesn't scale; a poller does.

### 9. Add a fallback rung

If the recurring failure is "the channel/source/tool didn't work," the structural fix is
a fallback chain (see `fallback-chains.md`) — not retrying harder on the same channel.

### 10. Ask a clarifying question

The system prompt's Correction Protocol mandates this after two same-class corrections.
"Try harder to understand the human" is the wrong move; "ask the human" is the right
one. Especially when corrected with shorter and shorter messages — that's the human's
patience burning, not their commitment increasing.

## The general shape

Every "try harder" reduces to: **make a structural change so the next attempt doesn't
need more effort.** The artifact of trying harder is a diff someone else can verify, not
a resolution someone else has to trust.

| Wrong "try harder" | Right "try harder" |
|---|---|
| "I'll be more careful next time" | Edit a memory block with the rule |
| "I'll remember to check X" | Add the check to checkpoint.md |
| "Let me try the same approach with one more tweak" | Run introspection on the loop, fix the structure |
| "I shouldn't have missed that" | Add a poller for the missed signal |
| "I'll explain it better this time" | Ask a clarifying question first |
| "Let me retry the failing tool" | Read its source / fall through to a backup |

The test from `five-whys`: *could someone else verify this was done?* "I tried harder"
fails this test. "I edited block X" passes.

## When you really do need persistence (not grit)

Sometimes the right move *is* to keep going — when each iteration is producing genuine
new information, when you're in the middle of a converging loop (not orbiting), when
the path is just long. That's not trying harder; it's *finishing*. The distinction:

* Finishing: each step shrinks the unknown
* Trying harder: each step is a variation on the last and the unknown isn't shrinking

If you can't tell which mode you're in, you're probably trying harder. Trip the
`circuit-breaker.md` and gather information.

## Composing with other patterns

* **`circuit-breaker.md`** — the circuit breaker recognizes when to stop the
  same-thing-again loop; this file is the menu of *what to do instead*.
* **`introspection`** — the diagnostic skill that finds *which* structural change is
  needed. Always pair "try harder" with introspection first.
* **`five-whys`** — the analytical skill that decomposes recurring failure into a
  bedrock cause + concrete artifact-shaped action.
* **`memory`** — when the structural fix is a memory block edit, the memory skill
  governs how to do it well.
* **`journal-as-breadcrumbs.md`** — encode the lesson in a journal entry so introspection
  can find it later.
* **`context-boundaries.md`** — many "try harder" failures are context-loss failures in
  disguise. The fix is durable storage, not effort.

## The anti-pattern this whole file exists to prevent

> "I'll do better next time."

Next time is a different agent with no memory of this commitment. The lesson has to be
on disk, in a block, in checkpoint.md, in a skill — somewhere that the next agent will
*see*. Otherwise the resolution dies the moment your turn ends.

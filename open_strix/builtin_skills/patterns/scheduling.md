# Scheduling: Which Temporal Primitive When

open-strix has at least seven overlapping ways to make something happen later. They're
not interchangeable — each makes different tradeoffs around context, frequency, cost,
and what happens at startup. Picking the wrong one is a common cause of wasted tokens
and surprising behavior.

This is the decision guide.

## The seven primitives

| Primitive | Where it lives | Wakes whom |
|---|---|---|
| **Pollers** (`pollers.json`) | Inside any skill | Fresh turn, any agent |
| **`add_schedule` / `scheduler.yaml`** | Agent-managed cron | Fresh turn, this agent |
| **`/schedule`** | Remote agent routine | Fresh remote agent run |
| **`/loop`** | This conversation, fixed interval | This conversation, again |
| **`ScheduleWakeup`** | This conversation, dynamic interval | This conversation, again |
| **`shell(async_mode=True)` + blocking-wait** | Agent process | This conversation, on event |
| **OS cron / launchd / systemd** | Outside the agent loop | A fresh process |

## The decision tree

**Does the agent need to react to *external state changes*?**
→ Poller. The poller script polls; emits events only when something happened. Fresh
turn per event. (`world-scanning.md`)

**Does the agent need to do *the same task* on a recurring cadence?**
→ `add_schedule` if it should run as *this* agent (with this agent's memory blocks /
state). → `/schedule` if it should run as a *fresh* remote agent (e.g. cleanup tasks,
weekly reports).

**Does *this conversation* need to repeat the same prompt on a fixed interval?**
→ `/loop <interval> <prompt>`. The current conversation re-runs.

**Does *this conversation* need to repeat, but with the model picking the next interval
each time?**
→ `/loop` without an interval (dynamic mode), then call `ScheduleWakeup` from inside.
The model paces itself.

**Does *this conversation* need to wait for *one specific event*, then resume?**
→ `shell(async_mode=True)` with a blocking command (dialog, file-watch, webhook
listener, `until` loop, `wait $PID`). The completion event wakes the same conversation
with full context. (`async-tasks.md`)

**Does the work need to run *outside the agent loop entirely*?**
→ OS-native cron / launchd / systemd. The OS launches a fresh process; if it should
talk back to the agent, have it write to a poller-watched file or send a message.

## Comparison axes

When the decision tree has ties, these axes break them.

**Frequency**

* Pollers: as frequent as you want (default 5 min, can go to seconds)
* `add_schedule`: cron expression, no theoretical limit but every fire costs tokens
* `/schedule`: typically daily/weekly; spinning up a remote agent is expensive
* `/loop`: any interval; dynamic mode self-paces
* `shell async`: one-shot; the wait can be any duration
* OS cron: any interval; cheapest for very frequent ticks

**Context preservation**

* This-conversation continues: `/loop`, `ScheduleWakeup`, `shell async`
* Fresh turn, same agent (memory blocks intact): pollers, `add_schedule`
* Fresh agent entirely: `/schedule`, OS cron
* This is the most important axis — see `context-boundaries.md`

**Cost**

* OS cron: free (no agent involved unless explicitly invoked)
* Pollers: cheap (pollers are dumb scripts; agent only wakes on emit)
* `shell async`: cheap during the wait (zero tokens), turn cost on resume
* `add_schedule` / `/loop`: full turn cost per fire
* `/schedule` (remote agent): full session spin-up per fire

**Survives reboot**

* Always: pollers, `add_schedule`, `/schedule`, OS cron
* No: `/loop`, `ScheduleWakeup`, `shell async` (these depend on the agent process)

**Failure mode**

* Poller / OS cron: silent failure unless you check logs
* `add_schedule`: harness logs `scheduler_invalid_*` events
* `/schedule`: routine UI shows status
* `shell async`: completion event always fires, with exit code

## Common mis-pickings

* **Using a poller when the question is "wake me when X" once.** A poller polls
  forever. If you only need one wake-up, use `shell(async_mode=True)` with an `until`
  loop. Saves cron overhead and naturally cleans up.

* **Using `/loop` when you want to react to events.** A loop fires on a clock; events
  don't happen on a clock. Use a poller.

* **Using `add_schedule` when the action should not require this agent's memory blocks.**
  If a daily cleanup doesn't need to know who *you* are, prefer OS cron — cheaper, more
  reliable, doesn't tie up the agent process.

* **Using `shell async` for recurring conditions.** The async wake-up is one-shot. For
  recurring, use a poller.

* **Stacking `/schedule` with `add_schedule` for "every day at 9am."** Pick one. They
  don't compose; they conflict.

## Composing them

The interesting power often comes from combinations:

* **Poller emits → schedule fires → agent acts.** A poller detects the state change
  cheaply; if action requires fresh-agent context, the schedule fires from there.
  Usually overkill; usually the poller can directly emit a wake-up.

* **One poller fire → N sequential agent turns (chain / fan-out).** A single
  poller invocation can emit N lines on stdout; the scheduler queues each as its
  own `AgentEvent`, so the agent runs N turns back-to-back, one per line. This is
  the right shape any time you need to *drain a queue* — a backlog of owed
  five-whys, an interest-backlog of captures, a batch of pending reviews.
  One cron tick, one process spawn, N fresh turns. Sequential (not parallel),
  collision-safe if each prompt says "pick the top open item" instead of
  hard-coding ids. Keep N small (3-7) and gate on idle + queue-depth + refire
  interval so the chain stays rare and useful. See
  `pollers/design-patterns.md` "Fan-Out" for the implementation pattern.

* **`shell async` waits → on completion, register an `add_schedule`.** Waited for the
  human to do X; now schedule the follow-up reminder.

* **`/loop` calls a poller.** The loop's prompt is "check X poller's recent emissions
  and act." Useful when the loop needs to *act on accumulated state* rather than each
  emission individually.

* **OS cron runs a poller offline.** For very high-frequency checks where you don't
  even want the harness scheduler involved.

## Composing with introspection

When something doesn't fire when you expected:

* `add_schedule` issues → `introspection/debugging-jobs.md`
* Poller issues → `pollers/SKILL.md` "Debugging" section
* `scheduler.yaml` is source of truth for `add_schedule`; check it directly
* `logs/events.jsonl` has `scheduler_*` events for invalid configs

Cron expressions in open-strix are evaluated in **UTC**. This bites everyone exactly
once.

When you find yourself with three or more pollers / schedules running, read
`coordination.md` — schedule jitter, idempotency keys, and reactive-vs-scheduled
debounce are the S2 fixes for the collisions that *will* start appearing.

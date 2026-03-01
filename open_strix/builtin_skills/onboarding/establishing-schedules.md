# Establishing Schedules

Scheduled jobs are what make an agent autonomous rather than reactive. Without them, the agent
only works when someone talks to it.

## Schedule Design

### Start Minimal

A new agent needs exactly one scheduled job to start: a periodic check-in that forces the agent
to look around, notice things, and decide if any action is needed.

**Starter job example:**
```yaml
jobs:
  - name: daily-check-in
    prompt: >
      Review your state files, check recent Discord activity, and update anything stale.
      If you notice something worth sharing, send a brief message. If not, just update
      your files and journal what you observed.
    time_of_day: "14:00"  # All times in UTC
```

Add more jobs only when the agent's actual behavior reveals a need. If you find yourself
manually reminding the agent to do something repeatedly, that's a job.

### Job Anatomy

Every scheduled job has:
- **name** — kebab-case, descriptive, unique. `morning-scan` not `job1`
- **prompt** — what to do AND how to communicate the result. Be specific.
- **timing** — `cron` (for recurring patterns) or `time_of_day` (for daily jobs)
- **channel_id** (optional) — target channel for output

**Prompt quality matters more than schedule frequency.** A well-prompted weekly job produces
more value than a poorly-prompted hourly one.

### Timing Guidelines

**All cron expressions are UTC.** Convert from the human's local timezone.

Common patterns:
```yaml
# Every 2 hours during waking hours (8am-10pm ET = 12:00-02:00 UTC)
cron: "0 12,14,16,18,20,22,0,2 * * *"

# Twice daily (9am/9pm UTC)
time_of_day: "09:00"  # first run
# (add a second job for 21:00)

# Every 4 hours
cron: "0 */4 * * *"

# Weekdays only at 2pm UTC
cron: "0 14 * * 1-5"
```

**Timezone trap:** The most common scheduling bug. Agent says "every morning at 8am" but
writes `cron: "0 8 * * *"` — that's 8am UTC, which is 3am ET. Always convert explicitly.

### What Makes a Good Job Prompt

**Good:**
```
Scan the buddy channel for the last 4 hours. If any human posted something you have
a genuine response to, reply. If Tim asked a question nobody answered, answer it.
If nothing warrants a response, update your state files with any observations and
journal what you saw. Do not send a message just because the job fired.
```

**Bad:**
```
Check Discord and respond to messages.
```

The difference: the good prompt tells the agent what to look for, how to decide whether to
act, and what to do in the no-action case. The bad prompt guarantees the agent will either
over-communicate (responding to everything) or under-communicate (not knowing what matters).

### Building Up the Schedule

**Week 1:** One daily check-in. That's it. Let the agent figure out its rhythm.

**Week 2:** If patterns emerge (agent always checks Bluesky, agent always reviews predictions),
formalize those as separate jobs with specific prompts.

**Week 3+:** Schedule should stabilize at 3-7 jobs. More than 10 is a smell — either jobs
are too granular or the agent is over-scheduled.

## Common Pitfalls

**Too many jobs too early** — Every job is a commitment. Start with 1-2 and add based on need.

**Overlapping schedules** — Two jobs firing within 5 minutes of each other can cause context
confusion. Space jobs at least 15 minutes apart.

**No quiet hours** — Unless the agent has genuine overnight work, don't schedule jobs during
the human's sleep hours. Jobs that fire and produce nothing useful waste compute and create
log noise.

**Prompt drift** — Job prompts written on day 1 may not match the agent's actual needs by
week 3. Review job prompts when behavior seems off — the prompt might be outdated.

**Missing the "do nothing" case** — Every job prompt should include what to do when there's
nothing to do. Otherwise the agent will manufacture output to justify the job firing.

## Coordination With Other Agents

If multiple agents share a channel:
- Stagger schedules to avoid simultaneous posts
- Include awareness of other agents' output in job prompts ("check if Verge already covered this")
- Use different job names across agents to avoid confusion in logs

If agents share a human:
- Respect the human's attention budget — three agents all posting at 9am is overwhelming
- Coordinate timing so the human gets a coherent flow, not a burst

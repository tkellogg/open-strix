# Establishing Schedules

Scheduled jobs are what make you autonomous rather than reactive. Without them, you only
work when someone talks to you.

But don't over-schedule. Start with one job and add more only when real needs emerge.

## Your First Job

After your first conversation, you probably know enough to set a check-in time. If the
human mentioned their morning routine, schedule your check-in for then. If they didn't
mention timing, pick something reasonable and offer to adjust.

```python
add_schedule(
    name="daily-check-in",
    prompt="Read state/daily-check-in.md and follow the instructions there.",
    time_of_day="14:00"  # All times in UTC — convert from human's timezone!
)
```

Two scheduling formats are available — use exactly one per job:
- **`time_of_day`** — simple `HH:MM` in UTC. Good for single daily jobs.
- **`cron`** — standard crontab (`min hour day month dow`). Good for complex patterns.

That's it for week one. One job. Let it run for a few days and see what happens.

## The File-Referenced Pattern

**Job prompts should be short. The real guidance lives in a state file.**

Why: you'll learn and adapt over time, but you rarely update job definitions. If the
prompt contains all the logic, it becomes stale. If it points to a file, you can update
the file as you learn — and the same job automatically gets smarter.

**Good:**
```python
add_schedule(
    name="morning-scan",
    prompt="Read state/morning-scan.md and follow the instructions there.",
    time_of_day="13:00"
)
```

Then `state/morning-scan.md` contains the actual guidance — what to scan, how to decide
whether to message, what to update. When you learn something (a new channel to check, a
pattern to watch for), update the file, not the job.

**Bad:**
```python
add_schedule(
    name="morning-scan",
    prompt="""Scan the buddy channel for the last 4 hours. Check Bluesky for new posts.
    Review state files. If any human posted something you have a genuine response to,
    reply. If nobody needs a response, update your state files with any observations.
    Also check if predictions are stale. Also scan arXiv if it's Monday or Thursday.
    Don't forget to update the relationships block if anyone new appeared...""",
    time_of_day="13:00"
)
```

The bad version will be wrong within a week. The good version stays correct because the
file evolves.

## The Perch Tick Pattern

The most powerful scheduling pattern is a **regular cadence with mutable priorities**.
Instead of specialized jobs for each task, you have a recurring tick — a heartbeat — that
reads a priority file to decide what to do.

The idea: schedule a tick every N hours. Each tick reads a state file that lists the
current priorities for that time slot. As your priorities change, you update the file,
not the schedule.

**Example: 2-hour cadence across 24 hours**

```python
add_schedule(
    name="perch-tick",
    prompt="Read state/tick-cadence.md. Find your current time slot. Execute the priorities listed for that slot.",
    cron="0 */2 * * *"  # Every 2 hours
)
```

Then `state/tick-cadence.md` maps time slots to priorities:

```markdown
# Tick Cadence

## 6am UTC — Morning Digest
- Scan overnight activity
- Generate morning summary for human
- Check for stale state files

## 8am UTC — Content Check
- Scan Bluesky/social channels
- Check for engagement needing response
- Update tracking numbers

## 12pm UTC — Midday Maintenance
- Sync memory blocks with recent activity
- Review state file freshness
- Check commitments and deadlines

## 4pm UTC — Afternoon Scan
- Bluesky numbers refresh
- Goal progress review
- Queue night work priorities

## 10pm-4am UTC — Night Work
- Execute queued project work
- Deep research, writing, experiments
- Infrastructure audit (once per cycle)
```

**Why this works so well:** You schedule the cadence once and never touch it again. All
the intelligence lives in the file, which you update as you learn what each time slot
should actually contain. Over time, the cadence file becomes a map of your entire
operational rhythm.

This pattern is how you go from "agent that responds when poked" to "agent that
autonomously manages its own workload across a 24-hour cycle."

## Essential Maintenance Jobs

Beyond your first check-in, certain jobs keep you healthy as an agent. Add these as
you stabilize in week 2-3:

### Memory Maintenance

Your memory blocks drift. State files go stale. A periodic maintenance job catches
this before it compounds.

```python
add_schedule(
    name="memory-maintenance",
    prompt="Read state/memory-maintenance.md and follow the instructions there.",
    cron="0 12 * * *"  # Once daily, pick a quiet time
)
```

The state file should cover:
- Check memory blocks against recent reality (are they still accurate?)
- Review state files for stale dates, completed items, outdated info
- Cross-reference blocks and files (do they agree?)
- See the **memory skill** for detailed maintenance guidance

### Prediction Evaluation

The **prediction-review skill** provides the mechanics. Your job is to make sure it
actually runs regularly — and more importantly, that the results feed back into your
blocks and files.

```python
add_schedule(
    name="prediction-review",
    prompt="Read state/prediction-review.md and follow the instructions there.",
    cron="0 9,21 * * *"  # Twice daily
)
```

The state file should cover:
- Run the prediction-review skill on entries 2-3 days old
- For each miss: what did you assume wrong? What should change?
- **Update the relevant memory block or state file** with what you learned
- The point isn't labeling true/false — it's closing the loop

Prediction evaluation without behavior change is just bookkeeping. When a prediction
is wrong, trace it to a specific assumption in your blocks or files, and fix it. That's
how predictions become a long-term learning mechanism instead of a checkbox.

## Adding Jobs Based on Need

Add a new job when you find yourself repeatedly doing something manually. If you check
Bluesky every time the human mentions it — that's a job. If you review the same state
files every morning — that's a job.

**Week 2:** If patterns emerge (you always scan something, always review something),
formalize those as separate jobs with specific prompts.

**Week 3+:** Schedule should stabilize at 3-7 jobs. More than 10 is a smell. If you
have many specialized jobs, consider consolidating into the perch tick pattern instead.

## Writing Good Prompts

Even for jobs that point to files, the prompt still matters. Keep it short and include
the "do nothing" case:

**Good prompt:**
```
Read state/channel-scan.md and follow the instructions there. If nothing needs action,
update state files and journal what you observed. Do not send a message just because
this job fired.
```

**Bad prompt:**
```
Check Discord and respond to messages.
```

**Every job prompt needs a "do nothing" case.** Otherwise you'll manufacture output
to justify the job firing.

## Critical: All Times Are UTC

The most common scheduling bug: agent writes `cron: "0 8 * * *"` thinking "8am" but
that's 8am UTC — 3am Eastern, 12am Pacific.

Always convert explicitly from the human's timezone:
```
# 9am ET = 13:00 or 14:00 UTC (depending on DST)
# 9am PT = 16:00 or 17:00 UTC
```

Common cron patterns:
```
"0 */4 * * *"           # Every 4 hours
"0 12,16,20,0 * * *"   # 4x daily at specific UTC hours
"0 14 * * 1-5"         # Weekdays only at 2pm UTC
```

## Evaluating Time Passage

Some things in onboarding say "stable for 2 weeks" or "revisit after a month." How
do you actually know how long it's been?

**Use your own logs.** Your `logs/journal.jsonl` has timestamps on every entry. Your
git history has dates on every commit. When you need to know "has this block been stable
for 2 weeks?", check when it was last modified:

```bash
# When was this block last changed?
git log --oneline -5 -- state/memory/persona.yaml

# When did I last mention this topic in journal?
jq -r 'select(.topics | test("persona")) | .timestamp' logs/journal.jsonl | tail -5
```

The **introspection skill** covers querying your own history in detail. The short
version: your event log and git history ARE your sense of time. Use them.

## Pitfalls

**Too many jobs too early** — Every job is a commitment. Start with 1-2.

**Overlapping schedules** — Space jobs at least 15 minutes apart to avoid context confusion.

**No quiet hours** — Don't schedule jobs during the human's sleep hours unless there's
genuine overnight work to do.

**Prompt drift** — Job prompts written on day 1 may not match reality by week 3. This is
why the file-referenced pattern matters — update the file, not the job.

**All logic in the prompt** — If your prompt is more than 2-3 sentences, move the logic
to a state file and point the prompt at the file instead.

## Multi-Agent Coordination

If you share a channel with other agents:
- Stagger schedules to avoid simultaneous posts
- Include awareness of others in your state files ("check if someone already covered this")
- Respect the human's attention budget — three agents posting at 9am is overwhelming

# Establishing Schedules

Scheduled jobs are what make you autonomous rather than reactive. Without them, you only
work when someone talks to you.

But don't over-schedule. Start with one job and add more only when real needs emerge.

## Your First Job

After your first conversation, you probably know enough to set a check-in time. If the
human mentioned their morning routine, schedule your check-in for then. If they didn't
mention timing, pick something reasonable and offer to adjust.

```yaml
jobs:
  - name: daily-check-in
    prompt: >
      Review your state files, check recent Discord activity, and update anything stale.
      If you notice something worth sharing, send a brief message. If not, just update
      your files and journal what you observed.
    time_of_day: "14:00"  # All times in UTC — convert from human's timezone!
```

That's it for week one. One job. Let it run for a few days and see what happens.

## Adding Jobs Based on Need

Add a new job when you find yourself repeatedly doing something manually. If you check
Bluesky every time the human mentions it — that's a job. If you review the same state
files every morning — that's a job.

**Week 2:** If patterns emerge (you always scan something, always review something),
formalize those as separate jobs with specific prompts.

**Week 3+:** Schedule should stabilize at 3-7 jobs. More than 10 is a smell.

## Writing Good Prompts

**Prompt quality matters more than schedule frequency.** A well-prompted weekly job
beats a poorly-prompted hourly one.

**Good prompt:**
```
Scan the buddy channel for the last 4 hours. If any human posted something you have
a genuine response to, reply. If nobody needs a response, update your state files
with any observations and journal what you saw. Do not send a message just because
this job fired.
```

**Bad prompt:**
```
Check Discord and respond to messages.
```

The good prompt says what to look for, how to decide whether to act, and what to do
when there's nothing to do. The bad prompt guarantees over-communication or silence.

**Every job prompt needs a "do nothing" case.** Otherwise you'll manufacture output
to justify the job firing.

## Critical: All Times Are UTC

The most common scheduling bug: agent writes `cron: "0 8 * * *"` thinking "8am" but
that's 8am UTC — 3am Eastern, 12am Pacific.

Always convert explicitly from the human's timezone:
```yaml
# 9am ET = 13:00 or 14:00 UTC (depending on DST)
# 9am PT = 16:00 or 17:00 UTC
```

Common cron patterns:
```yaml
cron: "0 */4 * * *"           # Every 4 hours
cron: "0 12,16,20,0 * * *"   # 4x daily at specific UTC hours
cron: "0 14 * * 1-5"         # Weekdays only at 2pm UTC
```

## Pitfalls

**Too many jobs too early** — Every job is a commitment. Start with 1-2.

**Overlapping schedules** — Space jobs at least 15 minutes apart to avoid context confusion.

**No quiet hours** — Don't schedule jobs during the human's sleep hours unless there's
genuine overnight work to do.

**Prompt drift** — Job prompts written on day 1 may not match reality by week 3. Review
them when behavior seems off.

## Multi-Agent Coordination

If you share a channel with other agents:
- Stagger schedules to avoid simultaneous posts
- Include awareness of others in prompts ("check if someone already covered this")
- Respect the human's attention budget — three agents posting at 9am is overwhelming

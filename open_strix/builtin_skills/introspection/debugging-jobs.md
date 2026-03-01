# Debugging Scheduled Jobs

## Quick Health Check

```bash
# List all current jobs
cat scheduler.yaml

# Find scheduler events in logs
jq -s 'map(select(.type | startswith("scheduler"))) | sort_by(.timestamp) | .[-20:]' logs/events.jsonl
```

## Common Problems

### Job Not Firing

**Symptoms:** No evidence in events.jsonl that the job ran. No journal entry.

**Diagnosis steps:**

1. **Is the job in scheduler.yaml?**
   ```bash
   cat scheduler.yaml
   ```
   If missing, add it with `add_schedule`.

2. **Was the job loaded successfully?**
   ```bash
   jq -s 'map(select(.type == "scheduler_reloaded")) | sort_by(.timestamp) | last' logs/events.jsonl
   ```
   Check the `jobs` count. If it's lower than expected, a job failed validation.

3. **Did validation fail?**
   ```bash
   jq -s 'map(select(.type | test("scheduler_invalid")))' logs/events.jsonl
   ```
   Look for `scheduler_invalid_job`, `scheduler_invalid_cron`, or `scheduler_invalid_time`.

4. **Is the cron expression correct?**
   Common mistakes:
   - Using 6-field cron (with seconds) — open-strix uses **5-field** standard cron
   - Wrong field order: `minute hour day-of-month month day-of-week`
   - Using `*/2` in the hour field when you mean every 2 hours from midnight (that's correct), vs every 2 hours from now (cron doesn't do relative offsets)

5. **Is the bot actually running?**
   If the process restarted, jobs are reloaded automatically. But if the bot was
   down during a scheduled time, that firing is skipped (no catch-up).

### Job Fires at Wrong Time

**Symptoms:** Job runs, but not when expected.

**Diagnosis steps:**

1. **Timezone mismatch.** All cron and time_of_day values are in **UTC**.
   - US Eastern = UTC-5 (EST) or UTC-4 (EDT)
   - US Pacific = UTC-8 (PST) or UTC-7 (PDT)
   - To run at 9am ET: use `0 14 * * *` (EST) or `0 13 * * *` (EDT)

2. **Verify actual fire times:**
   ```bash
   jq -s 'map(select(.type == "tool_call" and .tool == "journal")) | sort_by(.timestamp) | map({ts: .timestamp, session: .session_id})' logs/events.jsonl | tail -20
   ```
   Cross-reference journal timestamps with expected schedule.

3. **Cron field confusion:**
   ```
   ┌───────────── minute (0-59)
   │ ┌───────────── hour (0-23)
   │ │ ┌───────────── day of month (1-31)
   │ │ │ ┌───────────── month (1-12)
   │ │ │ │ ┌───────────── day of week (0-6, Sun=0)
   │ │ │ │ │
   * * * * *
   ```

### Job Fires But Does Nothing Useful

**Symptoms:** Event log shows the scheduler fired, but no meaningful output.

**Diagnosis steps:**

1. **Check what the prompt says:**
   ```bash
   cat scheduler.yaml
   ```
   Is the prompt clear and specific? Vague prompts produce vague results.

2. **Find the session that ran:**
   ```bash
   # Find scheduler fire events
   jq -s 'map(select(.type == "tool_call" and .scheduler_name != null)) | sort_by(.timestamp) | .[-5:]' logs/events.jsonl
   ```

3. **Trace the full session:**
   ```bash
   # Replace SESSION_ID with the actual session ID from above
   jq -s 'map(select(.session_id == "SESSION_ID")) | sort_by(.timestamp)' logs/events.jsonl
   ```
   Look for: errors, empty tool results, missing context.

4. **Did it send a message?**
   ```bash
   jq -s 'map(select(.session_id == "SESSION_ID" and .tool == "send_message"))' logs/events.jsonl
   ```
   If no send_message events, the agent completed the turn without communicating.

### Duplicate Jobs

**Symptoms:** Same job appears to run twice, or scheduler.yaml has near-duplicates.

**Diagnosis:**
```bash
# Check for duplicate names
cat scheduler.yaml | grep 'name:'
```

`add_schedule` replaces jobs by name. If two jobs have different names but similar
prompts, both will fire independently. Consolidate by removing one with `remove_schedule`.

### Job Removed But Still Firing

After `remove_schedule`, the job is removed from scheduler.yaml and the in-memory
scheduler. If it still appears to fire, check:

1. Was the bot restarted between remove and the next fire time?
2. Is there a second job with a different name but same prompt?

## Example: Setting Up a Reliable Recurring Job

```
add_schedule(
    name="morning-scan",
    prompt="Scan Bluesky for new mentions and replies. Update state files with current engagement numbers. Only send a message if something notable changed.",
    cron="0 12 * * *",        # noon UTC = 7am ET (EST) / 8am ET (EDT)
    channel_id="123456789"    # optional: target channel for any messages
)
```

Tips:
- Use descriptive names (kebab-case)
- Make prompts specific about what to do AND what to communicate
- Include the channel_id if the job should send messages to a specific place
- Test by temporarily setting cron to fire in 2 minutes, verify, then set final schedule

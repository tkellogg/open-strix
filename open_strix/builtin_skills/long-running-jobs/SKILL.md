---
name: long-running-jobs
description: Run shell commands in the background with file-backed output capture and a completion event that wakes the agent when the job exits. Use when a command might take more than ~30 seconds (builds, tests, deployments, agent jobs like acpx/codex exec) and you want to keep working while it runs.
---

# Long-Running Jobs

Some commands take minutes or hours. Rather than blocking on them, spawn them with `async_mode=True`. The harness owns the process — captures stdout/stderr to files, surfaces the job in the web UI, and **fires a completion event back into the agent's queue when the subprocess exits.**

The completion event is a fresh turn: journal, memory blocks, and recent messages get rebuilt from disk. There is no in-context state to preserve. Whatever you need to remember when the job finishes has to live somewhere durable (journal entry, state file, the command itself) **before** you spawn.

## When to Use This

**USE when:**
- Build commands (`cargo build`, `npm run build`, `make`)
- Test suites that take more than ~30 seconds
- Data processing, model training, large downloads
- Deployments or migrations
- Sub-agent invocations (`acpx`, `codex exec`, etc.)
- Any command where you want to keep working while it runs

**DON'T USE when:**
- Quick commands (< 30s) — just call shell normally and wait
- Commands you need the result of immediately (chain into next reasoning step)
- Interactive commands that need stdin

## The Canonical Pattern

```
shell(command="cargo build --release", async_mode=True)
```

That's it. The harness:

1. Spawns the command in its own process group, file-backs stdout/stderr
2. Returns immediately with `Spawned async job j_abc123 (pid 12345)`
3. Surfaces the job in the web UI with a live status indicator
4. When the subprocess exits, fires a `shell_job_complete` event that wakes the agent with a prompt containing `job_id`, `command`, `exit_code`, `elapsed`, and the tail (~4KB) of each stream

You keep working in the current turn. The completion event arrives as a separate wake-up.

## Inspecting Running Jobs

While a job is running you have three options — and one of them isn't a tool call:

- **Web UI** — the user can see running jobs at a glance with elapsed time and status. If the user's watching, they already know.
- `shell_jobs_list()` — every job the registry knows about (running + recently finished), with `job_id`, `pid`, `status`, `elapsed_seconds`, and `seconds_since_last_signal`.
- `shell_job_output(job_id, tail_lines=N, stream="stdout"|"stderr"|"both")` — tail current output without waiting for completion. Useful for sanity-checking that a build is making progress vs hung.

To stop a running job, call shell again with `kill <pid>` (or `kill -9 <pid>` if it's stuck). Pick the signal level yourself.

## What the Wake-Up Prompt Looks Like

When the job exits, you wake up to something like:

```
Shell job j_abc123def4 complete (status=exited_ok, exit_code=0, elapsed=47.3s).
Command: cargo build --release

--- stdout tail ---
   Compiling foo v0.1.0 (/home/agent/foo)
    Finished release [optimized] target(s) in 47.21s

--- stderr tail ---
(empty)
```

That's the entire context the wake-up gives you about the job itself. Your journal, memory blocks, and recent channel history come back automatically — but they only carry whatever you wrote to them before spawning.

## The Pre-Spawn Discipline

Because the completion turn is fresh, the cost of a context-thin spawn is paid by your future self. The fix is to leave breadcrumbs **before** you call shell:

- **Journal entry naming the job and the plan.** "Spawned `j_abc123` building release for PR #45. If green, run integration tests next; if red, suspect borrow checker on UserPagination." A good journal entry is the single highest-leverage thing you can do here.
- **State files for any multi-step plan.** `state/research/jobs-in-flight.md` or similar. The completion turn can read it.
- **Encode the next step in the command itself when feasible.** `make build && make test && touch /tmp/build-pipeline.done` collapses two steps into one job, which means one wake-up instead of two.

The old version of this skill recommended composing a long callback message at spawn time. That's no longer the model — the harness writes the wake-up prompt for you, and it doesn't carry your prose. Put your prose in the journal.

### What to Journal Before Spawning

A useful pre-spawn journal entry answers:

1. **What did you spawn?** — `job_id`, command, the bigger task it serves
2. **What's the plan on success?** — concrete next step
3. **What's the plan on failure?** — likely failure modes and where to look

Example:

```
[journal entry, before shell(... async_mode=True)]
Spawned j_abc123 (cargo build --release) as part of PR #45 prep.
On exit_code=0: run integration tests, then post merge-ready comment on PR.
On non-zero: most likely the new UserPagination struct lifetime issue.
Grep stderr for "borrow" or "lifetime" first.
```

Then on the completion turn you read recent journal entries and the wake-up prompt's exit code together — and you have everything you need.

## Reading Output Beyond the Tail

The completion prompt only includes the last ~4KB of each stream. For long output:

- `shell_job_output(job_id, tail_lines=500)` — pull more from the registry
- The full output files live under the harness `jobs/` directory (`<job_id>.out`, `<job_id>.err`) — read them directly if needed

## Multiple Jobs at Once

Each `async_mode=True` spawn registers independently. You can have several running concurrently. Each will fire its own completion event, in subprocess-exit order — so the order of wake-ups depends on which job finishes first, not which you spawned first. Plan accordingly: don't rely on Job B's wake-up implying Job A finished.

## Common Recipes

### Build, then react

```
# Pre-spawn journal entry
journal: "Spawned j_X cargo build --release for PR #45. Green → integration tests. Red → check borrow checker on UserPagination."

shell(command="cargo build --release", async_mode=True)
# Continue with other work in this turn.
```

### Long download

```
shell(command="wget -q -O /data/model.bin https://example.com/big-model.bin", async_mode=True)
```

### Sub-agent call

```
shell(command="acpx run -- 'investigate the rate-limiter regression' > /tmp/acpx.out 2>&1", async_mode=True)
```

`acpx` and similar sub-agents can take many minutes. Async is the default for these.

### Build → test pipeline (one wake-up)

```
shell(
  command="cargo build --release && cargo test --release",
  async_mode=True,
)
```

If you want both phases visible in the output, prefix them with markers (`echo "=== BUILD ===" && ...`) so the wake-up tail tells you which phase failed.

## Synchronous Fallback

If you genuinely need the result in the current turn — a quick check, a single short command — call shell without `async_mode`:

```
shell(command="git status --porcelain", timeout_seconds=10)
```

The default `timeout_seconds=120` is the right ceiling for "this should be fast but I want a guard." Don't push it past a few minutes; if a command might take longer, that's exactly what `async_mode=True` is for.

## Manual `nohup` Pattern (legacy / non-harness environments)

If you're in an environment without the harness's async shell support — running scripts outside the agent loop, debugging a remote box, etc. — the manual pattern still works:

```bash
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-build.log"
mkdir -p "$HOME/logs"
nohup bash -lc '
  ( cargo build --release ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  echo "EXIT_CODE=${PIPESTATUS[0]}" >> "'"$OUTPUT_FILE"'"
' > /dev/null 2>&1 &
echo "Launched (pid=$!). Output: $OUTPUT_FILE"
```

Then poll with `grep EXIT_CODE= $OUTPUT_FILE`. There is no completion event in this mode — you have to come back and check, or wire up your own callback (e.g., `curl` to an HTTP endpoint at the end of the bash block).

**Inside the open-strix harness, prefer `async_mode=True`.** The manual pattern doesn't register with `shell_jobs_list`, doesn't surface in the web UI, and won't wake the agent.

## Key Gotchas

1. **Wake-up is fresh context** — your in-turn variables are gone. Anything you need has to be in the journal or a state file.
2. **Tail is ~4KB per stream** — for verbose output, reach for `shell_job_output` on the wake-up turn.
3. **Channel routing** — the completion event resumes on the channel where the job was spawned. Spawns from non-channel contexts (scheduled ticks, etc.) still wake the agent; the prompt just doesn't carry a channel.
4. **Multiple concurrent jobs** — each fires its own event, in subprocess-exit order, not spawn order.
5. **Killing jobs** — `kill <pid>` from another shell call. The completion event fires when the process actually exits (including from signal), with the appropriate non-zero exit code.
6. **Manual `nohup` pattern doesn't reentrant-wake** — only the harness's `async_mode=True` does. Don't mix the two for the same task.

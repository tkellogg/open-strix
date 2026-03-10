---
name: long-running-jobs
description: Run shell commands in the background with output capture and completion callbacks. Use when a command might take more than ~30 seconds (builds, tests, deployments, data processing) and you want to keep working while it runs.
---

# Long-Running Jobs

Some commands take minutes or hours. Rather than blocking on them, you can launch them in the background, capture all output, and get notified when they finish.

The core pattern: **detach the process, tee output to a file, post a callback when done.**

## When to Use This

**USE when:**
- Build commands (`cargo build`, `npm run build`, `make`)
- Test suites that take more than ~30 seconds
- Data processing, model training, large downloads
- Deployments or migrations
- Any command where you want to keep working while it runs

**DON'T USE when:**
- Quick commands (< 30s) — just run them normally and wait
- Commands you need the result of immediately
- Interactive commands that need stdin

## The Pattern

### Basic: Background with Output Capture

```bash
# Launch in background, capture ALL output to a file
nohup bash -lc '( YOUR_COMMAND_HERE ) 2>&1 | tee "/path/to/output.log"; echo "EXIT_CODE=${PIPESTATUS[0]}" >> "/path/to/output.log"' > /dev/null 2>&1 &
echo "PID: $!"
```

What each piece does:
- `nohup` — survives if the parent shell exits
- `bash -lc` — login shell so PATH and env vars are loaded
- `( cmd ) 2>&1` — subshell captures both stdout AND stderr
- `| tee "file"` — writes to file AND passes through (file is live-readable mid-run)
- `PIPESTATUS[0]` — captures the REAL exit code of your command, not tee's exit code
- `> /dev/null 2>&1 &` — fully detaches, returns immediately

### Checking Progress Mid-Run

The `tee` pattern means the output file is live — you can read it while the command is still running:

```bash
# Check current output (last 30 lines)
tail -30 /path/to/output.log

# Check if it's done yet (EXIT_CODE appears only when finished)
grep "EXIT_CODE=" /path/to/output.log
```

### Checking Completion

When the command finishes, the last line of the output file will be `EXIT_CODE=N`:

```bash
# Read the exit code
tail -1 /path/to/output.log
# Output: EXIT_CODE=0  (success) or EXIT_CODE=1  (failure)
```

## The Callback Pattern

The real power is combining background execution with a **callback to your own event queue**. When the job finishes, a POST to your loopback API wakes you up with context about what to do next.

### Full Pattern with Callback

```bash
# Set up variables
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-build.log"
mkdir -p "$HOME/logs"
API_PORT=8080  # your agent's loopback API port

# Launch the job
nohup bash -lc '
  ( cargo build --release ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "EXIT_CODE=$EXIT_CODE" >> "'"$OUTPUT_FILE"'"
  curl -s -X POST "http://127.0.0.1:'"$API_PORT"'/api/event" \
    -H "Content-Type: application/json" \
    -d "{\"prompt\": \"Background job finished (exit code $EXIT_CODE). Check '"$OUTPUT_FILE"' for output. If exit code is 0, the release build succeeded — read the last 20 lines for any warnings, then run the integration tests. If non-zero, read the full output and diagnose the build failure.\", \"source\": \"background-job:build\"}"
' > /dev/null 2>&1 &

echo "Launched (pid=$!). Output: $OUTPUT_FILE"
```

### Without Loopback API

If your agent doesn't have an API port configured, skip the curl and poll instead:

```bash
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-tests.log"
mkdir -p "$HOME/logs"

nohup bash -lc '
  ( pytest tests/ -v ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  echo "EXIT_CODE=${PIPESTATUS[0]}" >> "'"$OUTPUT_FILE"'"
' > /dev/null 2>&1 &

echo "Launched (pid=$!). Output: $OUTPUT_FILE"
echo "Poll with: grep EXIT_CODE= $OUTPUT_FILE"
```

Then check back later:

```bash
# Is it done?
grep "EXIT_CODE=" "$HOME/logs/bg-*-tests.log"

# Read the results
tail -50 "$HOME/logs/bg-*-tests.log"
```

## Writing Good Callback Messages

**This is the most important part.** The callback message is your future self's lifeline. When the event fires, you'll be in a fresh context with no memory of what you were doing. The callback message needs to contain everything you need to pick up where you left off.

### What Makes a Good Callback

A good callback message answers three questions:
1. **What finished?** — Not just "job done" but what the job WAS
2. **What was I thinking?** — The plan you had BEFORE submitting the job
3. **What should I do next?** — Concrete instructions for both success AND failure

### Bad Callback (don't do this)

```
Background job finished. Check the output file.
```

This tells your future self nothing. You'll waste time figuring out what the job was, why you ran it, and what to do with the result.

### Good Callback (do this)

```
Background job "release-build" finished (exit code $EXIT_CODE).
Output: /home/agent/logs/bg-20260310T143022Z-release-build.log

CONTEXT: I was working on PR #45 (add pagination to /api/users).
All unit tests passed, so I kicked off a release build to verify
no compilation issues before requesting review.

IF SUCCESS (exit 0):
- Read last 20 lines of output for any warnings
- If clean, run integration tests: pytest tests/integration/ -v
- If integration tests pass, post a comment on PR #45 that
  build + integration verified, ready for review

IF FAILURE (non-zero):
- Read the full output file to find the error
- Most likely cause: the new UserPagination struct may have
  lifetime issues with the borrowed slice in list_users()
- Check lines mentioning "borrow" or "lifetime"
- Fix and rebuild
```

### Callback Message Template

Use this as a starting point:

```
Background job "{label}" finished (exit code $EXIT_CODE).
Output: {output_file}

CONTEXT: [What were you doing? What's the bigger task?
What led to running this specific command?]

IF SUCCESS (exit 0):
- [Concrete next step 1]
- [Concrete next step 2]
- [What does success mean for the bigger task?]

IF FAILURE (non-zero):
- [Most likely failure mode and where to look]
- [Second most likely failure mode]
- [Recovery steps]
```

### Tips for Writing Callbacks

- **Be specific about file paths** — your future self can't guess
- **Name the PR/issue/task** — link back to the larger context
- **Anticipate failure modes** — you know what might go wrong right now, your future self won't
- **Include the command you ran** — especially if it had flags or arguments that matter
- **Mention what you already tried** — prevents retrying the same thing

## Cross-Platform: PowerShell

On Windows, the pattern uses `Tee-Object` and `$LASTEXITCODE`:

```powershell
# PowerShell equivalent
$OutputFile = "$HOME\logs\bg-$(Get-Date -Format 'yyyyMMddTHHmmssZ')-build.log"
New-Item -ItemType Directory -Force -Path "$HOME\logs" | Out-Null

Start-Job -ScriptBlock {
    & { YOUR_COMMAND_HERE } 2>&1 | Tee-Object -FilePath $using:OutputFile
    Add-Content -Path $using:OutputFile -Value "`nEXIT_CODE=$LASTEXITCODE"
    # Optional: callback via Invoke-RestMethod
    $body = @{ prompt = "Job finished. Check $using:OutputFile"; source = "background-job" } | ConvertTo-Json
    Invoke-RestMethod -Uri "http://127.0.0.1:8080/api/event" -Method Post -Body $body -ContentType "application/json"
}
```

## Output File Naming Convention

Use a consistent naming scheme so you can find logs later:

```
logs/bg-{UTC_TIMESTAMP}-{LABEL}.log
```

Examples:
- `logs/bg-20260310T143022Z-cargo-build.log`
- `logs/bg-20260310T150000Z-test-suite.log`
- `logs/bg-20260310T160000Z-deploy-staging.log`

The timestamp means logs sort chronologically. The label means you can grep for specific job types.

## Common Recipes

### Build + Test Pipeline

```bash
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-build-and-test.log"
mkdir -p "$HOME/logs"

nohup bash -lc '
  (
    echo "=== BUILD ===" &&
    cargo build --release &&
    echo "=== TESTS ===" &&
    cargo test --release 2>&1
  ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  echo "EXIT_CODE=${PIPESTATUS[0]}" >> "'"$OUTPUT_FILE"'"
  curl -s -X POST "http://127.0.0.1:8080/api/event" \
    -H "Content-Type: application/json" \
    -d "{\"prompt\": \"Build+test pipeline finished (exit $EXIT_CODE). Output: '"$OUTPUT_FILE"'. CONTEXT: Running full build+test before merging PR #12. IF SUCCESS: post merge-ready comment. IF FAILURE: check which phase failed (grep for === BUILD === and === TESTS ===) and diagnose.\", \"source\": \"background-job:build-and-test\"}"
' > /dev/null 2>&1 &
```

### Long Download

```bash
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-download.log"
mkdir -p "$HOME/logs"

nohup bash -lc '
  ( wget -q --show-progress -O /data/model.bin https://example.com/big-model.bin ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  echo "EXIT_CODE=${PIPESTATUS[0]}" >> "'"$OUTPUT_FILE"'"
' > /dev/null 2>&1 &
```

### Deploy with Rollback Context

```bash
OUTPUT_FILE="$HOME/logs/bg-$(date -u +%Y%m%dT%H%M%SZ)-deploy-staging.log"
mkdir -p "$HOME/logs"

nohup bash -lc '
  ( cd /app && git pull origin main && docker compose up -d --build ) 2>&1 | tee "'"$OUTPUT_FILE"'"
  EXIT_CODE=${PIPESTATUS[0]}
  echo "EXIT_CODE=$EXIT_CODE" >> "'"$OUTPUT_FILE"'"
  curl -s -X POST "http://127.0.0.1:8080/api/event" \
    -H "Content-Type: application/json" \
    -d "{\"prompt\": \"Staging deploy finished (exit $EXIT_CODE). Output: '"$OUTPUT_FILE"'. CONTEXT: Deploying commit abc123 (fix: rate limiter config) to staging. Previous stable commit: def456. IF SUCCESS: run smoke tests (curl staging.example.com/health). IF FAILURE: rollback with cd /app && git checkout def456 && docker compose up -d --build.\", \"source\": \"background-job:deploy\"}"
' > /dev/null 2>&1 &
```

## Key Gotchas

1. **`PIPESTATUS` is bash-only** — if your shell is `sh` or `dash`, use `bash -c` explicitly
2. **`nohup` vs `disown`** — `nohup` is more portable; `disown` is bash-specific
3. **Output file paths with spaces** — always quote them
4. **Large output files** — `tee` captures everything; very chatty commands (e.g., verbose builds) can produce huge files. Consider `tail -n 500` when reading back
5. **Callback curl can fail silently** — the loopback API might not be running. The output file is the ground truth; the callback is a convenience
6. **Multiple jobs** — each gets its own output file and PID. Track them by label

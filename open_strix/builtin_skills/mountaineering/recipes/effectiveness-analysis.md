# Recipe: Effectiveness Analysis

"Is X actually working?" answered with data, not vibes. Write a measurement script, run it against real logs/events, interpret results, identify the actual mechanism.

## Trigger

- A system or process has been running for a while without measurement
- Someone asks "how effective is X?" or "does X actually work?"
- An assumption about what's working needs verification
- Resource allocation decisions depend on knowing what's worth keeping

**Search intents before starting:**
- `search for: the system in question` — how does it work? what are the moving parts?
- `grep: event types, log entries` — what data do we already have?
- `search for: intended behavior` — what was this supposed to do?
- `search for: similar systems, comparisons` — is there a control group?

## Inputs

- System or process to evaluate (feature, tool, workflow, architecture)
- Available data sources (event logs, JSONL files, git history, API responses)
- The question to answer (specific: "what % of surfaced files get read?" not vague: "is it good?")

## Outputs

- Rerunnable measurement script (committed to `scripts/`)
- Quantitative findings with specific numbers
- Identification of actual mechanism (what's really working, vs what we thought was working)
- Recommendation: keep / modify / remove

## Harness Sketch

This is an **iterative analysis climb** — the first measurement often reveals the real question.

```
workspace/
├── question.md       # The specific question being answered
├── script.py         # Measurement script (rerunnable)
├── data/             # Intermediate outputs
│   └── summary.md    # Script output summary (disk-first, never pipe large files)
├── findings.md       # Interpretation of results
└── recommendation.md # Keep / modify / remove with rationale
```

**Eval criteria (deterministic):**
1. Script runs without errors on current data
2. Results include specific numbers (not "some" or "many")
3. Findings identify a mechanism, not just a metric
4. Recommendation follows from the data (not from prior beliefs)
5. Script is rerunnable for periodic tracking

**CRITICAL: Disk-first pattern.** Never pipe large files (events.jsonl, logs) into agent context. Write analysis to a file, read the summary only. This prevents OOM crashes on large datasets.

## Worked Example

**Half-RAG effectiveness — March 21, 2026**

Tim asked: "How effective is half-RAG actually? For every session, is a surfaced doc actually read?"

**What the search intents found:**
- Half-RAG (vector search) surfaces ~5 files per session via ChromaDB
- Events logged in events.jsonl: `vector_search` (files_surfaced) and `tool_call` (Read actions)
- 3,702 sessions and 18,332 file surfacings in the dataset
- Verge runs same architecture without vector search (natural control group)

**The script (scripts/half_rag_hits.py):**
- Correlates `vector_search` events with subsequent `Read` tool calls on those exact files
- Groups by session (timestamp proximity to claude_session start/end)
- Breaks down by trigger type (cron vs Discord message)

**Findings:**
- 2.8% file-level hit rate (511 reads out of 18,332 surfacings)
- 10.9% session hit rate (403/3,702 sessions had at least one surfaced file read)
- Cron jobs worse (0.7%) than Discord messages (3.1%)
- Best performers: topically precise files (family.md 39%, people/lumen.md 36%)
- Worst: archived files surfaced hundreds of times, read zero

**The mechanism discovery:**
- Original assumption: vector search provides useful ambient context
- Actual mechanism: intent-driven search works, ambient surfacing is 97% noise
- The 39% hit rate files were all high-intent lookups (someone asked about family → family.md surfaced → family.md read)
- Control group (Verge, no vector search): no felt gap

**Follow-up analysis (DAG):**
The half-RAG finding led directly to the file reference DAG analysis — measuring whether the implicit pointer graph (file references) was doing better. Answer: 86% stale edges. Both mechanisms were mostly decorative.

**What made it work:**
- Specific question from Tim, not vague "how's it going?"
- Real data (events.jsonl had months of logs)
- Natural control group (Verge) emerged from the ecosystem
- Script is rerunnable — can track changes over time
- Finding the mechanism was more valuable than the metric

## Failure Modes

| Failure | Detection | Fix |
|---------|-----------|-----|
| **Metric without mechanism** | "Hit rate is 2.8%" with no interpretation | Force: "what's actually happening in the sessions with hits vs misses?" |
| **Confirming prior beliefs** | Analysis finds what you expected | Run the analysis BEFORE forming the hypothesis. Look at the data first. |
| **OOM from large data** | Script pipes full events.jsonl into context | Disk-first: write summary to file, read summary only |
| **One-time analysis** | Script works once, can't be rerun | Commit to scripts/, use relative paths, document data dependencies |
| **Missing the control group** | No baseline comparison | Search for: what's the closest thing to "this system but without X?" |
| **Survivorship bias** | Only measuring successes, not the failures | Include denominator: how many times did X fire total, not just when it worked |

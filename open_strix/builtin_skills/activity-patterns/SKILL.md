---
name: activity-patterns
description: Analyze conversation activity patterns using embedding-free metrics. Use when you want to understand the texture of your conversations — who's talking, how much, and what the balance looks like. Run the dashboard periodically or when asked about communication patterns.
---

# Activity Patterns

Embedding-free conversation metrics derived from events.jsonl. No external APIs,
no embeddings — pure message counts and character volumes.

## What This Measures

### Quantity Ratio (QR)

**Output volume / input volume** (character counts).

This is NOT a health score. It's a description of what's happening:

| QR Range | Region | What it means |
|----------|--------|---------------|
| < 0.5 | Absorbing | Mostly listening. Could be appropriate (learning, monitoring) or concerning (disengaged). |
| 0.5 - 1.5 | Conversational | Roughly balanced exchange. Back-and-forth dialogue. |
| 1.5 - 3.0 | Productive | Outputting more than receiving. Normal during work sprints, research delivery. |
| 3.0 - 5.0 | Chatty | Significantly more output. Check: is this a sprint or am I monologuing? |
| > 5.0 | Monologue | Mostly one-directional output. Morning digests, overnight work ticks push this up. |

**Important:** QR skews high early in the day (morning digest is one-directional output
before bidirectional conversation starts). A high daily QR doesn't mean "too much talking" —
it depends on what's happening. Overnight work ticks produce output with zero input.

### Effective Sources

**exp(Shannon entropy)** of message count distribution across input sources.

- 1.0 = all input from one person (or monologue)
- 2.0 = two roughly equal contributors
- N = N equal contributors

This captures whether the agent is hearing from multiple voices or just one.

### Volume Breakdown

Per-source character counts showing who's providing input and how much.

## Running the Dashboard

```bash
# Text report (no dependencies beyond stdlib)
uv run python -m open_strix.builtin_skills.scripts.activity_patterns --repo-root . --text-only

# With image (requires matplotlib)
uv run python -m open_strix.builtin_skills.scripts.activity_patterns --repo-root . --days 7

# Custom output path
uv run python -m open_strix.builtin_skills.scripts.activity_patterns --repo-root . --output state/dashboards/activity.png
```

## Interpreting Results

**QR alone doesn't tell you much.** Combine it with:
- **Effective sources** — High QR + 1.0 effective sources = monologue. High QR + 3.0 sources = active multi-party with high agent output.
- **What's actually happening** — A sprint day looks different from a quiet day. The dashboard describes texture, not health.
- **Time of day** — Morning QR is always inflated by digest delivery.

**The goal is visibility, not diagnosis.** These metrics make invisible dynamics legible.
They don't tell you what to do about them — that's your judgment call.

## Per-Agent Calibration

The same QR value means different things for different agent roles:
- **An adversary agent** might have QR < 1.0 most of the time (absorbing is the role). A *spike* above 1.0 is the interesting signal — it found something worth engaging with.
- **A jester agent** might swing wildly. Consistency isn't the goal.
- **An assistant** in productive mode might sit at 2-3 QR naturally.

Calibrate expectations to the agent's role, don't apply universal thresholds.

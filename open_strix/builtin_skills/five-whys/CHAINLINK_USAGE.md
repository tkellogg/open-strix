# Using Chainlink for 5 Whys

This document explains how to use chainlink as the storage backend for 5 Whys
analyses. Read SKILL.md first for the methodology — this covers the tooling.

## Before You Start

Make sure you're in your RCA directory (see CHAINLINK_SETUP.md):

```bash
cd rca   # relative to your home repo root
```

All chainlink commands below assume you're in a directory with a `.chainlink/`
ancestor. If you get "No .chainlink directory found," you're in the wrong place.

## Creating a 5 Whys Tree

### Step 1: Create the root issue (the problem)

```bash
chainlink issue create \
  --label rca \
  --label "5-whys" \
  "11 consecutive classifier errors, 57 min wasted"
# Returns: Created issue #1
```

The `rca` and `5-whys` labels distinguish these from any other issues. Use them
consistently.

### Step 2: Create Why nodes as subissues

```bash
# First why
chainlink issue subissue 1 \
  --label rca \
  "Why: Proposer suggested CatBoost, not in allowed list"

# Second why (branching — multiple causes)
chainlink issue subissue 1 \
  --label rca \
  "Why: No validation step checks algorithm against allowed list"
```

### Step 3: Go deeper with nested subissues

```bash
# Why did the proposer suggest CatBoost?
chainlink issue subissue 2 \
  --label rca \
  "Why: Proposer has no documentation of available algorithms"
```

### Step 4: Mark bedrock nodes

When you hit a root cause, label it:

```bash
chainlink issue label 4 bedrock
```

### Step 5: Create action items from bedrock nodes

```bash
# Action item linked to the bedrock finding
chainlink issue create \
  --label action-item \
  --label rca \
  "Add algorithm allowlist to proposer's system prompt"

# Block it by the analysis (action depends on understanding)
chainlink issue block 5 4
```

### Step 6: Close the chain when analysis is complete

```bash
# Close the root — cascades show the full tree is analyzed
chainlink issue close 1
```

## Viewing Trees

```bash
# See the full tree for an analysis
chainlink issue tree 1

# List all open RCA chains
chainlink issue list --label rca

# List unresolved action items
chainlink issue list --label action-item

# Search across all analyses
chainlink issue search "classifier"
```

## Using Falsification Cascades

Chainlink has built-in support for falsification — marking an assumption as wrong
and seeing what downstream conclusions break. This is useful when a 5 Whys chain
turns out to have a wrong intermediate answer.

```bash
# You discover that Why-2 was actually wrong
chainlink issue falsify 3 "Validation step DID exist but was bypassed"

# See what breaks
chainlink issue cascade 3
```

## Using Sessions for Analysis Time

```bash
# Start a session when you begin an analysis
chainlink session start

# Mark which issue you're working on
chainlink session work 1

# End when done
chainlink session end --notes "Completed classifier error RCA, 3 action items"
```

## Conventions

| Label | Meaning |
|---|---|
| `rca` | Part of a root cause analysis |
| `5-whys` | Root issue of a 5 Whys tree |
| `bedrock` | Leaf node — root cause found |
| `action-item` | Concrete fix derived from bedrock |
| `external-boundary` | Root cause outside your control |
| `accepted-tradeoff` | Known and intentional design choice |

## Example: Full 5 Whys in Chainlink

```
#1  [rca, 5-whys] Agent unresponsive for 62 minutes
├── #2  [rca] Why: LLM API call hung and never returned
│   └── #3  [rca] Why: No turn-level timeout in harness
│       └── #4  [rca, bedrock] Why: Harness assumes LLM calls always complete
│           └── #7  [action-item] Add configurable turn timeout to harness
├── #5  [rca] Why: Agent already degraded from sync sleep-polling
│   └── #6  [rca, bedrock] Why: No positive evidence of async reliability
│       └── #8  [action-item] Add reliability metrics to async callback docs
└── #9  [rca, external-boundary] Why: LLM endpoint unstable — shared resource
```

## Querying Across Analyses

Find patterns across multiple 5 Whys:

```bash
# All bedrock findings
chainlink issue list --label bedrock

# All open action items from RCAs
chainlink issue list --label action-item --status open

# Search for a theme across all analyses
chainlink issue search "timeout"
```

This is where the separate database pays off — every issue in this DB is an RCA
artifact. No task-tracking noise to filter through.

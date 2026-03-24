# Mountaineering Recipes

Recipes are proven climb patterns — things that have already worked, codified for reuse.

Each recipe encodes **what to look for** (search intents) so the agent knows when and how to set up the climb. The recipe itself is the structured demand; hybrid search (FTS5 + vector) provides the retrieval.

## How to Use

1. **Match the situation to a recipe** — scan trigger conditions
2. **Run the search intents** — find the specific files, data, and context the recipe needs
3. **Set up the harness** — use the recipe's harness template
4. **Climb** — the recipe tells you what "better" looks like

## Available Recipes

| Recipe | When | Hill Shape |
|--------|------|------------|
| `paper-deep-read.md` | External paper needs genuine engagement, not just summary | LLM-judged quality of reaction notes |
| `cross-agent-critique.md` | Document needs substantive review from multiple angles | Convergence across independent reviewers |
| `effectiveness-analysis.md` | "Is X actually working?" needs data, not vibes | Deterministic metrics from log/event data |
| `thread-engagement.md` | External post warrants substantive public response | Quality of contribution to ongoing conversation |

## Recipe Structure

Each recipe follows the same format:

- **Trigger** — when this recipe applies
- **Search intents** — what to look for before starting
- **Inputs / Outputs** — what goes in, what comes out
- **Harness sketch** — how to set up the climb
- **Worked example** — a specific time this pattern succeeded, with enough detail to replicate
- **Failure modes** — what goes wrong and how to detect it

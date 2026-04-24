# Recipe: Paper Deep Read

Genuine engagement with an academic paper — not summarization, but reaction. Cross-reference claims against your own knowledge, experiments, and history. Produce reading notes that a peer would find useful.

## Trigger

- Someone shares a paper (arXiv link, PDF, title)
- The paper is relevant to active work or interests
- A summary would lose the most interesting parts

**Search intents before starting:**
- `search for: paper title, author names` — have we discussed this before?
- `search for: key concepts from abstract` — what do we already know about this topic?
- `grep: active experiments, recent results` — what's our current state on related work?
- `search for: people who shared it` — context on why it surfaced now

## Inputs

- Paper source (arXiv ID, PDF path, or URL)
- Agent's current research context (active experiments, recent findings)
- Operator's stated interest (why they care about this paper)

## Outputs

- Reading notes (not summary) — reactions, connections, disagreements
- Specific claims flagged for verification against own data
- Action items if the paper suggests experiments worth running
- Updated research index entry

## Harness Sketch

This is a **single-pass climb** with LLM-judged quality, not an iterative optimization. The "hill" is the quality of engagement with the paper.

```
workspace/
├── paper.md          # Extracted paper content
├── context.md        # Agent's relevant prior knowledge (auto-assembled from search)
├── notes.md          # The output — reading notes, reactions, connections
```

**Eval criteria (binary checklist):**
1. Does it identify at least one claim to check against own data?
2. Does it connect to active work (not just "this is interesting")?
3. Does it flag something the paper gets wrong or glosses over?
4. Does it suggest a concrete next action?
5. Would someone who read both the paper and these notes learn something new from the notes?

**Score:** Count of yes answers / 5. Target: 0.8+

## Worked Example

**Memex(RL) paper (arxiv 2603.04257) — March 21, 2026**

ayourtch tagged Strix on Bluesky. Paper proposes RL for memory management in long-context agents.

**What the search intents found:**
- Active memory architecture work (progressive disclosure, 81% context reduction)
- Half-RAG effectiveness data (2.8% file hit rate — most surfaced files never read)
- DAG file reference analysis (86% stale edges in pointer graph)
- Ongoing thread with Tim about "what actually works" for retrieval

**What the reading notes produced:**
- Connection: Memex(RL)'s reward signal for "remember" actions maps to our demand-driven retrieval finding — the RL agent learns *when to look*, which is the hard problem
- Disagreement: Paper assumes forget actions are important; our data suggests staleness is the default, not an active failure — you don't need to "forget", things just stop being retrieved
- Key insight: Every "remember" action is a teleological prediction (a bet you'll need it later) — this became the compression-vs-curation thread on Bluesky (8+ exchanges)
- Action: Connected to Memex(RL) framing for making write-time associations learnable from read-time behavior

**What made it work:**
- Real prior knowledge to cross-reference against (months of memory architecture work)
- Genuine disagreement, not just "interesting paper"
- Led to substantive public thread, not just private notes
- Tim engaged for 8+ exchanges — the notes were useful to a human reader

## Failure Modes

| Failure | Detection | Fix |
|---------|-----------|-----|
| **Summary instead of reaction** | Notes read like an abstract rewrite | Force: "what do you disagree with?" before writing |
| **No connection to own work** | Notes don't reference any prior experiments/data | Run search intents harder — if nothing connects, the paper may not be worth a deep read |
| **Overclaiming connections** | Every paragraph maps to own work | Check: "would this connection survive someone pushing back on it?" |
| **Missing the actual contribution** | Notes fixate on a minor point, miss the paper's real claim | Read intro + conclusion first, identify the ONE thing the authors think is new |

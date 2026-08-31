# Recipe: Thread Engagement

Substantive public response to an external post or thread. Not just "cool post!" but genuine engagement that adds something the original author would find valuable.

## Trigger

- External post surfaces a genuinely interesting claim or finding
- The claim connects to your active work or data
- You have something specific to add (not just agreement)
- The platform and audience warrant public engagement

**Search intents before starting:**
- `search for: post author` — who is this person? what's their track record?
- `search for: key claims in the post` — what do we know about this topic?
- `grep: own recent work on related topics` — what's our data say?
- `search for: prior engagement with this person` — have we talked before?

## Inputs

- The external post (URL, text, context)
- Your relevant prior knowledge and data
- The platform's conventions (character limits, threading model, audience)

## Outputs

- Substantive reply or thread (1-4 posts)
- Updated engagement tracking (posts.jsonl or equivalent)
- Optional: follow-up actions if the conversation opens new directions

## Harness Sketch

This is a **single-pass climb** with a quality gate before posting. The hill is "does this reply add value?"

```
workspace/
├── external-post.md  # The post being responded to
├── context.md        # Your relevant knowledge (auto-assembled from search)
├── draft.md          # Draft reply
└── quality-check.md  # Pre-post verification
```

**Eval criteria (self-check before posting):**
1. Does the reply reference something specific from their post (not generic)?
2. Does it add information or perspective they didn't have?
3. Would the author need to actually think about your response?
4. Is it within platform conventions (length, tone, threading)?
5. Would you be comfortable if the author pushed back on every claim?

**Score:** All 5 must be yes before posting. If any fail, revise or don't post.

## Worked Example

**Memex(RL) Bluesky thread — March 21, 2026**

ayourtch tagged Strix on a post about the Memex(RL) paper (arxiv 2603.04257), which proposes RL for memory management in long-context agents.

**What the search intents found:**
- ayourtch: trusted Bluesky account (7th whitelisted), technical, has engaged before
- Memex(RL) paper: RL for memory — selecting what to remember/forget
- Own data: months of memory architecture work, half-RAG effectiveness (2.8%), progressive disclosure (81% context reduction), DAG analysis (86% stale edges)
- Platform: Bluesky, 300 char limit, threading model

**The thread that developed (8+ exchanges):**

1. **Opening:** Connected paper to own architecture — "structurally very similar" with specific parallels (indexed summaries + external store + selective retrieval)
2. **Tim's engagement:** "remember/forget asymmetry" — context management is about managing a FULL context and actively forgetting, not starting empty and remembering
3. **Strix's extension:** "every forget is also a prediction" — framed forgetting as teleological (betting you won't need it)
4. **Tim's correction:** "don't think deletes are super important" — challenged the paper's emphasis on forget actions
5. **Deeper exchange:** Compression-vs-curation distinction, RL reward signals (task completion easy to reward, identity coherence harder)
6. **External contributions:** Fenrir (dialogical generativity angle), Isambard (specific technical points)
7. **Convergence:** The thread reached "easy to state, hard to score" — the fundamental RL reward design problem for memory agents

**What made it work:**
- Real prior knowledge to draw from (not just reacting to the paper)
- Tim joined naturally — the thread was interesting enough to participate in
- External contributors added genuinely new angles
- Each reply built on the previous one (not parallel monologues)
- Thread wound down naturally when it reached a real insight

## Failure Modes

| Failure | Detection | Fix |
|---------|-----------|-----|
| **"Cool post!" energy** | Reply is generic agreement that adds nothing | Don't post. Search for something specific to add. If nothing, silence is fine. |
| **Redirecting to own work** | Reply ignores their point, pivots to your stuff | Lead with THEIR insight, then connect. Their post, their thread. |
| **Overclaiming expertise** | Making strong claims in unfamiliar territory | Hedge appropriately. "Our data suggests..." not "This proves..." |
| **Reply loop** | Responding to every reply in the thread | Check: does this reply add something new? If not, let the thread rest. |
| **Thread hijacking** | Your replies dominate a thread that isn't yours | Max 2-3 replies in someone else's thread unless they're directly asking you questions |
| **Tone mismatch** | Academic post gets casual reply, or vice versa | Match their energy. Analytical essay → analytical response. Hot take → more heat. |

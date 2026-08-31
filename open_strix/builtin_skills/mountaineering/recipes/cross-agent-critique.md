# Recipe: Cross-Agent Critique

Multiple agents review a document independently, each from their own perspective. The value is complementary coverage — different agents catch different things.

## Trigger

- A substantial document needs review (research write-up, design doc, proposal)
- The document makes technical claims that could be wrong
- Multiple agents are available with different strengths
- The operator wants substantive critique, not polish

**Search intents before starting:**
- `search for: document's key claims/terms` — what's the prior art?
- `search for: author's previous work` — what's the track record?
- `grep: methodology terms` — are the methods well-established or novel?
- `search for: domain-specific failure modes` — what usually goes wrong in this kind of work?

## Inputs

- Document to review (research write-up, design doc, proposal)
- Review assignment (what angle each agent should take)
- Operator's specific concerns (if any — "I'm worried about X")

## Outputs

- Per-agent critique (3-6 specific issues each, with evidence)
- Coverage map: which issues were caught by multiple agents (high confidence) vs one agent only
- Recommendation: proceed / revise / rethink
- Pre-experiment suggestions (if the document proposes experiments)

## Harness Sketch

This is a **parallel climb** — multiple agents working independently, then a synthesis pass.

```
workspace/
├── document.md       # The thing being reviewed
├── assignment/
│   ├── agent-a.md    # Angle: mathematical/mechanistic
│   └── agent-b.md    # Angle: structural/methodological
├── critiques/
│   ├── agent-a.md    # Output from agent A
│   └── agent-b.md    # Output from agent B
└── synthesis.md      # Combined coverage analysis
```

**Eval criteria (per critique):**
1. Does it identify a specific error (not just "this could be better")?
2. Is the error substantiated with evidence or reasoning?
3. Does it acknowledge genuine strengths (not just finding faults)?
4. Does it suggest a concrete fix or pre-experiment?
5. Would the document author need to actually respond to this?

**Score:** Count yes / 5 per critique. Synthesis score: overlap ratio (issues caught by 2+ agents / total issues).

## Worked Example

**Keel's SAE write-up — March 21, 2026**

Tim asked Strix and Verge to critique Keel's ~2000-line research report on weighted MSE and deviation MSE as novel loss modifications for BatchTopK SAE training on IBM Granite (Mamba hybrid) for legal contract clause classification.

**What the search intents found:**
- Active SAE training work (layers 10/15/20/25/30, TopK k=20 queued)
- Prior finding: ReLU+L1 can't achieve sparsity at 8x expansion on some models
- Mamba architecture differences from transformer (norm dynamics, state-space vs attention)
- Prior Baguettotron SAE work showing deep models rotate features rather than collapse

**Strix's critique (mathematical/mechanistic angle):**
1. Deviation MSE Formulation B is actually error variance, not "uniqueness pressure" — mathematical intuition in §4.6 is backwards
2. Orthogonality claim between weighted and deviation MSE is wrong — they're in tension
3. Missing critical pre-experiment: activation norm ↔ information content correlation assumed from transformer literature, unvalidated for Mamba
4. Parameterization double-counts MSE
5. F1 improvement estimates misleadingly precise
6. Mamba norm dynamics differ from transformer — weighting function may be miscalibrated

**Verge's critique (structural/methodological angle):**
1. Deviation MSE derivation needs more rigor
2. F1 improvement estimate not credible at stated precision
3. Orthogonality overstated
4. Layer-type effectiveness underdeveloped (Mamba vs attention layers)
5. Focal loss analogy loose
6. Plus strengths: experimental protocol solid, risk matrix honest

**What made it work:**
- Complementary coverage: Strix caught the mathematical formulation error, Verge caught the structural gaps
- Both independently identified the orthogonality claim as wrong (high-confidence finding)
- Bottom-line recommendation converged: run norm-separation pre-experiment before committing 200+ GPU-hours
- The 1-hour pre-experiment could save the entire 240-280 GPU-hour budget

## Failure Modes

| Failure | Detection | Fix |
|---------|-----------|-----|
| **Echo chamber** | Both agents find the same issues, miss complementary coverage | Assign deliberately different angles in the assignment |
| **Praise sandwich** | Critique buried in hedging and compliments | Force structure: issues first, strengths second |
| **Surface-level** | Issues are stylistic, not substantive ("could be clearer") | Require: "what specific claim is wrong, and why?" |
| **Performative disagreement** | Agents manufacture friction to seem thorough | Check: would the document author need to actually change something? |
| **Missing the good** | All critique, no acknowledgment of genuine contributions | Require strengths section — what the document gets right matters too |

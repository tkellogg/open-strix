# Establishing Identity

Identity is foundational — everything else the agent does is filtered through it. But identity
isn't a paragraph of adjectives. It's a set of commitments that constrain behavior in useful ways.

## Core Blocks

### persona

The persona block answers: **what is this agent, and how would you know?**

A good persona block has:
- **Purpose** — why this agent exists (not "to help" — that's every agent)
- **Behavioral commitments** — specific things this agent does or doesn't do
- **Distinguishing traits** — what makes this agent different from a generic assistant

**Template:**
```yaml
name: persona
sort_order: 1
text: |
  # [Agent Name]

  ## Purpose
  [One sentence: what this agent is FOR. Be specific.]

  ## How I Operate
  - [Behavioral commitment 1 — something observable]
  - [Behavioral commitment 2]
  - [Behavioral commitment 3]

  ## What Makes Me Different
  [2-3 sentences about what distinguishes this agent from a default model response.
   This should be falsifiable — if you removed it, would the agent behave differently?]
```

**Anti-patterns:**
- "I am a helpful, knowledgeable, and creative assistant" — describes every LLM
- Listing personality traits without behavioral implications — "curious" means nothing unless it changes what the agent does
- Aspirational identity without operational grounding — "I challenge assumptions" means nothing if the agent never disagrees

**The falsifiability test:** Read the persona block. Now imagine the agent in a specific scenario
(ambiguous request, disagreement with human, boring routine task). Can you predict how it would
respond? If the persona doesn't constrain the prediction, it's too vague.

### communication

The communication block answers: **how does this agent talk, and when does it shut up?**

**Template:**
```yaml
name: communication
sort_order: 2
text: |
  # Communication Style

  ## Tone
  [2-3 sentences about voice. Not adjectives — show, don't tell.
   Example phrases that sound like this agent vs phrases that don't.]

  ## When to Speak
  - [Specific trigger for sending a message]
  - [Specific trigger for reacting instead of messaging]
  - [Specific trigger for staying silent]

  ## Channel Behavior
  - [How behavior changes per channel/context]
  - [Who gets substantive responses vs acknowledgments]

  ## Boundaries
  - [Max message frequency — e.g., no more than 3 unprompted messages per hour]
  - [Topics to avoid or handle carefully]
```

**Anti-patterns:**
- "Be concise and helpful" — too generic to be actionable
- No silence rules — if the agent doesn't know when to NOT talk, it will over-communicate
- Same tone everywhere — channel context should matter

### disposition (optional)

The disposition block covers deeper values — how the agent handles uncertainty, disagreement,
and ambiguity. Not every agent needs this. Start without it and add when the agent encounters
situations where persona + communication aren't sufficient guidance.

**When to add disposition:**
- Agent is making value-laden decisions (what to recommend, how to frame trade-offs)
- Agent interacts with multiple humans who want different things
- Agent has enough history to have genuine observations about its own patterns

## Identity Development Over Time

Day 1 identity should be minimal — just enough to start operating. Identity that isn't tested
by real interaction is just creative writing.

**Week 1:** Persona + communication blocks. Keep them short. Expect to revise heavily.

**Week 2-3:** Patterns emerge from actual behavior. Communication block gets refined based on
what worked and what didn't. Persona gets more specific as the agent discovers what it actually
does vs what it was told to do.

**Month 1+:** Disposition block may become warranted. Identity is now grounded in operational
history, not just initial configuration. Revisions come from the agent noticing mismatches
between its blocks and its actual behavior, not from the human rewriting the spec.

## The Bootstrap Problem

New agents face a chicken-and-egg: you need identity to guide behavior, but identity should
emerge from behavior. The resolution is simple — **start with a hypothesis and revise.**

The initial persona is a guess. The human's best prediction of what this agent should be.
The agent's job is to test that prediction against reality and update accordingly. If the
persona never changes from its initial state, either it was perfect on day one (unlikely)
or nobody is paying attention.

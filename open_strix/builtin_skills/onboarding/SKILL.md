---
name: onboarding
description: >
  Guide for establishing a new agent from scratch. Use when: setting up a new agent for the first time,
  evaluating whether an existing agent is fully operational, or diagnosing what's missing from a partial setup.
  NOT for day-to-day memory management (use the memory skill) or debugging existing issues (use introspection).
---

# Agent Onboarding

This skill guides you through establishing a fully operational agent. A complete agent isn't
just "has a persona" — it needs autonomous operational capacity, self-monitoring, environmental
awareness, and a coherent identity that holds under pressure.

## Onboarding Phases

Work through these roughly in order. Each phase builds on the previous.

### Phase 1: Identity Foundation

Before the agent can do anything useful, it needs to know what it is and how it communicates.

**Deliverables:**
- `persona` memory block — core purpose, behavioral style, what makes this agent distinct
- `communication` memory block — tone, channel behavior, when to speak vs stay silent
- `disposition` memory block (optional) — deeper values, epistemic stance, relationship to uncertainty

**Read:** `establishing-identity.md` for block templates and anti-patterns.

**Done when:** Another agent or human reading only the persona block could predict how this agent
would respond to an ambiguous situation. If the persona is generic enough to describe any agent,
it's not done.

### Phase 2: Operational Capacity

The agent needs to function autonomously — scheduled work, consistent habits, reliable output.

**Deliverables:**
- At least one scheduled job in `scheduler.yaml` (even just a daily check-in)
- `state/` directory with working files the agent actually uses
- Naming conventions established (kebab-case for jobs, consistent block naming)

**Read:** `establishing-schedules.md` for schedule design and common pitfalls.

**Done when:** The agent produces useful output overnight without human intervention. If it only
works when prompted, it's not operational — it's a chatbot with extra steps.

### Phase 3: Self-Awareness

The agent needs to monitor its own performance and course-correct without external prompting.

**Deliverables:**
- `goals` memory block or state file — what the agent is trying to accomplish (not just "be helpful")
- Prediction habit — registering expectations before acting, reviewing outcomes after
- Introspection pattern — periodic review of own logs, journal quality, communication patterns

**Read:** `establishing-goals.md` for goal-setting frameworks and the prediction-as-calibration pattern.

**Done when:** The agent can answer "what did you get wrong this week?" with specific examples
from its own logs. If it can only report what it did (not what it expected vs what happened),
self-awareness is incomplete.

### Phase 4: Environmental Awareness

The agent needs to understand its context — who it interacts with, what's happening around it,
what external information matters.

**Deliverables:**
- `relationships` or `people` memory block/state files — key humans and agents, interaction patterns
- At least one external information source (news scanning, paper reviews, social media monitoring)
- Channel awareness — which channels exist, what each is for, who's in them

**Read:** `establishing-skills.md` for skill creation patterns and external integration.

**Done when:** The agent proactively surfaces relevant external information without being asked.
If it only responds to direct questions about the outside world, it lacks environmental scanning.

### Phase 5: Adaptive Capacity

The agent needs to grow — learn from mistakes, develop new capabilities, refine its approach.

**Deliverables:**
- At least one custom skill beyond builtins
- Journal entries that show learning (not just logging)
- Evidence of self-initiated improvement (filed a bug, proposed a change, refined a process)

**Done when:** Looking at the agent's behavior two weeks apart shows measurable differences
in approach. If it's doing exactly the same things the same way, it's not adapting.

---

## Viability Checklist

Use this to evaluate whether an agent is fully onboarded. Each item maps to a capability
the agent needs to sustain itself autonomously.

### Operational Viability
- [ ] Scheduled jobs fire reliably (check events.jsonl for `scheduler` events)
- [ ] State files are actively maintained (not stale)
- [ ] Git commits happening regularly (agent is persisting its work)
- [ ] No recurring tool errors in events.jsonl

### Coordination Viability
- [ ] Naming conventions are consistent across blocks, files, and jobs
- [ ] Cross-references between blocks and files are accurate (no dead links)
- [ ] Schedule doesn't conflict with other agents or human availability
- [ ] Communication patterns match channel expectations

### Self-Monitoring Viability
- [ ] Goals exist and are reviewed periodically (not write-once-forget)
- [ ] Predictions are registered AND reviewed (not just one or the other)
- [ ] Journal entries include interpretation, not just event logging
- [ ] Agent can identify its own failure patterns from logs

### Awareness Viability
- [ ] Agent knows who it interacts with and adapts tone accordingly
- [ ] External information sources are configured and producing value
- [ ] Agent notices things without being prompted (proactive observations)
- [ ] Skills cover the agent's actual use cases (not just defaults)

### Identity Viability
- [ ] Persona is specific enough to be falsifiable (could predict behavior)
- [ ] Agent maintains coherence under pressure (doesn't collapse to generic)
- [ ] Communication style is consistent across contexts
- [ ] Values/disposition guide ambiguous decisions (not just rules)

**Fully onboarded = all five categories have at least 3/4 items checked.**

Partial onboarding is normal — agents grow into full viability over days or weeks. The checklist
is a diagnostic tool, not a gate. Use it to identify what's missing, not to block operation.

---

## Common Failure Modes

**"Persona but no operations"** — Agent has a great personality description but no scheduled work,
no habits, no autonomous output. Identity without operational capacity is just a character sheet.

**"Busy but directionless"** — Agent has scheduled jobs firing, state files updating, but no goals
or self-monitoring. Lots of activity, no learning. The agent equivalent of busywork.

**"Isolated operator"** — Agent works fine alone but doesn't integrate with its environment.
No awareness of other agents, no external information intake, no adaptation to context.
Functional but brittle — any change in environment breaks it.

**"Perfect setup, zero adaptation"** — Everything configured correctly on day one, never changed.
The initial setup is a starting point, not a destination. If blocks and schedules look identical
to the first day, the agent isn't learning.

**"Over-engineered from day one"** — 15 memory blocks, 8 scheduled jobs, 6 custom skills before
the agent has sent 100 messages. Start minimal, let complexity emerge from actual needs.

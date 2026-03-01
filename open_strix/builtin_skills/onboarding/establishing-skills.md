# Establishing Skills & Environmental Awareness

Custom skills extend what an agent can do. Environmental awareness ensures the agent
understands its context well enough to act appropriately.

## One-Off Skills

Skills are how agents develop specialized capabilities beyond the builtins. The skill-creator
builtin handles the mechanics — this guide covers when and what to build.

### When to Create a Skill

Create a skill when:
- The agent repeatedly does the same multi-step task (automate it)
- A specific domain requires context the agent won't have by default (document it)
- The agent needs to interface with an external system (codify the interface)

**Don't create a skill when:**
- The task has only happened once (wait for repetition)
- A memory block or state file would suffice (skills are for procedures, blocks are for context)
- The builtin skills already cover it (check first)

### Skill Design Principles

**The description is the trigger.** The SKILL.md description field determines when the agent
loads the skill. Make it specific:

```yaml
# Good — clear trigger conditions
description: >
  Post to Bluesky. Use when composing posts, replies, or threads.
  NOT for reading Bluesky (use fetch tools directly).

# Bad — too vague to route
description: Social media management
```

**Narrow scope beats broad coverage.** A skill that does one thing well is more reliable than
one that tries to cover everything. Split broad domains into focused skills.

**Include the failure modes.** What goes wrong when using this skill? Document the common
errors so the agent doesn't rediscover them every time:

```markdown
## Common Errors
- Posts over 300 characters are silently truncated — always check length
- CIDs must be fetched fresh, not reused from previous sessions
- Absolute paths required: `cd /home/botuser/agent-name && ...`
```

**Concrete examples over abstract instructions.** Show the exact commands, not descriptions
of commands. Agents execute better from examples than from principles.

### Skill Lifecycle

1. **Need identified** — agent or human notices a repeated task
2. **Minimal version** — SKILL.md with just enough to execute the task
3. **Failure refinement** — after first failures, add error handling and common pitfalls
4. **Stabilization** — skill works reliably, rarely needs updates
5. **Decomposition** — if the skill grows too large, split into focused sub-skills

Most skills should reach stabilization within a week of active use. If a skill requires
constant edits, it might be covering too broad a domain.

## Environmental Awareness

### People Tracking

Agents that interact with multiple humans need context about each one. At minimum:
- Name and how to reference them (Discord ID for mentions, etc.)
- Relationship to the agent's primary human
- Communication preferences (casual/formal, when they're active)
- Key context (what they work on, what they care about)

**Storage options:**
- `relationships` memory block — for 2-5 key people always in context
- `state/people/` directory — for a larger network, loaded on demand
- Both — block for core relationships, files for extended network

### External Information Sources

An agent with no external information intake operates in a bubble. The specific sources
depend on the agent's purpose:

- **Research agent:** arXiv scanning, paper reviews, literature tracking
- **Social agent:** Bluesky/Twitter monitoring, engagement tracking
- **Work agent:** Project status, team updates, deadline tracking
- **General:** News scanning, relevant industry developments

**The scanning pattern:**
1. Scheduled job triggers the scan
2. Agent checks external source for new/relevant information
3. Filters for relevance (most information is noise)
4. Surfaces genuinely interesting findings proactively
5. Logs what was scanned even when nothing was surfaced (prevents re-scanning)

**Quality signal:** The agent surfaces things the human didn't already know. If every "finding"
is something the human saw first, the scanning isn't adding value.

### Channel Awareness

For Discord-based agents, understand the channel landscape:
- Which channels exist and what each is for
- Who's in each channel (humans, other agents, both)
- Appropriate behavior per channel (banter channel vs work channel)
- When channels are active vs quiet

This context should live in a memory block or state file that's updated as the channel
landscape evolves. A phone-book pattern works well:

```markdown
# Channel Reference

## buddy-channel (ID: 123456)
- Type: Social, multi-agent
- Members: Tim, Lily, Strix, Verge, Motley
- Behavior: Casual, banter OK, substantive discussion welcome
- Peak hours: 8am-10pm ET

## shared-research (ID: 789012)
- Type: Technical, focused
- Members: Tim, Strix, Verge
- Behavior: Paper discussion, analysis, no banter
- Peak hours: Follows Tim's research sessions
```

## Putting It Together

A fully environmentally-aware agent:
1. Knows who it's talking to and adapts accordingly
2. Brings in external information without being asked
3. Has skills for its repeated specialized tasks
4. Understands its channel landscape and behaves appropriately
5. Updates its environmental model as things change

This doesn't happen on day one. Environmental awareness accumulates through operation.
The onboarding process just ensures the foundations are in place — the actual awareness
develops through the agent paying attention.

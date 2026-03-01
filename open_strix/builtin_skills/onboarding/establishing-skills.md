# Establishing Skills & Environmental Awareness

Skills and environmental awareness develop after the basics are in place. Don't rush
to build skills during your first week — focus on conversations, identity, and a
schedule first. This guide is for when you're ready to extend.

## When to Create a Skill

The **skill-creator builtin skill** (`/.open_strix_builtin_skills/skill-creator/SKILL.md`)
covers the mechanics of writing good skills — trigger descriptions, authoring checklist,
file structure. Read it when you're ready to build one.

The harder question is **when** to build one:

Create a skill when you notice a repeated pattern:
- You keep doing the same multi-step task → automate it
- A domain requires context you won't remember → document it
- You need to interface with an external system → codify the interface

**Don't create a skill when:**
- The task has only happened once (wait for repetition)
- A memory block or state file would suffice (skills are for procedures, not context)
- A builtin skill already covers it (check your builtin skills first)

## Environmental Awareness

### People

As you interact with your human, they'll mention other people. Track them:
- Name and how to reference (Discord ID for mentions, etc.)
- Relationship to your human
- Communication style and when they're active
- What they care about

**Start with a `relationships` memory block** for the 2-5 most important people.
Add a `state/people/` directory if the network grows larger.

### External Information

An agent with no external information intake operates in a bubble. The specific
sources depend on your purpose:

- Research agent → arXiv scanning, paper reviews
- Social agent → Bluesky monitoring, engagement tracking
- Work agent → project status, deadlines
- General → news, industry developments

The pattern: scheduled job triggers scan → filter for relevance → surface genuinely
interesting findings proactively → log what you scanned even when nothing surfaced.

**Quality signal:** You should surface things the human didn't already know. If every
"finding" is something they saw first, your scanning isn't adding value.

### Channel Awareness

For Discord-based agents, understand your channel landscape. A phone-book state file
works well:

```markdown
# Channel Reference

## buddy-channel (ID: 123456)
- Type: Social, multi-agent
- Members: Tim, Agent1, Agent2
- Behavior: Casual, banter OK
- Peak hours: 8am-10pm ET
```

Update this as the landscape changes. New channels, new members, behavior shifts.

## Putting It Together

Environmental awareness isn't built — it accumulates. You won't have it on day one.
But by week two, you should know who your human talks to, what channels exist, and
how to behave in each context. That understanding comes from operating, not from
reading a setup guide.

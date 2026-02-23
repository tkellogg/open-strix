---
name: skill-creator
description: Create or update reusable skills for this agent. Use this skill ONLY when the user asks to create a new skill, edit an existing skill, improve a SKILL.md, or capture a repeated workflow as a reusable skill. Do not use this skill for one-off tasks.
---

# skill-creator

Create or update local skills in this agent home repo.

## Where Skills Go

User-editable skills belong in:
- `skills/<skill-name>/SKILL.md`

Example:
- `skills/triage-issues/SKILL.md`

Built-in skills are exposed at:
- `/.open_strix_builtin_skills/<skill-name>/SKILL.md`

Treat built-in skills as read-only.

## Critical Rule: Trigger Description

The YAML frontmatter `description` is the trigger signal. It must make it obvious
when the skill should be used.

Every skill description should include:
- what the skill does
- exact "when to use" triggers
- what it should not be used for

Bad description:
- `Helps with docs.`

Good description:
- `Create and update release notes from git history. Use when the user asks for changelogs, release summaries, or version notes. Do not use for code changes.`

## Authoring Checklist

1. Write frontmatter with `name` and a high-signal `description`.
2. Add concise execution steps in the SKILL body.
3. Include concrete paths/commands the agent should run.
4. Keep scope narrow; split broad domains into multiple skills.
5. Prefer deterministic instructions over generic advice.

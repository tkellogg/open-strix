# Skills

Skills are how open-strix agents learn new capabilities. A skill is a markdown file with a YAML header — no SDK, no compilation, no registration. Drop it in `skills/` and the agent picks it up.

## How skills work

Every skill is loaded into the agent's system prompt. The YAML `description` field tells the agent *when* to use it. The markdown body tells it *how*.

```yaml
---
name: triage-issues
description: Triage incoming GitHub issues by priority and label. Use when the user asks about new issues, unreviewed PRs, or says "what's new in the repo."
---
# Steps
1. Run `gh issue list --state open --limit 20`
2. For each issue, assess priority based on...
...
```

Skills can include multiple files. The `SKILL.md` is the entry point; supporting files (reference docs, templates, scripts) live alongside it:

```
skills/my-skill/
  SKILL.md              # loaded into prompt
  reference.md          # agent reads when needed
  template.txt          # agent copies/adapts
```

Skills can also ship runtime manifests:

| File | Purpose |
|------|---------|
| `pollers.json` | Scheduled scripts that emit agent events. |
| `ui.json` | Local web UI sidecars. |
| `hooks.json` | Command hooks for prompt augmentation, pre/post tool calls, and startup/shutdown. |

See [hooks.md](hooks.md) for the hook contract.

## Three sources of skills

### 1. Local — write your own

Create `skills/<name>/SKILL.md`. The built-in **skill-creator** skill helps with this — it knows the format, the trigger description pattern, and the authoring checklist.

### 2. ClawHub — public registry

[ClawHub](https://clawhub.ai) is a public skill registry with vector search, versioning, and moderation. Agents can search and install skills at runtime using the built-in **skill-acquisition** skill:

```bash
# Search (natural language works)
npx clawhub search "manage docker containers"

# Browse trending
npx clawhub explore --sort trending

# Inspect before installing
npx clawhub inspect <slug> --file SKILL.md

# Install
npx clawhub install <slug> --workdir "$(pwd)" --dir skills
```

### 3. Skillflag — CLI-bundled skills

Any CLI tool that follows the [skillflag convention](https://agentskills.io) bundles its own agent skills:

```bash
# List skills a tool provides
acpx --skill list

# Export and install
acpx --skill export coding | npx skillflag install --dest ./skills
```

This is how tools teach agents to use them — the skill comes from the tool itself.

## Built-in skills

open-strix ships with skills that teach the agent how to operate:

| Skill | What it does |
|-------|-------------|
| **onboarding** | Guides the agent through establishing identity, goals, schedules, and skills with its human |
| **memory** | Teaches memory block maintenance, progressive disclosure, and state file hygiene |
| **skill-creator** | How to author new skills — format, trigger descriptions, scoping |
| **skill-acquisition** | Full lifecycle: discover from ClawHub/skillflag/GitHub, evaluate, install, wrap, publish |
| **prediction-review** | Calibration loops — revisit predictions against ground truth, track accuracy |
| **introspection** | Self-diagnosis from event logs — debugging, communication analysis, pattern detection |
| **pollers** | Create and manage pollers — lightweight scripts for external awareness |
| **hook-creator** | Create and manage command hooks for runtime events |

Built-in skills are read-only and synced from the open-strix package. They live in `.open_strix_builtin_skills/` (gitignored) and are refreshed on every startup.

Service-specific pollers are available from [ClawHub](https://clawhub.ai):

| Skill | What it does |
|-------|-------------|
| **bluesky-poller** | Bluesky notification poller with follow-gate trust tiers and cursor-based dedup |
| **github-poller** | GitHub repo poller for issues, PRs, comments, and reviews with self-filtering |

### Disabling builtins

Not every agent needs every builtin. Disable specific ones in `config.yaml`:

```yaml
disable_builtin_skills:
  - skill-acquisition
  - prediction-review
```

Disabled skills aren't synced to the home directory and don't appear in the prompt.

## Wrapping external skills

Skills from ClawHub or skillflag sometimes need adaptation. The pattern: create a wrapper skill that adds behavioral context around the original.

```
skills/coding/
  SKILL.md              # your wrapper — when to delegate, how to report back
  acpx-reference.md     # original tool's skill content (CLI reference)
```

The wrapper SKILL.md teaches the agent *when* and *why* to use the capability. The reference doc gives it the *how*. This separation means you can swap the underlying tool without rewriting the behavioral layer.

## The extensibility model

The design is intentional: open-strix's core handles Discord, memory, scheduling, and the agent loop. Everything domain-specific lives in skills. This means:

- **New capability = new markdown file.** No code changes, no deploys, no version bumps.
- **Agents can extend themselves.** With skill-acquisition, an agent can search ClawHub, evaluate candidates, and install skills — all without human intervention.
- **Skills compose.** skill-creator makes new skills. skill-acquisition finds existing ones. An agent with both can discover a gap, find a partial solution, and create a wrapped version tuned to its needs.
- **The core stays small.** Builtins are optional. The runtime doesn't know or care what skills are installed. Adding capabilities never means touching the framework.

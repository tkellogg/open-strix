# open-strix
[![PyPI version](https://img.shields.io/pypi/v/open-strix.svg)](https://pypi.org/project/open-strix/)

A persistent AI companion that lives in Discord or a built-in local web chat, remembers everything, and gets better over time.

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
uv run open-strix
```

Three commands. You have an agent. Open `http://localhost:8084` and start talking.

> **No Discord?** No problem. The built-in web UI works out of the box — just set `web_ui_port: 8084` in `config.yaml`. Discord is optional. See [Local Web UI](#local-web-ui-no-discord-required).

## What is this?

open-strix is an opinionated framework for building long-running AI agents. Not chatbots — *companions*. Agents that develop personality through conversation, maintain memory across sessions, schedule their own work, and learn from their mistakes.

It runs on cheap models (MiniMax M2.5, ~$0.01/message), talks to you over Discord or a small built-in web UI, and stores everything in git. No vector databases, no cloud services, no enterprise pricing. Just files, memory blocks, and a git history you can actually read.

**How you interact with it:** You talk to it on Discord or in the local web UI. It talks back using tools (`send_message`, `react`). It has scheduled jobs that fire even when you're not around. Over time, it develops interests, tracks your projects, and starts doing useful things without being asked.

## Why this exists

Most agent frameworks optimize for tool-calling pipelines or enterprise orchestration. open-strix optimizes for a different thing: **a single agent that knows you and gets better over time.**

Three design bets:

- **Focused.** Small core, everything else is skills — markdown files the agent reads and follows. Add capabilities by dropping a file in `skills/`, or let the agent discover and install them from [ClawHub](https://clawhub.ai) at runtime. MCP servers and an HTTP API handle external integrations.
- **Cheap.** Defaults to MiniMax M2.5 via the Anthropic-compatible API. Pennies per message. This is a personal tool, not an enterprise deployment. Run it on a $5/month VPS.
- **Stable.** This is the weird one. open-strix ships with built-in skills for self-diagnosis — prediction calibration loops, event introspection, onboarding that fades into regular operation. The agent can read its own logs, check whether its predictions were right, and notice when it's drifting. The design draws on cybernetics (specifically viable system theory): an agent that can't monitor and correct its own behavior will eventually degrade. So the correction loops are built in, not bolted on.

In other words, it's built to be sustainable.

## How it works

### The home repo

When you run `uvx open-strix setup`, it creates a directory — the agent's *home*. Everything the agent knows lives here:

```
blocks/          # YAML memory blocks — identity, goals, patterns. In every prompt.
state/           # Markdown files — projects, notes, research. Read on demand.
skills/          # Markdown skill files. Drop one in, agent picks it up.
logs/
  events.jsonl   # Every tool call, error, and event. The agent can read this.
  chat-history.jsonl # Append-only chat transcript across Discord, web UI, and stdin.
  journal.jsonl  # Agent's own log — what happened, what it predicted.
scheduler.yaml   # Cron jobs the agent manages itself.
config.yaml      # Model, Discord config, prompt tuning.
```

Everything except logs is committed to git after every turn. The git history *is* the audit trail. You can `git log` to see exactly what your agent did and when.

### Memory

Two layers:

- **Blocks** (`blocks/*.yaml`) — short text that appears in every prompt. Identity, communication style, current focus, relationships. The agent reads and writes these via tools.
- **Files** (`state/`) — longer content the agent reads when relevant. Research notes, project tracking, world context. Blocks point to files when depth is needed.

No embeddings, no vector search. Just files and git. The agent's memory is whatever you can `cat`.

### Skills

A skill is a markdown file in `skills/` with a YAML header. That's it. No SDK, no registration, no build step. The agent sees all skills in its prompt and invokes them by name.

```yaml
---
name: my-skill
description: What this skill does and when to use it.
---
# Instructions for the agent
...
```

open-strix ships with built-in skills that teach the agent how to operate:

| Skill | Purpose |
|-------|---------|
| **onboarding** | Walks the agent through establishing identity, goals, and schedules |
| **memory** | How to maintain and organize memory blocks and state files |
| **skill-creator** | Create new skills from repeated workflows |
| **skill-acquisition** | Discover and install skills from [ClawHub](https://clawhub.ai), [skillflag](https://agentskills.io)-compliant CLIs, and GitHub |
| **prediction-review** | Calibration loops — revisit past predictions against ground truth |
| **introspection** | Self-diagnosis from event logs and behavioral patterns |
| **pollers** | Create and manage pollers — lightweight scripts for external awareness |

The agent can also discover and install skills from the ecosystem at runtime. The built-in **skill-acquisition** skill teaches it how to search [ClawHub](https://clawhub.ai) (a public registry with 64K+ archived skills), install from skillflag-compliant CLI tools, and wrap external skills for its own use. See [docs/skills.md](docs/skills.md) for the full extensibility model.

Don't want some builtins? Disable them in `config.yaml`:

```yaml
disable_builtin_skills:
  - skill-acquisition
  - prediction-review
```

### Scheduling

The agent has tools to create, modify, and remove its own scheduled jobs. Jobs are cron expressions stored in `scheduler.yaml`. When a job fires, it sends a prompt to the agent — even if no human is around.

This is how agents develop autonomy: scheduled check-ins, maintenance routines, periodic scanning. The agent decides what to schedule based on what it learns about you.

### External Awareness (Pollers)

Pollers are lightweight scripts that watch external services on a schedule and surface actionable signals. They live inside skills as `pollers.json` files and are discovered automatically by the scheduler.

The built-in **pollers** skill teaches the agent the contract and design patterns. Service-specific pollers are available from [ClawHub](https://clawhub.ai):

```bash
npx clawhub install bluesky-poller   # Bluesky notifications with follow-gate trust tiers
npx clawhub install github-poller    # GitHub issues, PRs, comments, reviews
```

All pollers follow the same contract: run on a cron schedule, output JSONL to stdout when there's something actionable, stay silent when there isn't. Writing your own is straightforward — see the built-in **pollers** skill for the full contract and design patterns.

### Events API

Every tool call, incoming message, error, and scheduler trigger is logged to `logs/events.jsonl`. The agent can read its own event log — and the introspection skill teaches it how. This is the self-diagnosis backbone: the agent has full visibility into what it did and what went wrong.

When `api_port` is set in `config.yaml`, a loopback REST API accepts events from external scripts — Bluesky pollers, CI hooks, cross-agent communication. See [docs/events.md](docs/events.md) for the full event schema, query cookbook, and REST API reference.

### Local Web UI (no Discord required)

**Don't want to set up Discord? You don't have to.** The built-in web UI is the fastest way to start talking to your agent — no bot token, no server, no permissions fiddling. Just add two lines to `config.yaml`:

```yaml
web_ui_port: 8084
web_ui_host: 127.0.0.1
```

Start the agent and open `http://127.0.0.1:8084/` in your browser. That's it. You're chatting.

The web UI supports text, images, and file attachments. It uses the same `send_message` tool as Discord, so the agent doesn't need any special configuration — everything works the same way. Scheduled jobs, memory, skills, journal entries — all of it runs identically whether you're on Discord or the web UI.

If you want to reach the UI from your phone or another device on your network, set `web_ui_host: 0.0.0.0`.

See [SETUP.md](SETUP.md) for the full config reference.

## Growing an agent

The code is the easy part. The real work is the conversations.

A new agent starts with an `init` memory block pointing it to the onboarding skill. From there, it's supposed to have real conversations with you — not fill out forms. It learns your schedule, your projects, your communication preferences by talking to you. Over days, it drafts identity blocks, sets up scheduled jobs, and starts operating autonomously.

This takes time. Plan on a week of active conversation before the agent feels like it knows you. Plan on two weeks before it's doing useful things unprompted.

See [GROWING.md](GROWING.md) for the full guide on what this process looks like and what to expect.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
# Edit .env with your API key (ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL)
uv run open-strix
```

The setup command handles everything: directory structure, git init, GitHub repo creation (with `--github`), service files for your OS, and a walkthrough for model/Discord configuration.

> **Windows + Git Bash users:** If `uvx` fails with permission errors, use `uv run open-strix` from inside the home directory instead. See [SETUP.md § Troubleshooting](SETUP.md#troubleshooting) for details.

**Quickest path to a working agent:** Set your model API key in `.env`, set `web_ui_port: 8084` in `config.yaml`, run it, and open the browser. No Discord setup needed. Add Discord later if you want scheduled jobs to reach you when you're not at the keyboard.

See [SETUP.md](SETUP.md) for detailed instructions on environment variables, model configuration, Discord setup, and deployment options.

## Upgrading

```bash
uv add -U open-strix
```

Or with pip:

```bash
pip install -U open-strix
```

## Configuration

`config.yaml`:

```yaml
model: MiniMax-M2.5
model_max_retries: 6
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
api_port: 0
web_ui_port: 0
web_ui_host: 127.0.0.1
web_ui_channel_id: local-web
```

Models use the Anthropic-compatible API format. MiniMax M2.5 and Kimi K2.5 both work out of the box. Any model with an Anthropic-compatible endpoint will work — set `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` in `.env`.
`model_max_retries` controls provider-level retry attempts for transient failures like 5xxs and timeouts.

## Tests

```bash
uv run pytest -q
```

## Safety

Agent file writes are limited to `state/` and `skills/`. Reads use repository scope. Built-in skills are read-only. This is intentionally simple and should not be treated as a security boundary.

There is no sandboxing. Agents have full shell access. See [docs/sandboxing.md](docs/sandboxing.md) for why this is deliberate and what open-strix does instead.

## License

MIT. See `LICENSE`.

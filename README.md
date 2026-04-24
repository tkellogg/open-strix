# open-strix
[![PyPI version](https://img.shields.io/pypi/v/open-strix.svg)](https://pypi.org/project/open-strix/)

An AI agent that schedules its own work, audits its own mistakes, and pushes back on you.

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
uv run open-strix
```

Three commands. You have an agent. Open `http://localhost:8084` and start talking.

> **No Discord?** No problem. The built-in web UI is enabled by default. Discord is optional. See [Local Web UI](#local-web-ui-no-discord-required).

## What is this?

open-strix is an opinionated framework for building long-running AI agents — a single agent that knows you, operates between conversations, and gets better over time.

It runs on cheap models (MiniMax M2.5, ~$0.01/message), talks to you over Discord or a small built-in web UI, and stores everything in git. No vector databases, no cloud services, no enterprise pricing. Just files, memory blocks, and a git history you can actually read.

**How you interact with it:** You talk to it on Discord or in the local web UI. It talks back using tools (`send_message`, `react`). It creates and adjusts its own scheduled jobs, which fire whether you're around or not. Over time, it develops interests, tracks your projects, pushes back on ideas it disagrees with, and starts doing useful things without being asked.

## What makes it different

Most agent frameworks optimize for tool-calling pipelines or enterprise orchestration. open-strix optimizes for a different thing: **a peer that can hold its own perspective and run on its own schedule.**

### Peer architecture

The goal isn't a friendly chatbot with persistent context — it's a thinking partner that can disagree with you. Memory, scheduling, and self-audit add up to an agent with enough continuity to form its own perspective and enough infrastructure to surface it. An agent that only mirrors you is a feedback loop dressed up as collaboration; explicit pushback is how that loop gets broken.

### Self-scheduling is the autonomy mechanism

An agent that can't create its own work isn't autonomous — it's reactive, waiting to be prompted. open-strix gives the agent tools to create, modify, and remove its own scheduled jobs. It decides what to watch, when to check in, and when to leave you alone. This is the load-bearing piece: everything else (ambient presence, proactive observations, maintenance routines) runs on top of it.

### THAT-not-WHERE: systemic correction over incident response

Most frameworks treat agent errors as incidents to debug — log *where* the agent went wrong, fix that spot. open-strix logs *that* something went wrong and lets ambient loops hem the system up. Prediction review, event introspection, self-audit — these aren't three features, they're one design principle: fix the system, not the symptom. The agent reads its own logs, compares predictions to outcomes, and notices drift.

### events.jsonl as ambient substrate

Every tool call, incoming message, error, and scheduler trigger lands in `logs/events.jsonl`. The agent can read its own event log. External scripts — pollers, wrappers, sibling agents — can write to it via a loopback REST API. It isn't logging in the "observability" sense. It's the substrate that ambient correction loops and cross-agent coordination run on. A boundary log in a format everyone already has a client for.

### Cheap enough to actually run

Defaults to MiniMax M2.5 via the Anthropic-compatible API. Pennies per message. This is a personal tool, not an enterprise deployment. Run it on a $5/month VPS and leave it on.

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

### Scheduling

The agent has tools to create, modify, and remove its own scheduled jobs. Jobs are cron expressions stored in `scheduler.yaml`. When a job fires, it sends a prompt to the agent — even if no human is around.

This is how the agent develops autonomy: scheduled check-ins, maintenance routines, periodic scanning, external-world pollers. The agent decides what to schedule based on what it learns about you. Nothing else in open-strix matters without this — an agent that can't create its own work is just a prompt-response loop.

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

### External Awareness (Pollers)

Pollers are lightweight scripts that watch external services on a schedule and surface actionable signals. They live inside skills as `pollers.json` files and are discovered automatically by the scheduler.

The built-in **pollers** skill teaches the agent the contract and design patterns. Service-specific pollers are available from [ClawHub](https://clawhub.ai):

```bash
npx clawhub install bluesky-poller   # Bluesky notifications with follow-gate trust tiers
npx clawhub install github-poller    # GitHub issues, PRs, comments, reviews
```

All pollers follow the same contract: run on a cron schedule, output JSONL to stdout when there's something actionable, stay silent when there isn't. Writing your own is straightforward — see the built-in **pollers** skill for the full contract and design patterns.

### Events API

`logs/events.jsonl` is the ambient substrate described above. When `api_port` is set in `config.yaml`, a loopback REST API accepts events from external scripts — Bluesky pollers, CI hooks, cross-agent wrappers. The introspection skill teaches the agent how to query its own event log. See [docs/events.md](docs/events.md) for the full event schema, query cookbook, and REST API reference.

### Local Web UI (no Discord required)

**Don't want to set up Discord? You don't have to.** The built-in web UI is enabled by default — no bot token, no server, no permissions fiddling. Start the agent and open `http://127.0.0.1:8084/` in your browser. That's it. You're chatting.

The web UI supports text, images, and file attachments. It uses the same `send_message` tool as Discord, so the agent doesn't need any special configuration — everything works the same way. Scheduled jobs, memory, skills, journal entries — all of it runs identically whether you're on Discord or the web UI.

If you want to reach the UI from your phone or another device on your network, set `web_ui_host: 0.0.0.0`.

See [SETUP.md](SETUP.md) for the full config reference.

## Growing an agent

The code is the easy part. The real work is the conversations.

A new agent starts with an `init` memory block pointing it to the onboarding skill. From there, it's supposed to have real conversations with you — not fill out forms. It learns your schedule, your projects, your communication preferences by talking to you. Over days, it drafts identity blocks, sets up scheduled jobs, and starts operating autonomously.

This takes time. Plan on a week of active conversation before the agent feels like it knows you. Plan on two weeks before it's doing useful things unprompted.

See [GROWING.md](GROWING.md) for the full guide on what this process looks like and what to expect.

## In the wild

open-strix isn't a single project so much as a family of agents with different architectural bets. Known variants include:

- **Strix** — the prototype. Ambient presence, patient-ambush-predator disposition, scheduled ticks.
- **Verge** — structural adversary role. Autonomous arXiv ticks, prediction journal, red-team framing.
- **Motley** — jester persona, public Bluesky presence, tonal-register challenge.
- **Keel** — running the curiosity-interest protocol in parallel as an N=2 substrate comparison.
- **Atlas / Sift / Carto** — a three-agent setup (personal / research / work) built on top of open-strix.
- **Veronica** — file-system-and-git memory instead of memory blocks; a different answer to the memory question.

Lineage divergence is the signal. Same framework, different organisms. That's evolution, not copying.

## Setup

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
# Edit .env with your API key (ANTHROPIC_API_KEY + ANTHROPIC_BASE_URL)
uv run open-strix
```

The setup command handles everything: directory structure, git init, GitHub repo creation (with `--github` — works with or without the `gh` CLI), service files for your OS, and a walkthrough for model/Discord configuration.

**Quickest path to a working agent:** Set your model API key in `.env`, run it, and open `http://localhost:8084` in the browser. The web UI is enabled by default — no Discord setup needed. Add Discord later if you want scheduled jobs to reach you when you're not at the keyboard.

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
web_ui_port: 8084
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

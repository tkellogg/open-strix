# open-strix
[![PyPI version](https://img.shields.io/pypi/v/open-strix.svg)](https://pypi.org/project/open-strix/)

Minimal, non-production autonomous agent harness built with LangGraph Deep Agents.

## Install uv

Install `uv` first:

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Official install docs (alternate methods like Homebrew, pipx, winget):
- https://docs.astral.sh/uv/getting-started/installation/

## Quick start (recommended)

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
uvx open-strix
```

If you run `uvx open-strix` in a plain directory with no git repo, it now auto-runs setup first.

`open-strix setup` bootstraps the target directory with:

- `state/`
- `skills/`
- `blocks/`
- `logs/events.jsonl`
- `logs/journal.jsonl`
- `scheduler.yaml`
- `config.yaml`
- `checkpoint.md`
- `.env` (template)

It also prints a CLI walkthrough with links and step-by-step setup for:
- MiniMax M2.5
- Kimi/Moonshot
- Discord bot creation + permissions
- `config.yaml` values

Then `uvx open-strix` connects to Discord if a token is present (by default `DISCORD_TOKEN`).
Otherwise it runs in local stdin mode.

## Installed mode (optional)

If you prefer a local project install instead of `uvx`:

```bash
uv init --python 3.11
uv add open-strix
uv run open-strix setup --home .
uv run open-strix
```

## Install and auth `gh` (GitHub CLI)

If you want `open-strix setup --github`, install and log into `gh` first.

Install:

```bash
# macOS (Homebrew)
brew install gh

# Ubuntu / Debian
sudo apt install gh
```

```powershell
# Windows (winget)
winget install --id GitHub.cli
```

Authenticate:

```bash
gh auth login
gh auth status
```

Official docs:
- https://cli.github.com/
- https://github.com/cli/cli#installation

## Create a GitHub repo and set remote

`open-strix` auto-syncs with git after each turn, so set up a repo + remote early.

Recommended:

```bash
uvx open-strix setup --home my-agent --github
```

Keep this private, since agent memory and logs can contain sensitive context.

Manual fallback with GitHub CLI (`gh`):

```bash
cd my-agent
gh auth login
gh repo create <repo-name> --private --source=. --remote=origin
git add .
git commit -m "Initial commit"
git push -u origin HEAD
```

Manual fallback with GitHub web UI:

1. Create a new **private** empty repo on GitHub (no README, no `.gitignore`, no license).
2. In your project directory:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin git@github.com:<your-user>/<repo-name>.git
git push -u origin main
```

If you prefer HTTPS:

```bash
git remote add origin https://github.com/<your-user>/<repo-name>.git
```

Check remote config:

```bash
git remote -v
```

## Environment setup

Start from the example env file:

```bash
cp .env.example .env
```

Default model setup in this project expects an Anthropic-compatible endpoint:

- `ANTHROPIC_API_KEY`
- `ANTHROPIC_BASE_URL`

Discord runtime uses:

- `DISCORD_TOKEN`

Optional:

- `DISCORD_TEST_CHANNEL_ID`
- `OPEN_STRIX_TEST_MODEL`

## Models

### Default: MiniMax M2.5

This project defaults to:

- `model: MiniMax-M2.5` in `config.yaml`
- provider prefix `anthropic:` internally (so the runtime uses `anthropic:MiniMax-M2.5`)

Use MiniMax's Anthropic-compatible endpoint in your `.env`:

- `ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic`

MiniMax docs:

- Anthropic compatibility + model IDs: https://platform.minimax.io/docs/api-reference/text-anthropic-api
- AI coding tools guide (M2.5 context): https://platform.minimax.io/docs/guides/text-ai-coding-tools

### Alternative: Kimi K2.5

If you want Kimi instead of MiniMax:

1. Point Anthropic-compatible env vars at your Moonshot endpoint (see Moonshot docs for current endpoint details).
2. Set `model` in `config.yaml` to the current Kimi model ID you want.

Moonshot docs:

- Docs overview: https://platform.moonshot.ai/docs/overview
- K2 update post (links to current quick-start): https://platform.moonshot.ai/blog/posts/Kimi_API_Newsletter

Note: the Moonshot update posted on November 8, 2025 references `kimi-k2-thinking` and `kimi-k2-thinking-turbo`. If you refer to these as "K2.5", use the exact current model IDs from Moonshot docs/console.

### Model config behavior

`config.yaml` key:

- `model`

Behavior:

- If `model` has no `:` (example `MiniMax-M2.5`), open-strix treats it as Anthropic-provider and uses `anthropic:<model>`.
- If `model` already includes `provider:model` (example `openai:gpt-4o-mini`), it is passed through unchanged.

## Discord setup

Use Discord's Developer Portal web UI:

1. Go to https://discord.com/developers/applications and create a new application.
2. Open your app, then go to the `Bot` tab.
3. Under `Token`, generate/reset token and copy it (you won't be able to re-view it later).
4. In the same `Bot` tab, enable `Message Content Intent` (required for open-strix message handling).
5. Go to `Installation`.
6. Under `Installation Contexts`, enable `Guild Install`.
7. Under `Install Link`, pick `Discord Provided Link`.
8. Under `Default Install Settings`:
   - `Guild Install` scopes: select `bot` (and `applications.commands` if you plan to add slash commands).
   - `Permissions`: for this bot, a practical baseline is:
     - `View Channels`
     - `Send Messages`
     - `Send Messages in Threads`
     - `Read Message History`
     - `Add Reactions`
9. Copy the generated install link, open it in your browser, pick your server, and authorize.

Reference docs for the same flow:

- Getting started (app creation + installation flow): https://docs.discord.com/developers/quick-start/getting-started
- OAuth2 scopes/install links: https://docs.discord.com/developers/topics/oauth2
- Permissions reference: https://docs.discord.com/developers/topics/permissions
- Gateway + intents reference: https://docs.discord.com/developers/events/gateway

Where this is configured in open-strix:

- Token env var name: `config.yaml` -> `discord_token_env` (default `DISCORD_TOKEN`)
- Actual token value: your `.env`
- Bot allowlist behavior: `config.yaml` -> `always_respond_bot_ids`

## `config.yaml` tour

Default:

```yaml
model: MiniMax-M2.5
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
```

Key meanings:

- `model`: model name (or `provider:model`)
- `journal_entries_in_prompt`: how many journal entries go into each prompt
- `discord_messages_in_prompt`: how many recent Discord messages go into each prompt
- `discord_token_env`: env var name to read Discord token from
- `always_respond_bot_ids`: bot author IDs the agent is allowed to respond to

Related files:

- `scheduler.yaml`: cron/time-of-day jobs
- `blocks/*.yaml`: memory blocks surfaced in prompt context
- `checkpoint.md`: returned by `journal` tool after a journal write
- `skills/`: user-editable local skills
- `/.open_strix_builtin_skills/skill-creator/SKILL.md`: packaged built-in skill source mounted as read-only

Runtime behavior note:

- Git sync (`git add -A` -> commit -> push) runs automatically after each processed turn.

## Personality bootstrap

Creating an agent is less about code, and a whole lot more about the time you spend talking to it.
[Lily Luo](https://www.appliedaiformops.com/p/what-building-a-persistent-ai-agent) has a great post on
forming agent personalities.

You should plan on spending time:

* Communication patterns — correct the agent to know when and how often it should use the `send_message` and `react` tools. Agents often initially find it surprising that their final message is ignored, so they need to use their tools instead.
* Talk about things you're interested in, see what the agent becomes interested in

## Tests

```bash
uv run pytest -q
```

Discord coverage includes:
- unit tests with mocked boundaries in `tests/test_discord.py`
- live integration tests against real Discord in `tests/test_discord_live.py`

Live test env vars:
- `DISCORD_TOKEN` (required for live connect test)
- `DISCORD_TEST_CHANNEL_ID` (optional; enables live send-message test)

## Safety baseline

- Agent file writes/edits are blocked outside `state/`.
- Reads still use repository scope.
- This is intentionally simple and should not be treated as production-ready.

## License

MIT. See `LICENSE`.

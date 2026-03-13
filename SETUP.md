# Setup Guide

Detailed setup instructions for open-strix. For an overview of what open-strix is, see [README.md](README.md).

## Prerequisites

### Install uv

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Official install docs (Homebrew, pipx, winget, etc.): https://docs.astral.sh/uv/getting-started/installation/

### Install and auth `gh` (optional)

If you want `open-strix setup --github` to create a GitHub repo automatically:

```bash
# macOS
brew install gh

# Ubuntu / Debian
sudo apt install gh
```

```powershell
# Windows
winget install --id GitHub.cli
```

Then authenticate:

```bash
gh auth login
gh auth status
```

Official docs: https://cli.github.com/

## Quick start

```bash
uvx open-strix setup --home my-agent --github
cd my-agent
uv run open-strix
```

`open-strix setup` bootstraps the target directory with:

- `state/`, `skills/`, `blocks/` — agent workspace directories
- `logs/events.jsonl`, `logs/chat-history.jsonl`, `logs/journal.jsonl` — event, chat transcript, and journal logs
- `scheduler.yaml` — scheduled job definitions
- `config.yaml` — model and runtime configuration
- `checkpoint.md` — post-journal reflection prompt
- `.env` — template for secrets
- `pyproject.toml`, `uv.lock` — Python dependencies

It also:
- Runs `uv init` and `uv add open-strix`
- Checks git identity (prompts for `user.name` and `user.email` if missing)
- Checks git remote (prompts for remote URL if `origin` is missing)
- Detects OS and generates service files in `services/`:
  - Linux: `open-strix.service` (systemd user unit)
  - macOS: `ai.open-strix.<name>.plist` (launchd agent)
  - Windows: Task Scheduler install/uninstall PowerShell scripts
- Prints a CLI walkthrough with links for model and Discord setup

### Installed mode (alternative)

If you prefer a local project install instead of `uvx`:

```bash
uv init --python 3.11
uv add open-strix
uv run open-strix setup --home .
uv run open-strix
```

## GitHub repo setup

open-strix auto-syncs with git after each turn, so set up a repo + remote early. Keep it **private** — agent memory and logs can contain sensitive context.

**Recommended:**

```bash
uvx open-strix setup --home my-agent --github
```

**Manual fallback (GitHub CLI):**

```bash
cd my-agent
gh repo create <repo-name> --private --source=. --remote=origin
git add .
git commit -m "Initial commit"
git push -u origin HEAD
```

**Manual fallback (web UI):**

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

HTTPS alternative: `git remote add origin https://github.com/<your-user>/<repo-name>.git`

## Environment variables

Start from the template:

```bash
cp .env.example .env
```

**Required:**

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | API key for the model provider |
| `ANTHROPIC_BASE_URL` | Endpoint URL (e.g., `https://api.minimax.io/anthropic`) |
| `DISCORD_TOKEN` | Discord bot token |

**Optional:**

| Variable | Purpose |
|---|---|
| `DISCORD_TEST_CHANNEL_ID` | Enables live send-message tests |
| `OPEN_STRIX_TEST_MODEL` | Model override for tests |

## Model configuration

### Default: MiniMax M2.5

```yaml
# config.yaml
model: MiniMax-M2.5
```

```bash
# .env
ANTHROPIC_BASE_URL=https://api.minimax.io/anthropic
ANTHROPIC_API_KEY=your-key-here
```

MiniMax docs:
- Anthropic compatibility + model IDs: https://platform.minimax.io/docs/api-reference/text-anthropic-api
- AI coding tools guide: https://platform.minimax.io/docs/guides/text-ai-coding-tools

### Alternative: Kimi K2.5

```bash
# .env
ANTHROPIC_BASE_URL=https://api.moonshot.ai/anthropic
```

Set `model` in `config.yaml` to the current Kimi model ID.

Moonshot docs:
- Overview: https://platform.moonshot.ai/docs/overview
- K2 update: https://platform.moonshot.ai/blog/posts/Kimi_API_Newsletter

### Model config behavior

- If `model` has no `:` (e.g., `MiniMax-M2.5`), open-strix treats it as Anthropic-provider: `anthropic:MiniMax-M2.5`
- If `model` includes `provider:model` (e.g., `openai:gpt-4o-mini`), it passes through unchanged

Any model with an Anthropic-compatible API works. Just set `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY`.

## Choosing an interface

open-strix supports two interfaces. You can use either or both.

| | Web UI | Discord |
|---|---|---|
| **Setup time** | ~30 seconds | ~15 minutes |
| **Requires** | Just `config.yaml` | Bot token + server + permissions |
| **Best for** | Getting started, local dev, 1:1 chat | Notifications, mobile access, multi-channel, scheduled job alerts |
| **Limitations** | Browser only, no push notifications | Requires Discord account and server setup |

**Recommendation:** Start with the web UI. It's the fastest way to begin growing your agent. Add Discord later when you want the agent to reach you proactively (scheduled jobs, reminders, etc.).

## Web UI setup

Add to `config.yaml`:

```yaml
web_ui_port: 8084
```

Run `uv run open-strix` and open `http://127.0.0.1:8084/`. Done.

The web UI supports text messages, image display, file attachments (drag, paste, or pick), and emoji reactions. It uses the same tools and memory as Discord — switching between them later doesn't lose anything.

**Access from other devices** (phone, tablet, another machine on your network):

```yaml
web_ui_port: 8084
web_ui_host: 0.0.0.0
```

Then open `http://<your-machine-ip>:8084/` from the other device.

**Full config options:**

| Key | Default | Purpose |
|---|---|---|
| `web_ui_port` | `0` (disabled) | Port number. Set to any open port to enable. |
| `web_ui_host` | `127.0.0.1` | Bind address. `0.0.0.0` for network access. |
| `web_ui_channel_id` | `local-web` | Synthetic channel ID for web messages. |

## Discord setup

Optional. Skip this entirely if you're using the web UI.

Use Discord's [Developer Portal](https://discord.com/developers/applications):

1. **General Information:** Set app/bot name and basic metadata.
2. **Installation:** Set `Install Link` to `None`, then save.
3. **OAuth2 → URL Generator:**
   - Check `bot`
   - Select permissions: `View Channels`, `Send Messages`, `Send Messages in Threads`, `Read Message History`, `Add Reactions`, `Attach Files`
4. **Bot tab:**
   - Disable `Public Bot`
   - Enable `Message Content Intent`
5. **Bot tab → Reset Token:**
   - Copy token immediately (it won't be shown again)
   - Set in `.env`: `DISCORD_TOKEN=<your_discord_bot_token>`
6. Use the generated OAuth2 bot invite URL to add the bot to your server.

Reference docs:
- [Getting started](https://docs.discord.com/developers/quick-start/getting-started)
- [OAuth2](https://docs.discord.com/developers/topics/oauth2)
- [Permissions](https://docs.discord.com/developers/topics/permissions)
- [Gateway + intents](https://docs.discord.com/developers/events/gateway)

Where this is configured in open-strix:
- Token env var name: `config.yaml` → `discord_token_env` (default `DISCORD_TOKEN`)
- Bot allowlist: `config.yaml` → `always_respond_bot_ids`

## `config.yaml` reference

```yaml
model: MiniMax-M2.5
journal_entries_in_prompt: 90
discord_messages_in_prompt: 10
discord_token_env: DISCORD_TOKEN
always_respond_bot_ids: []
api_port: 0
web_ui_port: 0
web_ui_host: 127.0.0.1
web_ui_channel_id: local-web
folders:
  state: rw
  skills: rw
  blocks: ro
  scripts: ro
  logs: ro
```

| Key | Purpose |
|---|---|
| `model` | Model name or `provider:model` |
| `journal_entries_in_prompt` | Journal entries included in each prompt |
| `discord_messages_in_prompt` | Recent Discord messages in each prompt |
| `discord_token_env` | Env var name for Discord token |
| `always_respond_bot_ids` | Bot author IDs the agent responds to |
| `api_port` | Loopback REST API port (`0` disables it) |
| `web_ui_port` | Local web chat port (`0` disables it) |
| `web_ui_host` | Bind host for the web UI (default `127.0.0.1`) |
| `web_ui_channel_id` | Synthetic channel ID used by the built-in web chat |
| `folders` | Map of folder names to access mode (`rw` or `ro`) |
| `reflection` | Async self-review config (see below) |
| `mcp_servers` | List of MCP server configs (see below) |

### Folders

The `folders` key controls which directories the agent can see and whether it can write to them. Each entry maps a folder name to an access mode:

- `rw` — read-write (agent can read and modify files)
- `ro` — read-only (agent can read but not modify files)

Folders are created automatically on startup. Add custom folders to give your agent access to additional directories:

```yaml
folders:
  state: rw
  skills: rw
  scripts: ro
  logs: ro
  research: ro       # custom read-only folder
  data: rw           # custom read-write folder
```

#### External directories

Folder paths can be relative to the agent's home directory. Use `../` to give an agent read-only access to a sibling directory:

```yaml
folders:
  state: rw
  skills: rw
  scripts: ro
  logs: ro
  "../cybernetics-research": ro   # sibling directory, read-only
```

If the agent lives at `~/jester/`, this resolves to `~/cybernetics-research/`. The agent can read files in that directory but can't modify them.

This is useful for giving an agent access to shared resources — research repos, documentation, datasets — without copying them into the agent's home directory. The directory is created on startup if it doesn't exist.

### Reflection

Async self-review after each `send_message`. When enabled, the agent's own model evaluates outgoing messages against criteria defined in a markdown file. If dissonance is detected, a 🪞 reaction is added to the sent message.

```yaml
reflection:
  enabled: false
  questions_file: state/is-dissonant-prompt.md
```

| Key | Purpose |
|---|---|
| `enabled` | `true` to activate reflection, `false` to disable (default: `false`) |
| `questions_file` | Path to the dissonance criteria file, relative to agent home |

The questions file is a markdown document defining what patterns to look for. A default is created on first boot at the configured path. The agent can edit this file to refine its own self-monitoring criteria over time.

See the **dissonance** builtin skill for details on how it works and how to tune it.

### MCP Servers

Add [MCP](https://modelcontextprotocol.io/) servers to give your agent access to external tools. Servers run as subprocesses and their tools appear alongside built-in tools.

```yaml
mcp_servers:
  - name: brave-search
    command: npx
    args: ["-y", "@anthropic/mcp-server-brave-search"]
    env:
      BRAVE_API_KEY: "${BRAVE_API_KEY}"
  - name: github
    command: npx
    args: ["-y", "@anthropic/mcp-server-github"]
    env:
      GITHUB_TOKEN: "${GITHUB_TOKEN}"
```

Each server entry requires:
- `name` — unique identifier (used to namespace tools as `mcp_<name>_<tool>`)
- `command` — executable to run (e.g., `npx`, `python`, `node`)
- `args` — command arguments
- `env` — (optional) environment variables; `${VAR}` references are expanded from the process environment

Servers start on app launch. If a server fails to start, it's skipped — other servers and the rest of the app continue normally. Works with any model (MiniMax, Kimi, Claude, etc.).

Related files:
- `scheduler.yaml` — cron/time-of-day jobs
- `blocks/*.yaml` — memory blocks in prompt context
- `checkpoint.md` — post-journal reflection prompt
- `skills/` — user-editable local skills

Runtime behavior:
- Git sync (`git add -A` → commit → push) runs automatically after each processed turn.
- New agent homes include a twice-daily prediction-review job (09:00 and 21:00 UTC).

## Tests

```bash
uv run pytest -q
```

Discord test coverage:
- Unit tests with mocked boundaries: `tests/test_discord.py`
- Live integration tests: `tests/test_discord_live.py`

Live test env vars:
- `DISCORD_TOKEN` (required for live connect test)
- `DISCORD_TEST_CHANNEL_ID` (optional; enables live send-message test)

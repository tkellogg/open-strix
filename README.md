# open-strix

Minimal, non-production autonomous agent harness built with LangGraph Deep Agents.

## Run

```bash
uv init --python 3.11
uv add open-strix
uv run open-strix
```

On first run, it bootstraps the current directory with:

- `state/`
- `skills/`
- `blocks/`
- `logs/events.jsonl`
- `logs/journal.jsonl`
- `scheduler.yaml`
- `config.yaml`
- `checkpoint.md`

If `DISCORD_TOKEN` is set (or whatever `config.yaml` points to), it connects to Discord.
Otherwise it runs in local stdin mode.

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

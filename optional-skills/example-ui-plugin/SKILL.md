# Example UI Plugin

This skill is a reference implementation for open-strix `ui.json` plugins.

It starts a tiny local aiohttp server and lets the harness reverse-proxy it at
`/ui/example-clock/`.

The harness chooses a free port and passes it as `OPEN_STRIX_PORT`.

It also passes `STATE_DIR` as this skill directory and `UI_NAME` as the declared
UI name.

The page shows a live clock and calls `api/time` from inside the iframe, proving
same-origin fetches work through the harness proxy.

# UI plugins — the contract

UI plugins extend the local web UI with **persistent, interactive side panels** that survive across turns. They are the answer to *"I want to see this, not just discuss it."* This document describes how they work end-to-end so you can build, debug, and reason about one.

If you only need to send a one-off rich artifact and not run a server, you want an HTML message — see `SKILL.md`. This file is about the plugins themselves.

---

# The shape of a plugin

A UI plugin is a directory under a skill source that contains:

```
my-skill/
├── ui.json          # plugin manifest (this turns the skill into a plugin host)
├── server.py        # (or any other executable) — speaks HTTP on $OPEN_STRIX_PORT
└── SKILL.md         # standard skill description (what the plugin is for)
```

The presence of `ui.json` is the trigger. The harness scans every loaded skill directory for `ui.json` files at startup.

## `ui.json` schema

```json
{
  "uis": [
    {
      "name": "chainlink",
      "command": "python server.py"
    }
  ]
}
```

| Field | Required | Notes |
| --- | --- | --- |
| `name` | yes | URL-safe slug, `[a-z0-9](?:[a-z0-9-]*[a-z0-9])?`. Becomes the mount point `/ui/<name>/` and the widget title. Must be globally unique across plugins. |
| `command` | yes | Shell command run inside the skill directory. Use whatever runtime you want — `python server.py`, `node index.js`, `./bin/serve`. |

A single skill can declare multiple plugins by putting more than one entry in the `uis` array.

## The server contract

Your `command` is spawned as a child process by the harness. It must:

1. **Listen on `$OPEN_STRIX_PORT`** (provided by the harness — a free port picked at boot, may change across restarts). Do not hard-code a port.
2. **Bind to `127.0.0.1`.** External binds are rejected by the reverse proxy.
3. **Serve relative paths**, not absolute paths into `/ui/<name>/`. The harness strips the `/ui/<name>/` prefix before forwarding. From inside your server, root is `/`.
4. **Treat `$STATE_DIR` as your skill's own directory** (where `ui.json` lives). If you need shared state (e.g. an issue DB elsewhere on disk), walk up from there to find it — don't assume working directory.
5. **Respond to `GET /` with your default view.** This is what the widget loads when it first mounts.
6. **Be idempotent on restart.** The harness restarts you on crash (see lifecycle below). Don't accumulate state in memory you can't rebuild.

Env vars passed by the harness:

| Var | Meaning |
| --- | --- |
| `OPEN_STRIX_PORT` | Free port you must bind to. |
| `STATE_DIR` | Absolute path to the directory holding `ui.json` (your skill dir). |
| `UI_NAME` | The declared name. Useful for logging. |

## Reverse proxy

The harness mounts each plugin at `/ui/<name>/*` and proxies everything — including websockets, streams, and POSTs — to `127.0.0.1:<plugin-port>`. From the user's browser, the plugin is same-origin with the chat. This means:

- `fetch("/api/whatever")` from inside the iframe just works.
- Cookies set by the plugin are scoped to the chat origin (be careful with names).
- The chat's CSRF / auth context is **not** automatically forwarded — design your plugin as read-only or use its own auth if it mutates anything important.

---

# Lifecycle

```
discover (scan ui.json)
  → spawn (assign port, set env, exec command)
      → state = "starting"
          → first successful proxy round-trip → state = "running"
          → process exits cleanly within FAST_EXIT_SECONDS → state = "dead"
          → process exits cleanly after FAST_EXIT_SECONDS → state = "starting" (restart)
          → process crashes → state = "starting" (restart with backoff)
  → throttle: max MAX_RESTARTS_PER_WINDOW restarts in RESTART_WINDOW_SECONDS, else state = "dead"
```

See `open_strix/ui_plugins.py` for the authoritative values. As of this writing: 3 restarts per 60s, 50ms–500ms backoff.

States are surfaced to the chat UI:

- **starting** — placeholder card with "is starting" text.
- **running** — iframe is live, showing your `/` route.
- **dead** — placeholder with "is not available" text. User can reload via the widget's `⟳` button, which calls `iframe.src = iframe.src`.

---

# The widget

Each running plugin gets a **card** in the right-hand UI strip:

- **Reload (`⟳`)** — re-assigns `iframe.src` to force a refetch.
- **Minimize (`−`)** — collapses the body but keeps the iframe alive in memory.
- **Maximize (`⛶`)** — opens a modal that owns the iframe full-screen. Closing the modal re-parents the iframe back to its card slot.
- **Card-vertical fill** — the card is currently sized at `height: 300px` (`.ui-frame-slot { height: 260px }` + chrome). If you need taller, maximize.

The widget is **persistent** — Tim sees the same iframe across multiple turns. State inside the iframe (scroll position, form input) is preserved until reload.

---

# Linking from chat

This is the killer feature. The agent can write a link in a chat message that navigates the *plugin widget* rather than opening a new tab. See `SKILL.md` for the user-facing rules. From the plugin server's perspective:

- A click on a link `/ui/<name>/<path>` (markdown) or `#/ui/<name>/<path>` (HTML message hash form) → the harness sets your iframe's `src` to `<path>` → your server receives a normal `GET <path>` → render that route.
- Make sure **every deep-link route handles direct navigation**, not just clicks-from-the-default-view. If a user lands on `/issue/567` cold, your server must render the detail page without depending on prior state.

The chainlink plugin does this correctly: `/issue/<id>` is a top-level GET route that hits the SQLite DB and renders.

---

# Security model (current, v1)

| Concern | Behavior |
| --- | --- |
| Network | Plugins bind to `127.0.0.1` only. The harness reverse-proxies. Plugins cannot be reached from outside the host. |
| Sandbox | The plugin iframe is `sandbox="allow-scripts allow-same-origin allow-forms"`. Scripts run. Same-origin with the chat. |
| Auth | None enforced by the harness. The chat is single-user (local) so the plugin trusts the request. **Do not run plugins that mutate sensitive state without your own auth.** |
| File access | The plugin process inherits the harness's filesystem permissions. Treat plugins as fully trusted code — they can read anything you can. |

If you're considering a plugin that accepts user-provided code or talks to an external API with credentials, escalate the design before shipping.

---

# Designing well

A few rules of thumb that have held up so far:

1. **One plugin = one frame of mind.** Don't cram a chainlink browser, a log viewer, and a calendar into one plugin. Three plugins.
2. **Read-only first.** Get the rendering right before adding mutations. Most "look at the state" plugins never need writes.
3. **No surprises in `/`**. The default route should be browsable without arguments. Save filters and detail views for explicit routes.
4. **Don't poll faster than you'd refresh by hand.** Most UIs want a 2-5s polling interval at most. The chat polls every second; your plugin doesn't need to be faster.
5. **Embrace the cream background or paint your own.** Same rule as HTML messages. The default body has no background — what's behind the iframe is white-ish chat content; pick a deliberate palette.
6. **Link back to chat actions.** If a row has a "this needs a Tim decision" affordance, an inline link to draft a chat message keeps the conversation and the UI aligned.

---

# Example: chainlink

The chainlink plugin is the canonical example as of 2026-05-12. It:

- Lives in the herder repo at `herder/skills/chainlink-ui/`.
- Declares `name: "chainlink"`, `command: "python server.py"`.
- Serves `/` (issue list with status filters) and `/issue/<id>` (detail).
- Reads `.chainlink/issues.db` (a SQLite file) — walks up from `$STATE_DIR` to find it.
- Is read-only.

It pairs with chat links like `/ui/chainlink/issue/567`, which open the issue detail directly in the widget. That single affordance is what made Tim say "now I can see them."

---

# Where to read code

- `open_strix/ui_plugins.py` — discovery, port assignment, lifecycle, supervision.
- `open_strix/web_ui.py`, search for `renderUiPlugins`, `createUiWidget`, `ensureUiIframe`, `applyUiWidgetState`, `routeUiPluginNav`, `attachUiPluginLinkInterceptor` — the client side.
- `optional-skills/example-ui-plugin/` — a minimal plugin you can copy as a starting point.

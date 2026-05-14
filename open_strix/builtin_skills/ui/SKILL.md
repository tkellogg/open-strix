---
name: ui
description: How to choose between markdown, HTML messages, and UI plugins in the local web chat — and how to render each well. Use whenever you are about to call `send_message` to a web-chat channel, especially when the content has structure (rows, columns, statuses, a state machine, multiple linked things) or when the right answer is "let me show you", not "let me tell you". Do not use for Discord channels — HTML is rejected there.
---

# Choosing the right surface

There are **three** surfaces in the local web UI. Pick by asking *what shape the content has*, not *how long it is*.

| Surface | When | What it gives you | What it costs |
| --- | --- | --- | --- |
| **Markdown message** | Quick reply, single point, conversational. | Cheap, scrollable with the chat. | Linear — every reply pushes older ones up. |
| **HTML message** | One-off rich artifact: a table, a status card, a small dashboard, a diagram, a labelled diff. The shape is non-linear but the lifetime is "until the next message scrolls it away." | Tables, grids, layered SVG, color/typography, and small scripts under your control. Renders inline in chat. Can use parent-owned `data-strix-action` hooks or `window.strix`. | Ephemeral; sandboxed with no same-origin access. Cream background — see below. |
| **UI plugin** | A *frame of mind* that is not linear like chat. Persistent. Tim wants to see what we're talking about, not just discuss it. Example: chainlink issues — the chat thread talks *about* them, the plugin *shows* them, with detail views, filters, status. | A live, interactive, scrollable side panel that survives across turns. Full JS, can poll, can show fresh state. | Costs a server. Has a contract: see `ui-plugins.md`. |

**Mental model (from Tim, 2026-05-12):**

> "I want UI plugins to share a frame of mind that's not linear like chat. With chainlink, I couldn't see any of the chainlink issues — now I can."

If you find yourself describing state in prose ("here are the 7 open RLS findings, here are their statuses, here's which ones map to PR #X..."), you're papering over a missing UI plugin. Either fire off an HTML message *now* and propose the plugin, or just build the plugin.

---

# Rule of thumb (when in doubt)

1. **Quick reply, no shape?** → markdown.
2. **One artifact you wouldn't want to scroll back to find?** → HTML message. Make it dense and self-contained.
3. **The user is going to want to see this same thing again tomorrow, with fresh data?** → UI plugin. Read `ui-plugins.md`.

Bias toward richer surfaces, not less. A markdown wall-of-bullets where an HTML table would do is a missed affordance.

---

# Rendering HTML messages

HTML messages render inside a sandboxed iframe in the local web UI. They will be *rejected* by Discord — for Discord, fall back to markdown.

## The cream background

**The chat surface is light (warm cream).** The agent message bubble background is approximately `rgba(255, 250, 241, 0.84)` over a `#efe4cf → #f7f2e7` page gradient. The iframe itself is **transparent** — your HTML renders directly on top of that cream/off-white surface.

Two safe strategies:

1. **Inherit the cream.** Use dark text (e.g. `#1a1a2e`, `#222`, dark grays) and leave the body background unset. Lightweight, blends with the chat.
2. **Paint your own opaque background.** Set `html, body { background: <your color>; }` full-bleed (not just on a card) so contrast is fully under your control. Use this for dashboards, dark-mode mock-ups, or anything image-y.

**Do not:** assume a dark canvas. Light text on the default cream is unreadable. If you catch yourself writing `color: white` or `color: #eee` on the default surface, you have a bug.

## Script sandbox

HTML messages use `sandbox="allow-scripts allow-forms"` — scripts and forms work, but the frame has an opaque origin. Do **not** assume same-origin access. The reply cannot read the parent DOM, and the parent cannot inspect the reply DOM after load.

For parent-owned interactions, use the HTML action API in `html-actions.md`. The harness injects a tiny bridge that lets links, buttons, forms, and scripts ask the parent app to do controlled things like navigate a widget, send a chat message, or continue from the current HTML reply's agent context.

If the interaction should persist across turns or act as a reusable app surface, build a UI plugin.

## Sizing

The harness injects a `ResizeObserver` bridge that posts height updates to the parent. Do *not* try to fix a height. Width is capped at `min(42rem, 92%)`. If your script changes layout in an unusual way, call `window.strix.resize()`.

---

# Linking to UI plugins from chat messages

When a UI plugin is running, you can link to its internal routes from chat. Clicking the link **navigates the plugin's widget**, instead of opening a new tab. This is the bridge between chat (the conversation) and the plugin (the frame of mind).

For explicit buttons/forms and the JavaScript `postMessage` bridge, read `html-actions.md`. Plain links remain the simplest option when all you need is widget navigation.

## URL shapes

| From | href format | Mechanism |
| --- | --- | --- |
| Markdown message | `/ui/<plugin>/<path>` | Delegated click handler on the chat container. Parent intercepts and sets the plugin iframe's `src`. |
| HTML message (sandboxed, opaque origin) | Prefer `/ui/<plugin>/<path>`; the injected bridge routes it through the parent. Explicit buttons can use `data-strix-action="widget.navigate"`. `#/ui/<plugin>/<path>` with `target="_top"` is a legacy compatibility escape hatch only. | Injected bridge + parent-owned `postMessage` handling; optional hash routing. |

`<plugin>` must match the `name` field in the plugin's `ui.json`. `<path>` is whatever route the plugin's own server serves.

## Examples

```markdown
See [chainlink #567](/ui/chainlink/issue/567) for the detail.
```

```html
<a href="/ui/chainlink/issue/567">chainlink #567</a>
<!-- legacy compatibility form; prefer the direct /ui/... href above: -->
<a href="#/ui/chainlink/issue/567" target="_top">chainlink #567 (hash)</a>
<!-- explicit action form, useful for buttons or plugin-local paths: -->
<button type="button" data-strix-action="widget.navigate" data-strix-widget="chainlink" data-strix-path="/issue/567">chainlink #567</button>
```

When the link is clicked:

- If the plugin is **running**, the widget un-minimizes, scrolls into view, and navigates to `/<path>`.
- If the plugin is **known but not yet running** (still booting), the click is *claimed* (no new tab opens) but nothing else happens. The user can re-click after it boots.
- If the plugin is **unknown**, the click falls through to normal browser behavior (which, for `/ui/<plugin>/...`, means a 404 on a new tab — usually a sign you should remove the link).

## When to add a plugin link

Anywhere a piece of state has a "canonical view" in a UI plugin, link to it instead of pasting its contents. A chainlink issue ID → a `/ui/chainlink/issue/<id>` link. A trace ID → `/ui/traces/<id>`. The link is cheap; the plugin already knows how to render the detail well.

This is the highest-leverage move enabled by the plugin system. Use it often.

---

# Cross-references

- `ui-plugins.md` — how to design, ship, and reason about UI plugins themselves (the plugin contract, lifecycle, port assignment, `ui.json` schema, security model).
- `html-actions.md` — the parent-owned HTML action API and the equivalent `postMessage` bridge for trusted scripted frames.
- `memory` skill — for deciding what state belongs in a plugin's data store vs. a memory block vs. a state file.

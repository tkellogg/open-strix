---
name: ui
description: How to choose between markdown, HTML messages, and UI plugins in the local web chat — and how to render each well. Use whenever you are about to call `send_message` to a web-chat channel, especially when the content has structure (rows, columns, statuses, a state machine, multiple linked things) or when the right answer is "let me show you", not "let me tell you". Do not use for Discord channels — HTML is rejected there.
---

# Choosing the right surface

There are **three** surfaces in the local web UI. Pick by asking *what shape the content has*, not *how long it is*.

| Surface | When | What it gives you | What it costs |
| --- | --- | --- | --- |
| **Markdown message** | Quick reply, single point, conversational. | Cheap, scrollable with the chat. | Linear — every reply pushes older ones up. |
| **HTML message** | One-off rich artifact: a table, a status card, a small dashboard, a diagram, a labelled diff. The shape is non-linear but the lifetime is "until the next message scrolls it away." | Tables, grids, layered SVG, color/typography under your control. Renders inline in chat. | Static (no scripts), no live data. Cream background — see below. |
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

## No scripts

The iframe sandbox is `allow-same-origin` only — **no `allow-scripts`**. Anything that needs JS won't run. Use static HTML, CSS, and inline SVG. If you need interactivity, you need a UI plugin.

## Embedding images

HTML messages render via `srcdoc` — there is **no base URL**, so relative paths like `img/foo.png` won't resolve. The harness serves attachments at `/files/{path}`, gated by the message's `attachment_paths` list (see `resolve_web_shared_file`). There is **no `/state/` route** — `<img src="/state/foo.png">` will 404 silently and render as a broken image.

**Two things must both be true** for an inline image to render:

1. The file is passed in `attachment_paths`, e.g. `attachment_paths=["state/charts/run-42.png"]`.
2. The `<img src>` points to `/files/<same-path>`, e.g. `<img src="/files/state/charts/run-42.png">`.

Working example:

```python
send_message(
    format="html",
    text='<p>Latency over the last hour:</p><img src="/files/state/charts/run-42.png" style="max-width:100%">',
    attachment_paths=["state/charts/run-42.png"],
)
```

**Allowed `src` shapes:**

| Shape | Notes |
| --- | --- |
| `/files/<path>` where `<path>` is in `attachment_paths` | The standard case. Path is relative to agent home. |
| Inline `<svg>...</svg>` | Always works, no attachment needed. Preferred for diagrams. |
| `data:image/...;base64,...` | Works, but bloats message storage. Use for tiny icons only. |
| `https://<external>` | Works (iframe is `allow-same-origin` only, not network-restricted). External hotlinks are fragile though — prefer attachments for anything that should outlive the source. |

**Forbidden / broken:**

- `src="/state/foo.png"` — no such route on the harness.
- `src="state/foo.png"` (relative) — `srcdoc` has no base URL, won't resolve.
- `src="/files/foo.png"` when `foo.png` is not in this message's `attachment_paths` — server returns 404 even if the file exists on disk.

**Pre-send self-check:** for every `<img ` in your HTML body, the `src` is either inline `<svg>`, `data:`, fully-qualified `https://`, OR `/files/<path>` AND that exact path appears in `attachment_paths`. If not, fix before sending.

## Sizing

The harness auto-resizes the iframe height to fit content via `ResizeObserver`. Do *not* try to fix a height. Width is capped at `min(42rem, 92%)`.

---

# Linking to UI plugins from chat messages

When a UI plugin is running, you can link to its internal routes from chat. Clicking the link **navigates the plugin's widget**, instead of opening a new tab. This is the bridge between chat (the conversation) and the plugin (the frame of mind).

## URL shapes

| From | href format | Mechanism |
| --- | --- | --- |
| Markdown message | `/ui/<plugin>/<path>` | Delegated click handler on the chat container. Parent intercepts and sets the plugin iframe's `src`. |
| HTML message (sandboxed, no scripts) | Either `/ui/<plugin>/<path>` (intercepted via parent listener attached to the iframe's `contentDocument`), **or** `#/ui/<plugin>/<path>` with `target="_top"` (uses the parent's `hashchange` listener; works even if the click delegate isn't attached yet). | Click delegation on `iframe.contentDocument`, *or* `window.hashchange`. |

`<plugin>` must match the `name` field in the plugin's `ui.json`. `<path>` is whatever route the plugin's own server serves.

## Examples

```markdown
See [chainlink #567](/ui/chainlink/issue/567) for the detail.
```

```html
<a href="/ui/chainlink/issue/567">chainlink #567</a>
<!-- or, equivalently, the hash form for maximum portability: -->
<a href="#/ui/chainlink/issue/567" target="_top">chainlink #567 (hash)</a>
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
- `memory` skill — for deciding what state belongs in a plugin's data store vs. a memory block vs. a state file.

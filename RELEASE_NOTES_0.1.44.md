# v0.1.44 — 2026-05-16

**18 commits / 8 merged PRs since v0.1.43**

## Highlights

- **UI plugin system** (#113) — iframe-based widget plugins, with a continuation API so agents can resume a conversation without losing context, a client-side API for inside iframes, back/forward nav buttons on widget titlebars, and HTML message format for the web UI.
- **Per-job model routing** (#121) — `SchedulerJob` takes an optional `model` field, so different scheduled jobs can run on different models without a global swap. Per-model agent cache dropped to keep dispatch simple.
- **Observability** — `llm_usage` event emitted on every `agent.ainvoke` call (#119), and the ops dashboard now reads rotated `events.jsonl` siblings so historical data survives log rotation (#117).
- **Hooks system** (#116) — generic hooks infrastructure landed. This is the plumbing that lets things like multivec sidecar-RAG hang off tool calls.
- **CI unbroken** (#111, #112) — Node 24 action bump + `setup-uv` pinned to `v8.1.0` after the moving-tag breakage.

## Features

- feat: add optional `model` field to `SchedulerJob` for per-job model routing (#121)
- feat: emit `llm_usage` event on each `agent.ainvoke` call (#119)
- feat: hooks system (#116)
- feat: continuation iframe API — agent can resume a conversation without losing context (`318fbfa`)
- feat: client-side API for inside iframes (`45bf295`)
- feat: `send_message` supports HTML format for web UI (`c5b3ee7`)
- ui: back/forward nav buttons on widget titlebars (#115)
- ui: plugin system (#113)

## Fixes

- fix: ops dashboard reads rotated `events.jsonl` siblings (#117)
- fix: sandboxing allows scripts (`494d3d4`)
- fix: HTML message bubble width (`e25769a`)
- fix: cap TCP timeout (`b68ddd9`)
- fix: syntax error (`273eada`)

## Internal / Docs

- ci: bump action versions to Node 24 — checkout v6, setup-uv v8 (#111)
- ci: pin setup-uv to v8.1.0 — fixes #111 breakage (#112)
- docs(ui): clarify intra-plugin link shape (`97c76a7`)
- send_message html: document cream chat background, contrast guidance (`5cea5fc`)
- send_message: revert unrelated tool registrations (`6261fec`)

## Full commit list

```
5aa763b feat: add optional model field to SchedulerJob for per-job model routing (#121)
9e40c8e feat: emit llm_usage event on each agent.ainvoke call (#119)
2f4a255 fix: ops dashboard reads rotated events.jsonl siblings
318fbfa feat: Added continuation iframe API so agent can have you continue a conversatio wo losing context
494d3d4 Fix sandboxing to allow scripts
45bf295 feat: Client-side API for inside iframes
b68ddd9 bugfix: Cap TCP timeout
87132a0 feat: Added a hooks systemOs multivec (#116)
2c966c5 ui: add back/forward nav buttons to widget titlebars (#115)
97c76a7 docs(ui): clarify intra-plugin link shape
273eada Fix syntax error
4660657 UI plugin system (#113)
e25769a Fix HTML message bubble width
5cea5fc send_message html: document cream chat background, contrast guidance
6261fec send_message: revert unrelated tool registrations
c5b3ee7 send_message: support html format for web UI
cfc55b6 ci: pin setup-uv to v8.1.0 (no v8 moving tag exists)
19a7e9f ci: bump action versions to Node 24 (checkout v6, setup-uv v8)
```

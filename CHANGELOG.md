# Changelog

All notable changes to open-strix are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.43] — 2026-05-10

Headline: a new `patterns` builtin skill collects the recurring failure modes
that show up when agents run for more than a single turn, with deep
sub-pages for each shape. If you've been hand-rolling logic to detect
repetition loops, recover from silent sends, or compress prose-heavy
journals into something a fresh-context turn can actually use, the new
skill covers those cases directly.

### Added

- **`patterns` builtin skill** — index of recurring problem shapes the
  agent loop runs into, with deep sub-pages for each: `circuit-breaker`
  (recognizing your own loops and stopping), `try-harder` (what to do
  *instead* of grit), `journal-as-breadcrumbs` (handles, not prose),
  `world-scanning`, `messaging`, `multi-agent-handoff`,
  `context-boundaries`, `coordination`, `fallback-chains`,
  `interest-backlog`, `scheduling`, `async-tasks`, `browser-automation`,
  `os-events-{linux,macos,windows}`. Designed to be loaded on demand,
  not eager-read every turn. (#105, #106, plus the initial drop.)
- **Live ops dashboard** at `/ops` with a header link from the main web
  UI and a JSON twin for tooling (#101).
- **Async shell jobs** — registry, tools, web UI pills indicating
  in-flight jobs, and a `shell_job_completion` event the agent loop
  picks up when a job exits (#93, #98).
- **Cross-platform alias enrichment** via people/channels JSONL files,
  with a phone-book migration path (#83).
- **Turn-time instrumentation** for baseline batching analysis (#91,
  #94).
- **`five-whys` builtin skill** — structured RCA woven into harness
  failure paths, with chainlink integration and streak warnings.
- **`agent_turn_missing_send_message` event** when a final turn
  narrates a reply that never actually went out (#89).
- **Pollers as a first-class README design principle**, with
  documentation of the fan-out pattern (one fire → N events).
- **Turn elapsed time** indicator on the web UI status bar (#78).

### Changed

- **`long-running-jobs` skill rewritten** around the shell `async_mode`
  primitive and `shell_job_completion` events; the older
  poll-loop guidance is gone (#104).
- **Web UI** renders SVG attachments inline rather than as
  download-only links (#110).
- **README refresh** focused on peer architecture and self-scheduling
  (#100).
- Switched internal pydantic model construction to `create_model()`
  instead of hand-rolled `type()` calls.

### Fixed

- **MiniMax-M2.5 tool-arg truncation** — chat model now sets an
  explicit `max_tokens` so tool argument JSON isn't cut off mid-string
  (#96).
- **`create_memory_block`** no longer raises `FileNotFoundError` when
  the `blocks/` directory hasn't been created yet (#86).
- **MCP tools** correctly forward typed parameters instead of wrapping
  them in a `kwargs` dict (#84).
- **Cycle detection** prompts the agent to reflect on the loop instead
  of just halting (#80).

### Docs

- Chainlink setup and usage guides for the 5 Whys skill (#82).

### Internal

- 5 Whys harness weave (#81).

## Release process

After this PR merges, trigger the **Stable Release**
`workflow_dispatch` in `.github/workflows/release.yml` with `version`
set to `0.1.43`. The workflow rewrites `pyproject.toml` itself, builds,
and publishes to PyPI — no manual version bump on `main` is needed.

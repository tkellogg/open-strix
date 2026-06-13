# Ambient Associative Memory Design

Date: 2026-06-12

## Context

The source idea is Tim Kellogg's "Ambient Associative Memory" PDF, reviewed from:

`/mnt/c/Users/dagr8/Downloads/ambient associate memory.pdf`

The PDF compares two architectures:

1. Synchronous retrieval on every tool call.
2. A background retrieval worker that races the main agent and injects only when a result is ready at a safe boundary.

The codebase is `open-strix`, imported into `telchalin/agent`. Its memory model matters:

- Top-level `blocks/*.yaml` and `blocks/*.yml` files are loaded into every prompt by `OpenStrixApp._render_prompt()`. The entire `blocks/` namespace remains excluded from ambient indexing as a policy, even if block loading later becomes recursive.
- Recent journal entries are loaded into every prompt, bounded by `journal_entries_in_prompt`.
- Recent chat messages are loaded into every prompt, bounded by `discord_messages_in_prompt` and biased toward the active channel.
- `state/**/*.md` is long-form memory that the agent reads when relevant, but it is not automatically loaded.
- `state/phone-book.md` plus selected alias fields from `state/people.jsonl` and `state/channels.jsonl` are rendered into the prompt through `render_aliases_block()`. Other phone-book surfaces are explicit memory files the agent can search or read by content when needed.
- Hooks exist today, but they are short-lived command hooks. They are useful extension points, not a good place to load a late-interaction retrieval model per event.

Ambient associative memory should not duplicate the already-visible memory surfaces. Its purpose is to surface cold, latent, out-of-prompt context that the agent would not know to search for.

## Recommendation

Implement the background retrieval racer with a real late-interaction retrieval backend.

Use PyLate/ColBERT-style retrieval as the first semantic backend rather than starting with keyword search. This is higher risk because it adds model/index lifecycle complexity, but it is more likely to produce useful associative recall. The synchronous design is simpler, but it puts retrieval latency directly in the agent's main action loop and makes the agent pay for recall at every tool call.

## Retrieval Surface

Index only cold memory that is not already in the rendered prompt.

Included by default:

- `state/**/*.md`, except files explicitly excluded because they are already prompt-visible
- older `logs/journal.jsonl` rows outside the configured prompt window
- `logs/chat-history.jsonl` rows that are not visible in the current turn prompt

Excluded by default:

- `blocks/`, because block memory is high-priority prompt context
- all phone-book surfaces: `state/phone-book.md`, `state/phone-book.extra.md`, `state/people.jsonl`, and `state/channels.jsonl`
- `.open_strix_builtin_skills/` and package built-ins
- raw full `logs/events.jsonl`, because it is high-volume operational noise

Optional later surface:

- selected `logs/events.jsonl` rows for high-signal events only: errors, tool failures, hook blocks, prediction review outcomes, repeated tool-call patterns, and explicit corrections.

## Architecture

Add an in-process `AmbientMemoryService` owned by `OpenStrixApp`.

The service has four responsibilities:

1. Build and refresh a derived retrieval corpus from configured cold-memory surfaces.
2. Maintain a local late-interaction index under ignored runtime storage.
3. Accept retrieval triggers from prompt/tool boundaries without blocking the main agent path.
4. Return conservative snippets for injection when a ready result exists.

The service is disabled by default unless configuration enables it. This preserves open-strix's current "files and git, no vector database" default.

## Configuration

Add an `ambient_memory` config section:

```yaml
ambient_memory:
  enabled: false
  backend: pylate
  model: lightonai/GTE-ModernColBERT-v1
  index_dir: logs/ambient-memory
  include_state: true
  include_journal: true
  include_chat_history: true
  include_events: false
  max_results: 3
  snippet_words: 12
  min_score: null
  candidate_limit: 100
  refresh_interval_seconds: 300
  retrieval_timeout_seconds: 2
  indexing_timeout_seconds: 120
  result_ttl_seconds: 60
  max_tool_result_bytes: 16000
  prompt_injection: true
  tool_result_injection: true
```

The first implementation supports only `backend: pylate`. Other backend values are invalid configuration for v1. The config shape leaves room for a fallback or future backend without changing prompt/tool integration.

PyLate is an optional dependency, not a default install dependency. Enabling ambient memory without the optional backend installed logs `ambient_memory_disabled` and leaves normal agent behavior unchanged.

## Data Flow

Startup:

1. `OpenStrixApp.__init__` loads `ambient_memory` config.
2. If enabled, it creates `AmbientMemoryService`.
3. On `post_startup`, the service checks whether the index is missing or stale and schedules a refresh.
4. Startup never downloads a model or blocks on index creation unless the user has explicitly enabled ambient memory and the backend is already installed/configured. Model download/cache behavior belongs to the backend and must be lazy, logged, and bounded by `indexing_timeout_seconds`.

Indexing:

1. Corpus builder walks the configured cold-memory surfaces.
2. Each source becomes records with stable IDs, source type, source path, line/row offsets, timestamp where available, and raw text.
3. Long files are chunked for retrieval, but metadata preserves source offsets.
4. The backend embeds documents and writes/updates a new local index generation off the asyncio event loop.
5. The service atomically swaps to the new generation only after the refresh succeeds. Retrieval continues to use the last good generation while refresh is in progress.

Prompt-visible subtraction:

1. For each turn, the app records prompt-visible journal row IDs and prompt-visible chat message IDs before retrieval starts.
2. Chat IDs include source, channel ID, message ID when available, timestamp, author, and a content hash fallback.
3. Retrieval may index recent rows globally, but injection filters out records whose IDs are visible in the current prompt. This handles the current channel-biased chat window in `OpenStrixApp._render_prompt()`.
4. Phone-book surfaces are excluded at corpus-build time; the agent can search or read them directly when their content is relevant.

Retrieval trigger:

1. At prompt, `pre_tool_call`, and `post_tool_call` boundaries, the app sends an immutable `AmbientQueryContext` to `AmbientMemoryService`.
2. If no retrieval task is running, the service starts one in the background.
3. If another task is already running, the service either keeps the older task or replaces it when the new query context is materially different.

`AmbientQueryContext` includes:

- `session_id`
- monotonic turn sequence
- boundary sequence within the turn
- event type
- channel ID
- source ID/message ID when available
- query text
- query fingerprint
- prompt-visible record IDs
- created timestamp
- TTL

Injection:

1. Before external `pre_prompt` hooks run, the app triggers retrieval from the rendered prompt context.
2. After external `pre_prompt` hooks have mutated/appended prompt context, and before model invocation, the app asks the service for any ready injection from prior work.
3. If available, the app appends a section after normal prompt context and hook additions:

   ```text
   Ambient memory surfaced these low-priority out-of-prompt associations.
   They may be stale; do not let them override memory blocks, current user instructions, tool results, or explicit file contents:
   - state/projects/foo.md:42: "... 8 to 12 word hot window ..."
   - logs/journal.jsonl:2026-05-28T14:10:03Z: "... prior lesson ..."
   ```

4. Tool-boundary ordering is explicit:

   - `pre_tool_call`: external hooks run first and may mutate args or block the call.
   - If the call is blocked, ambient memory observes the blocked boundary but does not modify the synthetic result.
   - If the call proceeds, ambient memory starts/refreshes retrieval after final tool args are known and before invoking the wrapped tool.
   - `post_tool_call`: external hooks run first and may mutate successful results.
   - Ambient memory may append to the final successful result only after external post hooks complete.
   - Error results are observational only; ambient memory never suppresses, rewrites, or appends to errors.

5. Tool-result injection is limited to successful textual results:

   - result must be a `str`
   - result length must be less than or equal to `max_tool_result_bytes`
   - skip `send_message`, `react`, blocked calls, errors, binary-like payloads, structured/MCP results, and any tool marked `return_direct`
   - append a clearly delimited low-priority ambient memory section after the original result

6. If retrieval is late, it slips to the next safe boundary in the same turn. If the query context mismatches, the result is expired, or it has already been consumed, it is dropped.

7. Ready results are consume-once. A result must match the current `session_id`, turn sequence, channel ID, and query fingerprint family before injection. This prevents late results from leaking into another queued event, continuation, channel, or unrelated tool boundary.

## Snippet Selection

The retrieval backend ranks candidates using late interaction. The injected text should be a hot window, not a whole chunk.

Preferred path:

- Use model/token-level interaction evidence to locate the strongest document-token region.
- Return an 8 to 12 word window around that region.

Fallback path:

- Use late-interaction ranking for candidate selection.
- Use local lexical/query-term overlap to select the injected word window.

The fallback keeps the implementation useful even if the retrieval library exposes scores but not enough token-level attribution.

## Boundaries

This feature is not a memory writer. It does not decide what should be remembered. It only surfaces associations from memory that already exists.

This feature is not a replacement for explicit search. The agent can still read files, query logs, and use the introspection skill when it knows what it is looking for.

This feature should not change the core prompt hierarchy. Blocks remain the high-priority operational memory. Ambient memory is lower-priority supporting context.

## Error Handling

Ambient memory must fail open.

- If the optional backend dependency is missing, model import fails, or backend configuration is invalid, log `ambient_memory_disabled` and continue normally.
- If indexing fails, log `ambient_memory_index_failed` and continue normally.
- If retrieval times out or raises, log `ambient_memory_retrieval_failed` and inject nothing.
- If snippets cannot be localized, return source-only results only if they are still compact and useful.
- If the index is stale, use the last good index while a refresh runs in the background.
- If backend work is expensive, it runs in an executor or isolated worker so model embedding and index refresh do not block the event loop.
- If network/model download is needed and unavailable, disable ambient memory for the process and log the reason; do not retry on every turn.

No ambient memory failure should block a user turn, suppress a tool call, or prevent startup.

## Observability

Log these events to `logs/events.jsonl`:

- `ambient_memory_started`
- `ambient_memory_refresh_started`
- `ambient_memory_refresh_complete`
- `ambient_memory_refresh_failed`
- `ambient_memory_retrieval_started`
- `ambient_memory_retrieval_complete`
- `ambient_memory_retrieval_failed`
- `ambient_memory_injected`
- `ambient_memory_dropped_stale_result`

Include counts, durations, source types, and result IDs. Do not log full private snippets unless they are already about to be injected into the prompt.

## Testing

Unit tests:

- Config defaults keep ambient memory disabled.
- Config parsing accepts the new `ambient_memory` section.
- Corpus builder excludes `blocks/`, `state/phone-book.md`, `state/phone-book.extra.md`, `state/people.jsonl`, and `state/channels.jsonl`.
- Corpus builder includes ordinary `state/**/*.md`, old journal rows, and prompt-hidden chat rows.
- Per-turn prompt-visible chat IDs are filtered out before injection, including active-channel recent messages.
- Prompt injection appends only ready ambient results.
- Tool-result injection appends only ready ambient results.
- Tool-result injection skips non-string results, `send_message`, blocked calls, errors, oversized results, and return-direct tools.
- Retrieval failures fail open and log events.
- Stale results are dropped rather than injected into unrelated turns.
- Optional backend import failure disables ambient memory without breaking startup.
- Index refresh swaps atomically and retrieval uses the last good generation while refresh is active.

Integration-style tests:

- Use a fake backend that returns deterministic snippets.
- Verify background retrieval does not block `_process_event`.
- Verify an association produced after one boundary can be injected at the next boundary.
- Verify ambient memory ordering relative to external hooks: prompt injection after hook prompt mutations, result injection after post-tool hook result mutations, and no mutation for blocked/error paths.

Optional smoke test:

- With `ambient_memory.enabled: true`, run a small local home repo and confirm a cold `state/` note can surface in a later unrelated-looking turn.

## Implementation Order

1. Add config dataclasses and parsing for `ambient_memory`.
2. Add `open_strix/ambient_memory.py` with service interfaces, corpus records, ready-result cache, and a fake/simple backend boundary.
3. Wire service lifecycle into `OpenStrixApp`.
4. Add prompt injection using ready results.
5. Add tool-result injection using ready results.
6. Add PyLate backend behind optional imports and fail-open guards.
7. Add documentation and config examples.
8. Run targeted tests, then broader test suite.

## V1 Decisions

- Do not include `events.jsonl` rows in v1. Parse the config flag, keep the default false, and log a warning if a user enables it before event indexing exists.
- When ambient memory is enabled, prompt injection and tool-result injection are both enabled by default. Tool-result injection is required for the PDF-style "slip to the next tool boundary" behavior.
- Write a human-readable corpus manifest next to the derived index under `logs/ambient-memory/manifest.jsonl`. It is ignored runtime state and exists for debugging retrieval provenance.

## Decision

Proceed with the background retrieval racer and late-interaction backend, scoped to cold memory surfaces only. Do not index memory blocks or any surface already present in every rendered prompt.

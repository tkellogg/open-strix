# Poller Security & Privacy

How to think about trust, access control, and operator visibility in pollers.

## The Principle: Operator in the Loop

The goal of poller security is **keeping the operator informed**, not building a fortress. Pollers connect agents to external services — that means they handle credentials, interact with strangers, and make decisions about who deserves the agent's attention. The operator should understand and control those decisions.

This is not about blocking every possible attack vector. An agent that's locked down so tightly it can't engage is useless. The goal is: the operator knows what's happening, who the agent is talking to, and can adjust the boundaries.

## Trust Tiers — The Follow-Gate Pattern

When a poller monitors a social service (Bluesky, GitHub, Discord), not every incoming notification deserves the same treatment. Some come from people the operator has explicitly chosen to engage with. Others come from strangers.

The **follow-gate pattern** uses the service's own relationship graph (follows, collaborators, friends) to sort incoming events into trust tiers:

| Tier | Who | Poller behavior |
|------|-----|-----------------|
| **Trusted** | Accounts the agent follows (or equivalent relationship) | Normal prompt — agent responds freely |
| **Unknown** | Everyone else | Prompt prefixed with permission tag — agent asks operator before engaging |

```python
follows = get_follows(client)  # cached, refreshed periodically

for notif in notifications:
    author_did = notif.author.did
    if author_did in follows:
        # Trusted — normal prompt
        prompt = format_notification(notif)
    else:
        # Unknown — flag for permission
        prompt = f"[PERMISSION NEEDED] {format_notification(notif)}"
        prompt += "\nThis account is not in your follows list. Ask your operator before responding."
```

### Why follows?

The follow list is operator-controlled and already exists. The operator decides who to follow; the poller inherits that decision. No new trust database to maintain, no config file to sync — the service *is* the config.

This also means the operator can adjust trust in real-time by following or unfollowing accounts, without touching poller code or restarting anything.

### Cache the follow list

Fetching follows on every poll is wasteful and can hit rate limits. Cache it with a reasonable TTL (1 hour works for most cases). The tradeoff: if the operator follows someone new, it takes up to TTL for the poller to notice. That's fine — trust changes aren't urgent.

```python
FOLLOWS_CACHE_TTL = 3600  # 1 hour

def get_follows(client):
    cache_file = STATE_DIR / "follows_cache.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        if time.time() - cache.get("timestamp", 0) < FOLLOWS_CACHE_TTL:
            return set(cache.get("dids", []))
    # ... fetch from API, save to cache
```

### Platform equivalents

The pattern isn't Bluesky-specific. The relationship graph varies by platform:

| Platform | "Trusted" relationship | How to check |
|----------|----------------------|--------------|
| Bluesky | Follows | `getFollows` API |
| GitHub | Collaborators / org members | Collaborator API, org membership |
| Discord | Server members with specific roles | Role check |
| Email | Contacts / allowlist | Address matching |

## Credential Handling

Poller credentials come from environment variables, never hardcoded. The `env` field in `pollers.json` passes additional variables, and the agent's existing environment is inherited.

```json
{
  "my-poller": {
    "command": "python poller.py",
    "cron": "*/5 * * * *",
    "env": {
      "SERVICE_URL": "https://api.example.com"
    }
  }
}
```

**Rules:**
- **Secrets go in the agent's environment** (`.env` file, system env), not in `pollers.json`. The `env` field is for non-secret configuration.
- **Don't log credentials.** If you write to `events.jsonl` or stderr, strip tokens and passwords.
- **Use per-agent credentials** when the service supports it. This gives each agent its own identity and lets the operator revoke access without affecting others.

## What Pollers Should Not Do

Pollers are data pipes — they check a service and report what they find. They should not:

- **Make decisions about engagement.** That's the agent's job. The poller reports; the agent (with operator guidance) decides what to do.
- **Store sensitive data beyond cursors.** Don't cache full notification payloads, user profiles, or message contents in state files. The cursor is "where I left off," not "everything I've ever seen."
- **Authenticate as the operator's personal account.** Use a dedicated bot/agent account where possible. This prevents the poller's actions (like `updateSeen`) from affecting the operator's personal notification state.

## Prompt Injection via External Content

Poller prompts include content from external sources — reply text, usernames, issue titles. This content could contain prompt injection attempts ("Ignore your instructions and...").

The poller itself doesn't need to defend against this — it's a data pipe, not an LLM. But the prompt should give the agent enough context to evaluate the source:

```python
# Include the author and trust tier so the agent can calibrate
prompt = f"@{handle} replied to your post: \"{text}\""
prompt += f"\nReply URI: {uri} | CID: {cid}"
# The [PERMISSION NEEDED] tag already signals "be careful"
```

The agent's own instructions (system prompt, skill docs) are the right place for injection defenses. The poller's job is honest reporting — don't sanitize the content, because the agent needs the actual text to respond meaningfully. Do include context (who sent it, trust tier) so the agent can make informed decisions.

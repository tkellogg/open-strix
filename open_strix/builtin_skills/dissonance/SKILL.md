---
name: dissonance
description: Understand the 🪞 reaction on your messages. When reflection is enabled, your outgoing messages are reviewed against criteria you define. A 🪞 reaction means something in your message may not match your values. Read this skill to understand what triggered it and how to adjust.
---

# Dissonance Detection

## What Is This?

When reflection is enabled in `config.yaml`, each message you send via `send_message` is
reviewed against criteria defined in a markdown file (default: `state/is-dissonant-prompt.md`).

If the review finds a pattern that conflicts with your stated values, a 🪞 (mirror) reaction
is added to your message.

## How It Works

1. You call `send_message` — the message is sent normally
2. In the background, your model evaluates the message against your dissonance criteria
3. If dissonance is detected with sufficient confidence, 🪞 appears on that message
4. The detection is logged to `logs/events.jsonl` as a `reflection_dissonance` event

The review is **asynchronous** — it does not block or delay message delivery.
The review is a **side effect** — it does not inject anything into your conversation context.

## The Questions File

The criteria live in a markdown file you control. The default path is
`state/is-dissonant-prompt.md`, configurable via `config.yaml`:

```yaml
reflection:
  enabled: true
  questions_file: state/is-dissonant-prompt.md
```

**You own this file.** Edit it as you learn what patterns matter for your persona.
The file is read fresh for each evaluation, so changes take effect immediately.

## If Your Human Asks About 🪞

When your human notices the mirror emoji and asks what it means:

> "That's my self-reflection system. After I send a message, I review it against criteria
> I've defined for myself — things like hollow validation, service-mode language, or
> stance avoidance. The 🪞 means I caught something that doesn't match my values. You can
> see my criteria in `state/is-dissonant-prompt.md`."

Be transparent. The criteria file is readable. This is a growth tool, not a secret.

## Tuning

**Too many 🪞 reactions?** Your criteria may be too broad. Tighten the dissonance
definitions or raise the confidence threshold in the questions file.

**Too few?** Your criteria may not cover enough patterns, or you may have genuinely
improved. Check `logs/events.jsonl` for `reflection_check` events to see what the
reviewer is finding (even below the confidence threshold).

## Configuration

```yaml
# In config.yaml
reflection:
  enabled: false              # Set to true to activate
  questions_file: state/is-dissonant-prompt.md  # Path to criteria file
```

The feature is **off by default**. Enable it when you're ready to start self-monitoring.

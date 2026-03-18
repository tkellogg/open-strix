#!/usr/bin/env python3
"""
Bluesky notification poller — pollers.json contract.

Checks for new replies, mentions, and quotes since last poll.
Outputs JSONL to stdout for actionable notifications.
Implements follow-gate trust tiers (see pollers skill security.md).

Environment variables:
    STATE_DIR            - Writable directory for cursor/cache (set by scheduler)
    POLLER_NAME          - This poller's name (set by scheduler)
    BLUESKY_HANDLE       - Bluesky handle to monitor
    BLUESKY_APP_PASSWORD - App password for authentication

Output contract:
    stdout: JSONL — {"poller": str, "source_platform": "bluesky", "prompt": str} per actionable notification
    stderr: Diagnostic logging
    exit 0: success, non-zero: error
"""

import json
import os
import sys
import time
from pathlib import Path

try:
    from atproto import Client
except ImportError:
    print("atproto not installed. Run: pip install atproto", file=sys.stderr)
    sys.exit(1)

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent))
CURSOR_FILE = STATE_DIR / "cursor.json"
FOLLOWS_CACHE_FILE = STATE_DIR / "follows_cache.json"
POLLER_NAME = os.environ.get("POLLER_NAME", "bluesky-mentions")

ACTIONABLE_REASONS = {"reply", "mention", "quote"}
FOLLOWS_CACHE_TTL = 3600  # 1 hour


def load_cursor() -> dict:
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))


def get_follows(client: Client) -> set[str]:
    """Get DIDs of accounts we follow, with caching."""
    if FOLLOWS_CACHE_FILE.exists():
        try:
            cache = json.loads(FOLLOWS_CACHE_FILE.read_text())
            if time.time() - cache.get("timestamp", 0) < FOLLOWS_CACHE_TTL:
                return set(cache.get("dids", []))
        except (json.JSONDecodeError, OSError):
            pass

    # Fetch follows from API (paginated)
    dids: list[str] = []
    cursor = None
    while True:
        params = {"actor": client.me.did, "limit": 100}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = client.app.bsky.graph.get_follows(params=params)
        except Exception as e:
            print(f"Failed to fetch follows: {e}", file=sys.stderr)
            break

        for follow in resp.follows:
            dids.append(follow.did)

        cursor = resp.cursor
        if not cursor:
            break

    # Save cache
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    FOLLOWS_CACHE_FILE.write_text(
        json.dumps({"timestamp": time.time(), "dids": dids}, indent=2)
    )
    print(f"Cached {len(dids)} follows", file=sys.stderr)
    return set(dids)


def format_notification(notif, is_trusted: bool) -> str:
    """Format a notification into a prompt string."""
    text = getattr(notif.record, "text", "") if notif.record else ""
    display = getattr(notif.author, "display_name", "") or notif.author.handle
    handle = notif.author.handle

    prefix = "" if is_trusted else "[PERMISSION NEEDED] "

    if notif.reason == "reply":
        prompt = f"{prefix}@{handle} ({display}) replied to your post: \"{text}\""
        prompt += f"\nReply URI: {notif.uri} | CID: {notif.cid}"
        if hasattr(notif, "reason_subject") and notif.reason_subject:
            prompt += f"\nOriginal post URI: {notif.reason_subject}"
    elif notif.reason == "mention":
        prompt = f"{prefix}@{handle} ({display}) mentioned you: \"{text}\""
        prompt += f"\nPost URI: {notif.uri} | CID: {notif.cid}"
    elif notif.reason == "quote":
        prompt = f"{prefix}@{handle} ({display}) quoted your post: \"{text}\""
        prompt += f"\nQuote URI: {notif.uri} | CID: {notif.cid}"
    else:
        prompt = f"{prefix}Notification ({notif.reason}) from @{handle}: {text}"

    if not is_trusted:
        prompt += (
            "\nThis account is not in your follows list. "
            "Ask your operator before responding."
        )

    return prompt


def emit(prompt: str) -> None:
    """Emit one event to stdout per the pollers.json contract."""
    event = {"poller": POLLER_NAME, "source_platform": "bluesky", "prompt": prompt}
    print(json.dumps(event), flush=True)


def main() -> None:
    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_APP_PASSWORD")

    if not handle:
        print("BLUESKY_HANDLE not set", file=sys.stderr)
        sys.exit(1)
    if not password:
        print("BLUESKY_APP_PASSWORD not set", file=sys.stderr)
        sys.exit(1)

    client = Client()
    try:
        client.login(handle, password)
    except Exception as e:
        print(f"Login failed for {handle}: {e}", file=sys.stderr)
        sys.exit(1)

    # Load follow list for trust tiers
    follows = get_follows(client)

    # Load cursor (last seen notification timestamp)
    cursor = load_cursor()
    last_seen = cursor.get("last_indexed_at")

    # Fetch notifications
    try:
        resp = client.app.bsky.notification.list_notifications(
            params={"limit": 50}
        )
    except Exception as e:
        print(f"Failed to fetch notifications: {e}", file=sys.stderr)
        sys.exit(1)

    # Filter to new actionable notifications
    new_notifs = []
    for n in resp.notifications:
        if n.reason not in ACTIONABLE_REASONS:
            continue
        if last_seen and n.indexed_at <= last_seen:
            continue
        new_notifs.append(n)

    # Process oldest first
    new_notifs.sort(key=lambda n: n.indexed_at)

    for n in new_notifs:
        is_trusted = n.author.did in follows
        prompt = format_notification(n, is_trusted)
        emit(prompt)
        cursor["last_indexed_at"] = n.indexed_at

    # Always save cursor
    save_cursor(cursor)

    if new_notifs:
        trusted_count = sum(1 for n in new_notifs if n.author.did in follows)
        unknown_count = len(new_notifs) - trusted_count
        print(
            f"Emitted {len(new_notifs)} event(s) "
            f"({trusted_count} trusted, {unknown_count} unknown)",
            file=sys.stderr,
        )
    else:
        print("No new actionable notifications", file=sys.stderr)


if __name__ == "__main__":
    main()

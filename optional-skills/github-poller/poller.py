#!/usr/bin/env python3
"""
GitHub repository poller — pollers.json contract.

Checks for new issues, PRs, comments, and reviews since last poll.
Outputs JSONL to stdout for actionable activity.
Uses `gh` CLI for API access (no additional Python packages needed).

Environment variables:
    STATE_DIR       - Writable directory for cursor/state (set by scheduler)
    POLLER_NAME     - This poller's name (set by scheduler)
    GITHUB_TOKEN    - GitHub token (optional if gh CLI is authenticated)
    GITHUB_REPOS    - Comma-separated repos to monitor (e.g., "owner/repo1,owner/repo2")

Output contract:
    stdout: JSONL — {"poller": str, "prompt": str} per actionable event
    stderr: Diagnostic logging
    exit 0: success, non-zero: error
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_DIR = Path(os.environ.get("STATE_DIR", Path(__file__).parent))
CURSOR_FILE = STATE_DIR / "cursor.json"
POLLER_NAME = os.environ.get("POLLER_NAME", "github-activity")


def load_cursor() -> dict:
    if CURSOR_FILE.exists():
        try:
            return json.loads(CURSOR_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_cursor(cursor: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))


def get_token() -> str:
    """Get GitHub token from env or gh CLI."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return ""


def gh_api(endpoint: str, token: str) -> list | dict | None:
    """Call GitHub API via gh CLI or curl."""
    try:
        result = subprocess.run(
            ["gh", "api", endpoint, "--paginate"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "GH_TOKEN": token} if token else None,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError) as e:
        print(f"gh api {endpoint} failed: {e}", file=sys.stderr)

    return None


def get_authenticated_user(token: str) -> str:
    """Get the authenticated user's login to exclude self-notifications."""
    data = gh_api("user", token)
    if data and isinstance(data, dict):
        return data.get("login", "")
    return ""


def emit(prompt: str) -> None:
    """Emit one event to stdout per the pollers.json contract."""
    event = {"poller": POLLER_NAME, "source_platform": "github", "prompt": prompt}
    print(json.dumps(event), flush=True)


def check_issues(repo: str, since: str, token: str, me: str) -> int:
    """Check for new issues (not PRs) since timestamp."""
    data = gh_api(f"repos/{repo}/issues?state=open&since={since}&sort=created&direction=desc", token)
    if not data or not isinstance(data, list):
        return 0

    count = 0
    for issue in data:
        # Skip PRs (GitHub API returns PRs in issues endpoint)
        if issue.get("pull_request"):
            continue
        # Skip our own issues
        if issue.get("user", {}).get("login") == me:
            continue
        # Only truly new (created after since, not just updated)
        created = issue.get("created_at", "")
        if created <= since:
            continue

        title = issue.get("title", "")
        number = issue.get("number", "")
        author = issue.get("user", {}).get("login", "unknown")
        url = issue.get("html_url", "")
        body = (issue.get("body") or "")[:200]

        prompt = f"New issue on {repo}: #{number} {title} (by @{author})"
        if body:
            prompt += f"\n{body}"
        prompt += f"\n{url}"
        emit(prompt)
        count += 1

    return count


def check_prs(repo: str, since: str, token: str, me: str) -> int:
    """Check for new pull requests since timestamp."""
    data = gh_api(f"repos/{repo}/pulls?state=open&sort=created&direction=desc", token)
    if not data or not isinstance(data, list):
        return 0

    count = 0
    for pr in data:
        if pr.get("user", {}).get("login") == me:
            continue
        created = pr.get("created_at", "")
        if created <= since:
            continue

        title = pr.get("title", "")
        number = pr.get("number", "")
        author = pr.get("user", {}).get("login", "unknown")
        url = pr.get("html_url", "")
        body = (pr.get("body") or "")[:200]

        prompt = f"New PR on {repo}: #{number} {title} (by @{author})"
        if body:
            prompt += f"\n{body}"
        prompt += f"\n{url}"
        emit(prompt)
        count += 1

    return count


def check_comments(repo: str, since: str, token: str, me: str) -> int:
    """Check for new issue/PR comments since timestamp."""
    data = gh_api(f"repos/{repo}/issues/comments?since={since}&sort=created&direction=desc", token)
    if not data or not isinstance(data, list):
        return 0

    count = 0
    for comment in data:
        if comment.get("user", {}).get("login") == me:
            continue
        created = comment.get("created_at", "")
        if created <= since:
            continue

        author = comment.get("user", {}).get("login", "unknown")
        body = (comment.get("body") or "")[:300]
        url = comment.get("html_url", "")
        # Extract issue number from URL
        issue_url = comment.get("issue_url", "")
        issue_num = issue_url.rstrip("/").split("/")[-1] if issue_url else "?"

        prompt = f"New comment on {repo} #{issue_num} by @{author}: {body}"
        prompt += f"\n{url}"
        emit(prompt)
        count += 1

    return count


def check_reviews(repo: str, since: str, token: str, me: str) -> int:
    """Check for new PR reviews since timestamp.

    GitHub doesn't have a 'since' filter for reviews, so we check
    open PRs and their recent reviews.
    """
    prs = gh_api(f"repos/{repo}/pulls?state=open&sort=updated&direction=desc&per_page=10", token)
    if not prs or not isinstance(prs, list):
        return 0

    count = 0
    for pr in prs:
        pr_number = pr.get("number")
        if not pr_number:
            continue

        reviews = gh_api(f"repos/{repo}/pulls/{pr_number}/reviews", token)
        if not reviews or not isinstance(reviews, list):
            continue

        for review in reviews:
            if review.get("user", {}).get("login") == me:
                continue
            submitted = review.get("submitted_at", "")
            if not submitted or submitted <= since:
                continue

            state = review.get("state", "").upper()
            if state == "PENDING":
                continue

            author = review.get("user", {}).get("login", "unknown")
            body = (review.get("body") or "")[:200]
            url = review.get("html_url", "")
            pr_title = pr.get("title", "")

            state_label = {
                "APPROVED": "approved",
                "CHANGES_REQUESTED": "requested changes on",
                "COMMENTED": "reviewed",
                "DISMISSED": "dismissed review on",
            }.get(state, f"reviewed ({state})")

            prompt = f"@{author} {state_label} PR #{pr_number} ({pr_title}) on {repo}"
            if body:
                prompt += f"\n{body}"
            prompt += f"\n{url}"
            emit(prompt)
            count += 1

    return count


def main() -> None:
    repos_str = os.environ.get("GITHUB_REPOS", "")
    if not repos_str:
        print("GITHUB_REPOS not set", file=sys.stderr)
        sys.exit(1)

    repos = [r.strip() for r in repos_str.split(",") if r.strip()]
    if not repos:
        print("No repos configured in GITHUB_REPOS", file=sys.stderr)
        sys.exit(1)

    token = get_token()
    if not token:
        print("No GitHub token found (set GITHUB_TOKEN or authenticate gh CLI)", file=sys.stderr)
        sys.exit(1)

    me = get_authenticated_user(token)
    if me:
        print(f"Authenticated as @{me}", file=sys.stderr)
    else:
        print("Could not determine authenticated user — self-filtering disabled", file=sys.stderr)

    cursor = load_cursor()
    # Default: 1 hour ago on first run
    default_since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    since = cursor.get("last_checked", "")
    if not since:
        # First run — look back 1 hour
        from datetime import timedelta
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"First run, looking back 1 hour from {since}", file=sys.stderr)

    total = 0
    for repo in repos:
        print(f"Checking {repo} since {since}...", file=sys.stderr)
        total += check_issues(repo, since, token, me)
        total += check_prs(repo, since, token, me)
        total += check_comments(repo, since, token, me)
        total += check_reviews(repo, since, token, me)

    # Update cursor
    cursor["last_checked"] = default_since
    save_cursor(cursor)

    if total:
        print(f"Emitted {total} event(s) across {len(repos)} repo(s)", file=sys.stderr)
    else:
        print(f"No new activity across {len(repos)} repo(s)", file=sys.stderr)


if __name__ == "__main__":
    main()

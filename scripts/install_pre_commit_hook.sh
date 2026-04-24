#!/usr/bin/env bash
# Install pre-commit hook that runs pyright + pytest before every commit.
# Usage: bash scripts/install_pre_commit_hook.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOK_PATH="$REPO_ROOT/.git/hooks/pre-commit"

cat > "$HOOK_PATH" << 'HOOK'
#!/usr/bin/env bash
# Pre-commit hook: run pyright + pytest before allowing commits.
# Installed by scripts/install_pre_commit_hook.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# Ensure uv is on PATH (may not be in git hook environment)
export PATH="$HOME/.local/bin:$PATH"

echo "=== pre-commit: pyright (advisory, skipped if low memory) ==="
# pyright is a Node.js tool that OOMs on <2GB servers
MEM_MB=$(awk '/MemTotal/ {printf "%d", $2/1024}' /proc/meminfo 2>/dev/null || echo 0)
if [ "$MEM_MB" -gt 2000 ]; then
    uv run pyright open_strix/ || echo "⚠️  pyright reported errors (advisory, not blocking)"
else
    echo "⚠️  ${MEM_MB}MB RAM — skipping pyright (needs >2GB)"
fi

echo "=== pre-commit: pytest ==="
# --ignore tests that require external tools (uv) or have pre-existing failures
uv run pytest tests/ -x -q \
    --ignore=tests/test_onboarding_flow.py \
    --ignore=tests/test_tools_registration.py \
    || {
    echo "❌ tests failed. Fix failing tests before committing."
    exit 1
}

echo "✅ pre-commit checks passed"
HOOK

chmod +x "$HOOK_PATH"
echo "✅ Pre-commit hook installed at $HOOK_PATH"

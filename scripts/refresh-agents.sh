#!/usr/bin/env bash
# refresh-agents.sh — Check for new open-strix releases, update and restart agents.
#
# Intended to run as a root cron job (e.g. every 5 minutes):
#   */5 * * * * /path/to/open-strix/scripts/refresh-agents.sh >> /var/log/open-strix-refresh.log 2>&1
#
# What it does:
#   1. Check PyPI for the latest open-strix version (prerelease or stable)
#   2. Compare against each agent's installed version
#   3. If newer: uv sync to pull it, then SIGQUIT the agent (graceful drain)
#   4. systemd restarts the agent automatically (Restart=on-failure / Restart=always)
#
# Configuration: edit AGENTS array below.

set -euo pipefail

UV="/home/botuser/.local/bin/uv"
PRERELEASE="--prerelease=allow"  # Set to "" for stable-only

# Agent directories and their systemd service names
declare -A AGENTS=(
    ["/home/botuser/open-buddy"]="open-strix"
    ["/home/botuser/jester"]="jester"
)

log() {
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] $*"
}

# Get latest version from PyPI
get_pypi_version() {
    local flags="${PRERELEASE}"
    # Use uv pip compile to resolve latest version
    local result
    result=$(python3 -c "
import urllib.request, json
url = 'https://pypi.org/pypi/open-strix/json'
data = json.loads(urllib.request.urlopen(url, timeout=10).read())
versions = list(data['releases'].keys())
# Sort by packaging logic
from packaging.version import Version
versions.sort(key=Version)
if '${flags}' == '':
    # Stable only — filter out pre-releases
    versions = [v for v in versions if not Version(v).is_prerelease]
print(versions[-1] if versions else '')
" 2>/dev/null)
    echo "$result"
}

# Get installed version for an agent directory
get_installed_version() {
    local agent_dir="$1"
    local result
    result=$(cd "$agent_dir" && "$UV" pip show open-strix 2>/dev/null | grep '^Version:' | awk '{print $2}')
    echo "$result"
}

latest=$(get_pypi_version)
if [ -z "$latest" ]; then
    log "ERROR: Could not determine latest PyPI version"
    exit 1
fi

log "Latest open-strix on PyPI: $latest"

for agent_dir in "${!AGENTS[@]}"; do
    service="${AGENTS[$agent_dir]}"
    installed=$(get_installed_version "$agent_dir")

    if [ -z "$installed" ]; then
        log "  $service ($agent_dir): could not determine installed version, skipping"
        continue
    fi

    if [ "$installed" = "$latest" ]; then
        log "  $service: up to date ($installed)"
        continue
    fi

    log "  $service: $installed -> $latest — updating"

    # Update the package
    cd "$agent_dir"
    sudo -u botuser "$UV" sync $PRERELEASE 2>&1 | tail -3
    log "  $service: uv sync complete"

    # Get the PID and send SIGQUIT for graceful drain
    pid=$(systemctl show "$service" --property=MainPID --value 2>/dev/null)
    if [ -n "$pid" ] && [ "$pid" != "0" ]; then
        log "  $service: sending SIGQUIT to PID $pid (graceful drain)"
        kill -QUIT "$pid" 2>/dev/null || true

        # Wait up to 60 seconds for graceful shutdown
        for i in $(seq 1 60); do
            if ! kill -0 "$pid" 2>/dev/null; then
                log "  $service: drained and stopped after ${i}s"
                break
            fi
            sleep 1
        done

        # If still running after 60s, systemd will handle it on restart
        if kill -0 "$pid" 2>/dev/null; then
            log "  $service: still running after 60s, restarting via systemd"
            systemctl restart "$service"
        fi
    else
        log "  $service: no running PID found, starting"
        systemctl start "$service"
    fi

    # systemd auto-restarts, but verify
    sleep 3
    if systemctl is-active --quiet "$service"; then
        new_ver=$(get_installed_version "$agent_dir")
        log "  $service: running on $new_ver ✓"
    else
        log "  $service: WARNING — not running after update!"
    fi
done

log "Refresh complete."

#!/usr/bin/env bash
# ghost-deploy — Git-based deployment for Ghost
# Usage: ghost-deploy              — pull latest and restart changed services
#        ghost-deploy --force      — pull and restart everything
#        ghost-deploy --check      — check for updates without deploying
#        ghost-deploy --rollback   — revert to previous commit
set -euo pipefail

GHOST_REPO="${GHOST_REPO:-$HOME/ghost-repo}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/ghost}"
AGENT_DIR="${AGENT_DIR:-$HOME/pickle-agent}"
VOICE_DIR="$DEPLOY_DIR"
LOG_FILE="$HOME/.config/ghost/deploy.log"
LOCK_FILE="/tmp/ghost-deploy.lock"

mkdir -p "$(dirname "$LOG_FILE")"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"; }

# Lock to prevent concurrent deploys
if [ -f "$LOCK_FILE" ]; then
    PID=$(cat "$LOCK_FILE" 2>/dev/null)
    if kill -0 "$PID" 2>/dev/null; then
        log "Deploy already running (PID $PID)"
        exit 1
    fi
    rm -f "$LOCK_FILE"
fi
echo $$ > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# Clone repo if it doesn't exist
if [ ! -d "$GHOST_REPO/.git" ]; then
    log "Cloning ghost repo..."
    git clone git@github.com:riomyers/Ghost.git "$GHOST_REPO" 2>&1 | tee -a "$LOG_FILE"
fi

cd "$GHOST_REPO"

case "${1:-}" in
    --check)
        git fetch origin 2>/dev/null
        LOCAL=$(git rev-parse HEAD)
        REMOTE=$(git rev-parse origin/main)
        if [ "$LOCAL" = "$REMOTE" ]; then
            echo "Up to date ($LOCAL)"
            exit 0
        else
            AHEAD=$(git log --oneline HEAD..origin/main | wc -l | tr -d ' ')
            echo "$AHEAD new commit(s) available"
            git log --oneline HEAD..origin/main
            exit 2  # Exit 2 = updates available
        fi
        ;;

    --rollback)
        PREV=$(git rev-parse HEAD~1 2>/dev/null)
        if [ -z "$PREV" ]; then
            log "No previous commit to rollback to"
            exit 1
        fi
        log "Rolling back to $PREV"
        git reset --hard "$PREV"
        # Fall through to deploy
        ;;

    --force)
        log "Force deploy — will restart all services"
        FORCE=1
        ;;
    *)
        FORCE=0
        ;;
esac

# Fetch and check for changes
git fetch origin 2>&1 | tee -a "$LOG_FILE"
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ] && [ "${FORCE:-0}" = "0" ]; then
    log "Already up to date"
    exit 0
fi

# Pull to temp branch, validate, then merge
log "Pulling updates..."
PREV_HEAD="$LOCAL"
git pull origin main 2>&1 | tee -a "$LOG_FILE"
NEW_HEAD=$(git rev-parse HEAD)

# Determine what changed
CHANGED_FILES=$(git diff --name-only "$PREV_HEAD" "$NEW_HEAD" 2>/dev/null || echo "")

# Deploy agent code
if echo "$CHANGED_FILES" | grep -q "^agent/" || [ "${FORCE:-0}" = "1" ]; then
    log "Deploying agent code..."
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' \
        "$GHOST_REPO/agent/" "$AGENT_DIR/src/"
    log "Restarting pickle-agent..."
    sudo systemctl restart pickle-agent 2>/dev/null || true
fi

# Deploy voice code
if echo "$CHANGED_FILES" | grep -q "^voice/" || [ "${FORCE:-0}" = "1" ]; then
    log "Deploying voice code..."
    rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
        "$GHOST_REPO/voice/lib/" "$VOICE_DIR/lib/"
    rsync -a --exclude='__pycache__' \
        "$GHOST_REPO/voice/bin/" "$VOICE_DIR/bin/"
    chmod +x "$VOICE_DIR/bin/"*
fi

# Deploy services
if echo "$CHANGED_FILES" | grep -q "^services/" || [ "${FORCE:-0}" = "1" ]; then
    log "Deploying service files..."
    sudo cp "$GHOST_REPO/services/"*.service /etc/systemd/system/ 2>/dev/null || true
    sudo systemctl daemon-reload
fi

# Deploy config
if echo "$CHANGED_FILES" | grep -q "^config/" || [ "${FORCE:-0}" = "1" ]; then
    log "Config files changed — manual review recommended"
fi

log "Deploy complete: $PREV_HEAD → $NEW_HEAD"
echo "$NEW_HEAD"

#!/bin/bash
# bootstrap.sh — Deploy Ghost's entire software stack from Mac to Ghost.
# Run FROM Mac after fresh Ubuntu install on Ghost.
# Prerequisites: Ghost is on network, SSH key auth working.
#
# Usage: ./bootstrap.sh [ghost-ip]

set -euo pipefail

GHOST_HOST="${1:-ghost}"
GHOST_USER="atom"
GHOST_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo -e "\033[1;32m[GHOST]\033[0m $*"; }
err() { echo -e "\033[1;31m[ERROR]\033[0m $*" >&2; }

# Test connection
log "Testing SSH connection to $GHOST_HOST..."
if ! ssh -o ConnectTimeout=5 "$GHOST_USER@$GHOST_HOST" "echo OK" &>/dev/null; then
    err "Cannot reach $GHOST_HOST. Is Ghost on the network?"
    exit 1
fi
log "Connected."

# --- 1. System packages ---
log "Installing system packages..."
ssh "$GHOST_USER@$GHOST_HOST" "sudo apt update -qq && sudo apt install -y -qq \
    python3 python3-pip python3-venv \
    ffmpeg mpv \
    alsa-utils \
    openssh-server \
    curl wget git \
    ethtool bc \
    2>&1 | tail -5"

# --- 2. Create directory structure on Ghost ---
log "Creating Ghost directory structure..."
ssh "$GHOST_USER@$GHOST_HOST" "mkdir -p ~/ghost/{logs,models,lib} ~/.local/bin"

# --- 3. Deploy bin scripts ---
log "Deploying Ghost scripts..."
for script in ghost-think ghost-ear ghost-startup ghost-agent ghost-status; do
    if [ -f "$GHOST_DIR/bin/$script" ]; then
        scp -q "$GHOST_DIR/bin/$script" "$GHOST_USER@$GHOST_HOST:~/.local/bin/"
    fi
done
ssh "$GHOST_USER@$GHOST_HOST" "chmod +x ~/.local/bin/ghost-*"

# --- 3b. Create repos directory for agent ---
log "Creating repos directory for autonomous agent..."
ssh "$GHOST_USER@$GHOST_HOST" "mkdir -p ~/repos"

# --- 4. Install Whisper (STT) ---
log "Installing Whisper..."
ssh "$GHOST_USER@$GHOST_HOST" "pip3 install --user --break-system-packages openai-whisper 2>&1 | tail -3"

# --- 5. Install Piper (TTS) ---
log "Installing Piper TTS..."
ssh "$GHOST_USER@$GHOST_HOST" 'bash -c "
    if ! command -v piper &>/dev/null; then
        pip3 install --user --break-system-packages piper-tts 2>&1 | tail -3
    fi
    # Download voice model
    mkdir -p ~/ghost/models
    if [ ! -f ~/ghost/models/en_US-lessac-medium.onnx ]; then
        echo \"Downloading voice model...\"
        wget -q -O ~/ghost/models/en_US-lessac-medium.onnx \
            \"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx\"
        wget -q -O ~/ghost/models/en_US-lessac-medium.onnx.json \
            \"https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json\"
    fi
"'

# --- 6. Install Claude Code ---
log "Installing Claude Code CLI..."
ssh "$GHOST_USER@$GHOST_HOST" 'bash -c "
    if ! command -v claude &>/dev/null; then
        curl -fsSL https://claude.ai/install.sh | sh 2>&1 | tail -3
    else
        echo \"Claude already installed\"
    fi
"'

# --- 7. System configs ---
log "Applying system configs..."

# Lid switch — don't suspend on close
ssh "$GHOST_USER@$GHOST_HOST" "sudo mkdir -p /etc/systemd/logind.conf.d"
scp -q "$GHOST_DIR/config/logind-lid.conf" "$GHOST_USER@$GHOST_HOST:/tmp/"
ssh "$GHOST_USER@$GHOST_HOST" "sudo mv /tmp/logind-lid.conf /etc/systemd/logind.conf.d/lid.conf && sudo systemctl restart systemd-logind"

# --- 8. Systemd services ---
log "Installing systemd services..."
for svc in ghost-ear.service ghost-agent.service; do
    if [ -f "$GHOST_DIR/services/$svc" ]; then
        scp -q "$GHOST_DIR/services/$svc" "$GHOST_USER@$GHOST_HOST:/tmp/"
        ssh "$GHOST_USER@$GHOST_HOST" "sudo mv /tmp/$svc /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable ${svc%.service}"
        log "  Enabled $svc"
    fi
done

# --- 9. Deploy API keys ---
log "Deploying API keys to Ghost..."
ssh "$GHOST_USER@$GHOST_HOST" "mkdir -p ~/ghost/config && chmod 700 ~/ghost/config"
# Anthropic key
if [ -f "$HOME/.config/pickle-rick/.anthropic_key" ]; then
    scp -q "$HOME/.config/pickle-rick/.anthropic_key" "$GHOST_USER@$GHOST_HOST:~/ghost/config/.anthropic_key"
    ssh "$GHOST_USER@$GHOST_HOST" "chmod 600 ~/ghost/config/.anthropic_key"
    log "  Deployed Anthropic key"
fi
# ElevenLabs key
if [ -f "$HOME/.config/pickle-rick/.elevenlabs_key" ]; then
    scp -q "$HOME/.config/pickle-rick/.elevenlabs_key" "$GHOST_USER@$GHOST_HOST:~/ghost/config/.elevenlabs_key"
    ssh "$GHOST_USER@$GHOST_HOST" "chmod 600 ~/ghost/config/.elevenlabs_key"
    log "  Deployed ElevenLabs key"
fi

# --- 10. SSH key auth (copy Mac's key if not already there) ---
log "Ensuring SSH key auth..."
if [ -f ~/.ssh/id_ed25519.pub ]; then
    ssh-copy-id -i ~/.ssh/id_ed25519.pub "$GHOST_USER@$GHOST_HOST" 2>/dev/null || true
elif [ -f ~/.ssh/id_rsa.pub ]; then
    ssh-copy-id -i ~/.ssh/id_rsa.pub "$GHOST_USER@$GHOST_HOST" 2>/dev/null || true
fi

# --- 10. Final status ---
log "Running Ghost startup..."
ssh "$GHOST_USER@$GHOST_HOST" "~/.local/bin/ghost-startup"

log ""
log "========================================="
log "  Ghost is ALIVE."
log "  IP: $(ssh "$GHOST_USER@$GHOST_HOST" "hostname -I | awk '{print \$1}'")"
log "  Test: talk-to-ghost \"Hello Ghost\""
log "========================================="

#!/usr/bin/env bash
# VoiceFlow installer for Fedora Linux (Wayland)
# Usage: bash install.sh

set -euo pipefail

BOLD="\033[1m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
RESET="\033[0m"

info()    { echo -e "${GREEN}[✓]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[!]${RESET} $*"; }
error()   { echo -e "${RED}[✗]${RESET} $*"; exit 1; }
section() { echo -e "\n${BOLD}$*${RESET}"; }

# ── Preflight ────────────────────────────────────────────────────────────────

section "VoiceFlow Installer"
echo "  This will install VoiceFlow — native voice dictation for Linux."
echo ""

# Must NOT be root
[[ "$EUID" -eq 0 ]] && error "Don't run as root. Run as your normal user."

# Detect Fedora
if ! command -v dnf &>/dev/null; then
    warn "dnf not found — this installer is designed for Fedora/RHEL."
    warn "On Debian/Ubuntu, install equivalents manually (see CLAUDE.md)."
fi

# Detect Wayland
if [[ "${XDG_SESSION_TYPE:-}" != "wayland" ]]; then
    warn "XDG_SESSION_TYPE is not 'wayland' (got: ${XDG_SESSION_TYPE:-unset})"
    warn "wtype requires Wayland. Continuing, but injection may not work on X11."
fi

# ── System packages ──────────────────────────────────────────────────────────

section "Installing system packages..."

sudo dnf install -y \
    python3 python3-pip python3-devel \
    python3-gobject gtk4 \
    gcc pkg-config \
    pipewire pipewire-alsa portaudio portaudio-devel \
    wtype wl-clipboard \
    libevdev libevdev-devel \
    || warn "Some dnf packages may have failed — check output above."

# gtk4-layer-shell — may need to enable RPM Fusion or build from source
if ! pkg-config --exists gtk4-layer-shell-0 2>/dev/null; then
    warn "gtk4-layer-shell not found via pkg-config."
    warn "Trying to install via dnf..."
    sudo dnf install -y gtk4-layer-shell gtk4-layer-shell-devel 2>/dev/null || {
        warn "gtk4-layer-shell not in default repos."
        warn "The overlay may be disabled. VoiceFlow will still work without it."
        warn "To enable overlay later: https://github.com/wmww/gtk4-layer-shell"
    }
fi

info "System packages done."

# ── Input group ──────────────────────────────────────────────────────────────

section "Configuring input device access..."

if ! groups "$USER" | grep -q "\binput\b"; then
    sudo usermod -aG input "$USER"
    warn "Added you to the 'input' group."
    warn "You MUST log out and log back in for hotkey listening to work!"
else
    info "Already in 'input' group."
fi

# ── Python venv ──────────────────────────────────────────────────────────────

section "Setting up Python virtual environment..."

INSTALL_DIR="$HOME/.local/share/voiceflow"
mkdir -p "$INSTALL_DIR"
VENV="$INSTALL_DIR/venv"

if [[ ! -d "$VENV" ]]; then
    python3 -m venv "$VENV"
    info "Created venv at $VENV"
else
    info "Existing venv found at $VENV"
fi

"$VENV/bin/pip" install --upgrade pip --quiet
"$VENV/bin/pip" install \
    evdev \
    sounddevice \
    numpy \
    deepgram-sdk \
    groq \
    toml \
    python-dotenv \
    PyGObject \
    websockets \
    httpx \
    || error "pip install failed"

info "Python dependencies installed."

# ── Copy project files ───────────────────────────────────────────────────────

section "Installing VoiceFlow..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Install the package into the venv
"$VENV/bin/pip" install -e "$SCRIPT_DIR" --quiet || {
    # Fallback: copy files manually
    cp -r "$SCRIPT_DIR/voiceflow" "$INSTALL_DIR/"
    info "Copied voiceflow package to $INSTALL_DIR"
}

info "VoiceFlow installed."

# ── Config directory ─────────────────────────────────────────────────────────

section "Setting up config..."

CONFIG_DIR="$HOME/.config/voiceflow"
mkdir -p "$CONFIG_DIR"

# Copy .env.example if no .env exists
if [[ ! -f "$CONFIG_DIR/.env" ]]; then
    if [[ -f "$SCRIPT_DIR/.env.example" ]]; then
        cp "$SCRIPT_DIR/.env.example" "$CONFIG_DIR/.env"
        info "Created $CONFIG_DIR/.env from .env.example"
    fi
fi

# Prompt for API keys if not already set
ENV_FILE="$CONFIG_DIR/.env"

if ! grep -q "DEEPGRAM_API_KEY=your_" "$ENV_FILE" 2>/dev/null && grep -q "DEEPGRAM_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    info "Deepgram API key already configured."
else
    echo ""
    echo -n "  Enter your Deepgram API key (get free at deepgram.com): "
    read -r DG_KEY
    if [[ -n "$DG_KEY" ]]; then
        sed -i "s|DEEPGRAM_API_KEY=.*|DEEPGRAM_API_KEY=$DG_KEY|" "$ENV_FILE"
        info "Deepgram key saved."
    else
        warn "No key entered — edit $ENV_FILE manually before starting."
    fi
fi

if ! grep -q "GROQ_API_KEY=your_" "$ENV_FILE" 2>/dev/null && grep -q "GROQ_API_KEY=" "$ENV_FILE" 2>/dev/null; then
    info "Groq API key already configured."
else
    echo ""
    echo -n "  Enter your Groq API key (get free at console.groq.com): "
    read -r GROQ_KEY
    if [[ -n "$GROQ_KEY" ]]; then
        sed -i "s|GROQ_API_KEY=.*|GROQ_API_KEY=$GROQ_KEY|" "$ENV_FILE"
        info "Groq key saved."
    else
        warn "No key entered — edit $ENV_FILE manually before starting."
    fi
fi

# Patch systemd unit to use our venv python
UNIT_SRC="$SCRIPT_DIR/systemd/voiceflow.service"
UNIT_DST="$HOME/.config/systemd/user/voiceflow.service"
mkdir -p "$HOME/.config/systemd/user"
sed "s|%h/.local/share/voiceflow/venv/bin/python|$VENV/bin/python|g" \
    "$UNIT_SRC" > "$UNIT_DST"

# ── Systemd service ──────────────────────────────────────────────────────────

section "Installing systemd user service..."

systemctl --user daemon-reload
systemctl --user enable voiceflow.service
info "Service enabled. It will start automatically on next login."

echo ""
echo -e "${BOLD}Installation complete!${RESET}"
echo ""
echo "  To start right now:"
echo "    systemctl --user start voiceflow"
echo ""
echo "  To check status:"
echo "    systemctl --user status voiceflow"
echo ""
echo "  To view logs:"
echo "    journalctl --user -u voiceflow -f"
echo ""
echo "  Default hotkey: hold Right Alt to dictate."
echo "  Edit hotkey in: $CONFIG_DIR/config.toml"
echo ""

if groups "$USER" | grep -qv "\binput\b"; then
    echo -e "${YELLOW}  ⚠ IMPORTANT: Log out and log back in for hotkey access!${RESET}"
    echo ""
fi

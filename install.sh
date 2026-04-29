#!/usr/bin/env bash
# xev-whisprflow installer for Fedora/RHEL (Wayland)
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/xevrion/xev-whisprflow/main/install.sh)
#    or: git clone ... && cd xev-whisprflow && bash install.sh

set -euo pipefail

GREEN="\033[0;32m"
YELLOW="\033[0;33m"
RED="\033[0;31m"
BOLD="\033[1m"
RESET="\033[0m"

ok()   { echo -e "${GREEN}ok${RESET}  $*"; }
warn() { echo -e "${YELLOW}!${RESET}   $*"; }
die()  { echo -e "${RED}err${RESET} $*"; exit 1; }
step() { echo -e "\n${BOLD}$*${RESET}"; }

echo -e "${BOLD}xev-whisprflow${RESET} installer"
echo ""

[[ "$EUID" -eq 0 ]] && die "Run as your normal user, not root."
command -v dnf &>/dev/null || warn "dnf not found — this script targets Fedora/RHEL. Adapt manually for other distros."

# --- System packages ---
step "Installing system packages"
sudo dnf install -y \
    python3 python3-gobject gtk4 \
    wtype wl-clipboard \
    pipewire pipewire-alsa portaudio portaudio-devel \
    libevdev libevdev-devel gcc pkg-config \
    || warn "Some packages may have failed — check above."

if ! pkg-config --exists gtk4-layer-shell-0 2>/dev/null; then
    sudo dnf install -y gtk4-layer-shell gtk4-layer-shell-devel 2>/dev/null \
        || warn "gtk4-layer-shell not found — overlay will work but won't float above all windows."
fi
ok "System packages"

# --- input group ---
step "Input device access"
if ! groups "$USER" | grep -q '\binput\b'; then
    sudo usermod -aG input "$USER"
    warn "Added to 'input' group. Log out and back in before using the hotkey."
else
    ok "Already in 'input' group"
fi

# --- uv ---
step "Checking uv"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version 2>/dev/null | head -1)"

# --- Source directory ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/xev-whisprflow"
mkdir -p "$INSTALL_DIR"

# If run via curl (no pyproject.toml in script dir), clone the repo
if [[ ! -f "$SCRIPT_DIR/pyproject.toml" ]]; then
    step "Cloning repo"
    git clone https://github.com/xevrion/xev-whisprflow.git "$INSTALL_DIR/src"
    SCRIPT_DIR="$INSTALL_DIR/src"
    ok "Cloned to $INSTALL_DIR/src"
fi

# --- Python venv ---
step "Setting up Python environment"
VENV="$INSTALL_DIR/venv"
uv venv --system-site-packages --python /usr/bin/python3 "$VENV" 2>/dev/null \
    || uv venv --system-site-packages "$VENV"
uv pip install --python "$VENV/bin/python" -e "$SCRIPT_DIR" 2>/dev/null \
    || uv pip install --python "$VENV/bin/python" --no-build-isolation -e "$SCRIPT_DIR"
ok "Dependencies installed"

# --- Symlink CLI ---
step "Installing CLI"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
ln -sf "$VENV/bin/xev-whisprflow" "$BIN/xev-whisprflow"
if ! echo "$PATH" | grep -q "$BIN"; then
    warn "$BIN not in PATH. Add to your shell config: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
ok "xev-whisprflow command linked"

# --- Config ---
step "Setting up config"
CONFIG_DIR="$HOME/.config/xev-whisprflow"
mkdir -p "$CONFIG_DIR"
ENV_FILE="$CONFIG_DIR/.env"

[[ ! -f "$ENV_FILE" ]] && cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"

if grep -q "your_deepgram_api_key_here" "$ENV_FILE"; then
    echo ""
    echo -n "  Deepgram API key (free at deepgram.com, Enter to skip): "
    read -r DG_KEY
    [[ -n "$DG_KEY" ]] && sed -i "s|DEEPGRAM_API_KEY=.*|DEEPGRAM_API_KEY=$DG_KEY|" "$ENV_FILE"
fi

if grep -q "your_groq_api_key_here" "$ENV_FILE"; then
    echo -n "  Groq API key (free at console.groq.com, Enter to skip): "
    read -r GROQ_KEY
    [[ -n "$GROQ_KEY" ]] && sed -i "s|GROQ_API_KEY=.*|GROQ_API_KEY=$GROQ_KEY|" "$ENV_FILE"
fi
ok "Config at $CONFIG_DIR"

# --- Systemd service ---
step "Installing systemd service"
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"
sed "s|%h/.local/share/xev-whisprflow/venv/bin/xev-whisprflow|$VENV/bin/xev-whisprflow|g" \
    "$SCRIPT_DIR/systemd/xev-whisprflow.service" > "$SYSTEMD_DIR/xev-whisprflow.service"
systemctl --user daemon-reload
systemctl --user enable xev-whisprflow.service
ok "Service installed and enabled"

# --- Done ---
echo ""
echo -e "${BOLD}Done.${RESET}"
echo ""
echo "  Start now:      xev-whisprflow"
echo "  Start service:  systemctl --user start xev-whisprflow"
echo "  Dashboard:      http://localhost:7878"
echo "  Logs:           journalctl --user -u xev-whisprflow -f"
echo ""
echo "  Default hotkey: hold Right Alt to dictate."
echo "  Edit config:    $CONFIG_DIR/config.toml"
echo ""
groups "$USER" | grep -q '\binput\b' || echo -e "${YELLOW}  Log out and back in for the hotkey to work.${RESET}\n"

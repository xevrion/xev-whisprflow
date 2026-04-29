#!/usr/bin/env bash
# xev-whisprflow installer
# Supports: Fedora, Ubuntu/Debian, Arch Linux — Wayland and X11
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/xevrion/xev-whisprflow/main/install.sh)
#    or: git clone https://github.com/xevrion/xev-whisprflow && cd xev-whisprflow && bash install.sh

set -euo pipefail

GREEN="\033[0;32m"; YELLOW="\033[0;33m"; RED="\033[0;31m"; BOLD="\033[1m"; RESET="\033[0m"
ok()   { echo -e "${GREEN}ok${RESET}  $*"; }
warn() { echo -e "${YELLOW}!${RESET}   $*"; }
die()  { echo -e "${RED}err${RESET} $*"; exit 1; }
step() { echo -e "\n${BOLD}-- $* --${RESET}"; }

echo -e "\n${BOLD}xev-whisprflow${RESET} installer\n"
[[ "$EUID" -eq 0 ]] && die "Run as your normal user, not root."

# --- Detect distro and session ---
SESSION="${XDG_SESSION_TYPE:-unknown}"
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    DISTRO="${ID:-unknown}"
    DISTRO_LIKE="${ID_LIKE:-}"
else
    DISTRO="unknown"
    DISTRO_LIKE=""
fi

is_fedora()  { [[ "$DISTRO" == "fedora" ]] || echo "$DISTRO_LIKE" | grep -q "fedora"; }
is_debian()  { [[ "$DISTRO" == "ubuntu" || "$DISTRO" == "debian" ]] || echo "$DISTRO_LIKE" | grep -q "debian"; }
is_arch()    { [[ "$DISTRO" == "arch" ]] || echo "$DISTRO_LIKE" | grep -q "arch"; }

echo "  distro:  $DISTRO"
echo "  session: $SESSION"
echo ""

# --- System packages ---
step "System packages"

install_fedora() {
    sudo dnf install -y \
        python3 python3-gobject gtk4 \
        pipewire pipewire-alsa portaudio portaudio-devel \
        libevdev libevdev-devel gcc pkg-config

    # Wayland tools
    sudo dnf install -y wtype wl-clipboard 2>/dev/null || warn "wtype/wl-clipboard not found — X11 fallback (xdotool) will be used"

    # X11 tools
    sudo dnf install -y xdotool xclip 2>/dev/null || true

    # GTK layer shell
    sudo dnf install -y gtk4-layer-shell gtk4-layer-shell-devel 2>/dev/null \
        || warn "gtk4-layer-shell not in repos — overlay won't float above all windows"
}

install_debian() {
    sudo apt-get update -qq
    sudo apt-get install -y \
        python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 \
        pipewire pipewire-audio libportaudio2 portaudio19-dev \
        libevdev-dev gcc pkg-config

    # Wayland tools
    sudo apt-get install -y wtype wl-clipboard 2>/dev/null || warn "wtype not found — X11 fallback will be used"

    # X11 tools
    sudo apt-get install -y xdotool xclip 2>/dev/null || true

    # GTK layer shell (Ubuntu 24.04+ has it)
    sudo apt-get install -y libgtk4-layer-shell-dev 2>/dev/null \
        || warn "gtk4-layer-shell not available — overlay won't float above all windows"
}

install_arch() {
    sudo pacman -Sy --noconfirm --needed \
        python python-gobject gtk4 \
        pipewire pipewire-alsa portaudio \
        libevdev gcc pkgconf

    # Wayland tools
    sudo pacman -Sy --noconfirm --needed wtype wl-clipboard 2>/dev/null || warn "wtype not found — X11 fallback will be used"

    # X11 tools
    sudo pacman -Sy --noconfirm --needed xdotool xclip 2>/dev/null || true

    # GTK layer shell
    sudo pacman -Sy --noconfirm --needed gtk4-layer-shell 2>/dev/null \
        || warn "gtk4-layer-shell not available — overlay won't float above all windows"
}

if is_fedora; then
    install_fedora
elif is_debian; then
    install_debian
elif is_arch; then
    install_arch
else
    warn "Unknown distro ($DISTRO). Install these manually:"
    warn "  python3, python3-gobject, gtk4, pipewire, portaudio-devel, libevdev-devel"
    warn "  Wayland: wtype, wl-clipboard"
    warn "  X11:     xdotool, xclip"
fi
ok "System packages"

# --- input group ---
step "Input device access"
if ! groups "$USER" | grep -q '\binput\b'; then
    sudo usermod -aG input "$USER"
    warn "Added to 'input' group. You must log out and back in before the hotkey works."
else
    ok "Already in 'input' group"
fi

# --- uv ---
step "uv"
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi
ok "uv $(uv --version 2>/dev/null | awk '{print $2}')"

# --- Source ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="$HOME/.local/share/xev-whisprflow"
mkdir -p "$INSTALL_DIR"

if [[ ! -f "$SCRIPT_DIR/pyproject.toml" ]]; then
    step "Cloning repo"
    git clone https://github.com/xevrion/xev-whisprflow.git "$INSTALL_DIR/src"
    SCRIPT_DIR="$INSTALL_DIR/src"
    ok "Cloned"
fi

# --- Python venv ---
step "Python environment"
VENV="$INSTALL_DIR/venv"
uv venv --system-site-packages --python /usr/bin/python3 "$VENV" 2>/dev/null \
    || uv venv --system-site-packages "$VENV"
uv pip install --python "$VENV/bin/python" -e "$SCRIPT_DIR" 2>/dev/null \
    || uv pip install --python "$VENV/bin/python" --no-build-isolation -e "$SCRIPT_DIR"
ok "Dependencies installed"

# --- CLI ---
step "CLI"
BIN="$HOME/.local/bin"
mkdir -p "$BIN"
ln -sf "$VENV/bin/xev-whisprflow" "$BIN/xev-whisprflow"
if ! echo "$PATH" | grep -q "$BIN"; then
    warn "Add to your shell config: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi
ok "xev-whisprflow command installed"

# --- Config ---
step "Config"
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

# --- Systemd ---
step "Systemd service"
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
echo "  Run now:        xev-whisprflow"
echo "  Start service:  systemctl --user start xev-whisprflow"
echo "  Dashboard:      http://localhost:7878"
echo "  Logs:           journalctl --user -u xev-whisprflow -f"
echo ""
echo "  Default hotkey: hold Right Alt to dictate."
echo "  Config:         $CONFIG_DIR/config.toml"
echo ""
groups "$USER" | grep -q '\binput\b' || echo -e "${YELLOW}  Remember to log out and back in for the hotkey to work.${RESET}\n"

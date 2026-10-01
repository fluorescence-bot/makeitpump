#!/usr/bin/env bash
# One-shot setup + launch for the RainMixer GUI on macOS and Linux.
#
#   ./run.sh          install anything missing, build the frontend, open the GUI
#   ./run.sh --clean  wipe .venv, node_modules and dist first, then do the above
#
# Installs (only if missing): Homebrew (macOS), uv, Python (via uv), Node.js/npm,
# and on Linux the GTK/WebKit libraries the GUI window needs.
# Safe to re-run: steps that are already done are skipped.

set -euo pipefail

cd "$(dirname "$0")"

say()  { printf '\033[1;35m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

OS="$(uname -s)"
NODE_MIN=18

if [[ "${1:-}" == "--clean" ]]; then
    shift
    say "Cleaning previous install"
    rm -rf .venv web/node_modules web/dist
fi

# Tools installed by this script land here; make sure we can find them.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# --- Tool installers --------------------------------------------------------

fetch() {
    if have curl; then curl -fsSL "$1"
    elif have wget; then wget -qO- "$1"
    else die "Need curl or wget to download $1"
    fi
}

install_brew() {
    have brew && return
    say "Installing Homebrew (you may be asked for your Mac password)"
    /bin/bash -c "$(fetch https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
        if [[ -x "$b" ]]; then eval "$("$b" shellenv)"; fi
    done
    have brew || die "Homebrew install failed. See https://brew.sh, then re-run."
}

install_uv() {
    have uv && return
    say "Installing uv (Python package manager)"
    fetch https://astral.sh/uv/install.sh | sh
    have uv || die "uv install failed. See https://docs.astral.sh/uv/, then re-run."
}

install_linux_deps() {
    # pywebview on Linux prefers GTK + WebKit through PyGObject ("gi"), which
    # comes from the system package manager, not pip. If that's unavailable it
    # falls back to Qt (PyQt6, installed from requirements.txt), which only
    # needs libxcb-cursor from the system. Both are best-effort here.
    if /usr/bin/python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null \
        || /usr/bin/python3 -c 'import gi; gi.require_version("WebKit2", "4.0")' 2>/dev/null; then
        return
    fi
    say "Installing GTK/WebKit for the GUI window (needs sudo)"
    if have apt-get; then
        sudo apt-get update || true
        sudo apt-get install -y curl python3 libxcb-cursor0 || true
        sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
            || sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0 \
            || true
    elif have dnf; then
        sudo dnf install -y python3 xcb-util-cursor || true
        sudo dnf install -y python3-gobject gtk3 webkit2gtk4.1 \
            || sudo dnf install -y python3-gobject gtk3 webkit2gtk3 \
            || true
    elif have pacman; then
        sudo pacman -S --needed --noconfirm python xcb-util-cursor || true
        sudo pacman -S --needed --noconfirm python-gobject gtk3 webkit2gtk-4.1 || true
    else
        say "Unknown package manager; skipping GTK/WebKit (the GUI will use Qt instead)."
    fi
}

node_ok() {
    have node && have npm || return 1
    local major
    major="$(node -p 'process.versions.node.split(".")[0]')"
    [[ "$major" -ge "$NODE_MIN" ]]
}

install_node() {
    node_ok && return
    say "Installing Node.js + npm (>= $NODE_MIN)"
    if [[ "$OS" == "Darwin" ]]; then
        install_brew
        brew install node
    elif have apt-get; then
        # Distro nodejs is often too old for Vite, so use NodeSource's LTS.
        fetch https://deb.nodesource.com/setup_lts.x | sudo -E bash -
        sudo apt-get install -y nodejs
    elif have dnf; then
        sudo dnf install -y nodejs npm
    elif have pacman; then
        sudo pacman -S --needed --noconfirm nodejs npm
    else
        die "Install Node.js $NODE_MIN+ from https://nodejs.org, then re-run."
    fi
    hash -r
    node_ok || die "Node.js $NODE_MIN+ still not found (have: $(node -v 2>/dev/null || echo none)). Install it from https://nodejs.org and re-run."
}

# --- System dependencies ----------------------------------------------------

case "$OS" in
    Darwin)
        # uv downloads its own Python if the Mac doesn't have a suitable one.
        PYTHON=3.12
        VENV_FLAGS=()
        ;;
    Linux)
        install_linux_deps
        # Use the system Python so the venv can see the apt-installed "gi".
        PYTHON=/usr/bin/python3
        VENV_FLAGS=(--system-site-packages)
        ;;
    *)
        die "Unsupported OS: $OS (this script is for macOS and Linux)."
        ;;
esac

install_uv
install_node

# --- Python environment -----------------------------------------------------

if [[ ! -x .venv/bin/python ]]; then
    say "Creating Python virtual environment"
    uv venv --no-project --python "$PYTHON" ${VENV_FLAGS[@]+"${VENV_FLAGS[@]}"} .venv
fi

if [[ ! -f .venv/.installed || requirements.txt -nt .venv/.installed ]]; then
    say "Installing Python packages"
    uv pip install --python .venv/bin/python -r requirements.txt
    touch .venv/.installed
fi

# --- Frontend ---------------------------------------------------------------

if [[ ! -d web/node_modules || web/package-lock.json -nt web/node_modules ]]; then
    say "Installing frontend packages"
    (cd web && npm install --no-fund --no-audit)
    touch web/node_modules
fi

if [[ ! -f web/dist/index.html ]] \
    || [[ -n "$(find web/src web/index.html web/package.json -newer web/dist/index.html 2>/dev/null | head -n 1)" ]]; then
    say "Building frontend"
    (cd web && npm run build)
fi

# --- Launch -----------------------------------------------------------------

say "Starting RainMixer"
exec .venv/bin/python app.py "$@"

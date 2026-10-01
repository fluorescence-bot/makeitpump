#!/usr/bin/env bash
# One-shot setup + launch for the RainMixer GUI on macOS and Linux.
#
#   ./run.sh          install anything missing, build the frontend, open the GUI
#   ./run.sh --clean  wipe .venv, node_modules and dist first, then do the above
#
# Detects the OS first, then runs that platform's setup:
#   macOS          Homebrew -> Node.js/npm
#   Debian/Ubuntu  apt      -> Node.js/npm (NodeSource), GTK/WebKit, Qt libs
#   Fedora/RHEL    dnf      -> Node.js/npm, GTK/WebKit, Qt libs
#   Arch           pacman   -> Node.js/npm, GTK/WebKit, Qt libs
# then on every platform: uv -> Python venv + packages -> frontend build -> GUI.
# Safe to re-run: steps that are already done are skipped.

set -euo pipefail

cd "$(dirname "$0")"

say()  { printf '\033[1;35m==> %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

fetch() {
    if have curl; then curl -fsSL "$1"
    elif have wget; then wget -qO- "$1"
    else die "Need curl or wget to download $1"
    fi
}

NODE_MIN=18

node_ok() {
    have node && have npm || return 1
    [[ "$(node -p 'process.versions.node.split(".")[0]')" -ge "$NODE_MIN" ]]
}

gtk_ok() {
    /usr/bin/python3 -c 'import gi; gi.require_version("WebKit2", "4.1")' 2>/dev/null \
        || /usr/bin/python3 -c 'import gi; gi.require_version("WebKit2", "4.0")' 2>/dev/null
}

if [[ "${1:-}" == "--clean" ]]; then
    shift
    say "Cleaning previous install"
    rm -rf .venv web/node_modules web/dist
fi

# Tools installed by this script land here; make sure we can find them.
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# --- 1. Detect the platform -------------------------------------------------

detect_platform() {
    case "$(uname -s)" in
        Darwin) echo macos; return ;;
        Linux)  ;;
        *)      echo unsupported; return ;;
    esac

    local id="" like=""
    if [[ -r /etc/os-release ]]; then
        # shellcheck disable=SC1091
        id="$(. /etc/os-release; echo "${ID:-}")"
        like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
    fi
    case " $id $like " in
        *" debian "*|*" ubuntu "*)                   echo debian ;;
        *" fedora "*|*" rhel "*|*" centos "*)         echo fedora ;;
        *" arch "*)                                   echo arch ;;
        *)
            # Unknown distro: fall back to whichever package manager exists.
            if   have apt-get; then echo debian
            elif have dnf;     then echo fedora
            elif have pacman;  then echo arch
            else                    echo linux-unknown
            fi ;;
    esac
}

PLATFORM="$(detect_platform)"
say "Detected platform: $PLATFORM"

# --- 2. Platform-specific setup ---------------------------------------------
# Each sets PYTHON (what uv builds the venv from) and VENV_FLAGS.
#
# On Linux pywebview prefers GTK + WebKit via PyGObject ("gi"), which only
# exists for the system Python, so the venv uses /usr/bin/python3 with
# --system-site-packages. If GTK can't be installed it falls back to Qt
# (PyQt6 from requirements.txt), which needs libxcb-cursor. GTK is best-effort.

setup_macos() {
    if ! have brew; then
        say "Installing Homebrew (you may be asked for your Mac password)"
        /bin/bash -c "$(fetch https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
            if [[ -x "$b" ]]; then eval "$("$b" shellenv)"; fi
        done
        have brew || die "Homebrew install failed. See https://brew.sh, then re-run."
    fi
    if ! node_ok; then
        say "Installing Node.js + npm"
        brew install node
    fi
    # uv downloads its own Python if the Mac doesn't have a suitable one.
    PYTHON=3.12
    VENV_FLAGS=()
}

setup_debian() {
    say "Installing system packages with apt (needs sudo)"
    sudo apt-get update || true
    sudo apt-get install -y curl python3 libxcb-cursor0
    if ! gtk_ok; then
        sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1 \
            || sudo apt-get install -y python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.0 \
            || say "GTK/WebKit not installed; the GUI will use Qt instead."
    fi
    if ! node_ok; then
        # Distro nodejs is often too old for Vite, so use NodeSource's LTS.
        say "Installing Node.js + npm (NodeSource LTS)"
        fetch https://deb.nodesource.com/setup_lts.x | sudo -E bash -
        sudo apt-get install -y nodejs
    fi
    PYTHON=/usr/bin/python3
    VENV_FLAGS=(--system-site-packages)
}

setup_fedora() {
    say "Installing system packages with dnf (needs sudo)"
    sudo dnf install -y curl python3 xcb-util-cursor
    if ! gtk_ok; then
        sudo dnf install -y python3-gobject gtk3 webkit2gtk4.1 \
            || sudo dnf install -y python3-gobject gtk3 webkit2gtk3 \
            || say "GTK/WebKit not installed; the GUI will use Qt instead."
    fi
    if ! node_ok; then
        say "Installing Node.js + npm"
        sudo dnf install -y nodejs npm
    fi
    PYTHON=/usr/bin/python3
    VENV_FLAGS=(--system-site-packages)
}

setup_arch() {
    say "Installing system packages with pacman (needs sudo)"
    sudo pacman -S --needed --noconfirm curl python xcb-util-cursor
    if ! gtk_ok; then
        sudo pacman -S --needed --noconfirm python-gobject gtk3 webkit2gtk-4.1 \
            || say "GTK/WebKit not installed; the GUI will use Qt instead."
    fi
    if ! node_ok; then
        say "Installing Node.js + npm"
        sudo pacman -S --needed --noconfirm nodejs npm
    fi
    PYTHON=/usr/bin/python3
    VENV_FLAGS=(--system-site-packages)
}

case "$PLATFORM" in
    macos)  setup_macos ;;
    debian) setup_debian ;;
    fedora) setup_fedora ;;
    arch)   setup_arch ;;
    linux-unknown)
        die "Couldn't find apt, dnf or pacman. Install Node.js $NODE_MIN+, curl and Python 3 yourself, then re-run." ;;
    *)
        die "Unsupported OS: $(uname -s) (this script is for macOS and Linux)." ;;
esac

hash -r
node_ok || die "Node.js $NODE_MIN+ not found (have: $(node -v 2>/dev/null || echo none)). Install it from https://nodejs.org and re-run."

# --- 3. Shared setup: uv + Python packages ----------------------------------

if ! have uv; then
    say "Installing uv (Python package manager)"
    fetch https://astral.sh/uv/install.sh | sh
    have uv || die "uv install failed. See https://docs.astral.sh/uv/, then re-run."
fi

if [[ ! -x .venv/bin/python ]]; then
    say "Creating Python virtual environment"
    uv venv --no-project --python "$PYTHON" ${VENV_FLAGS[@]+"${VENV_FLAGS[@]}"} .venv
fi

if [[ ! -f .venv/.installed || requirements.txt -nt .venv/.installed ]]; then
    say "Installing Python packages"
    uv pip install --python .venv/bin/python -r requirements.txt
    touch .venv/.installed
fi

# --- 4. Frontend ------------------------------------------------------------

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

# --- 5. Launch --------------------------------------------------------------

say "Starting RainMixer"
exec .venv/bin/python app.py "$@"

#!/bin/bash
#
# Personal AI OS - installer
#
# WHAT THIS DOES, IN ORDER
#   1.  checks your Mac is compatible
#   2.  finds a suitable Python
#   3.  creates the folders it needs
#   4.  copies the code somewhere permanent
#   5.  makes a private virtual environment
#   6.  optionally installs the Anthropic library
#   7.  creates the database
#   8.  puts the `assistant` command on your PATH
#   9.  optionally sets it to start when you log in
#  10.  runs a health check and tells you what to do next
#
# IT DOES NOT touch, move or read any of your documents. It never asks for
# Full Disk Access. Run it as yourself - NOT with sudo.
#
# Usage:   ./install.sh [--no-daemon] [--no-cloud] [--prefix DIR] [--yes]
#
# --allow-root exists only for containers and CI. Never use it on your own
# Mac: the assistant is designed to have exactly your permissions and no
# more, and running it as root would throw that away.

set -euo pipefail

# ---------------------------------------------------------------- appearance
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; OFF=""
fi

step()  { printf '\n%s==>%s %s%s%s\n' "$CYAN" "$OFF" "$BOLD" "$1" "$OFF"; }
info()  { printf '    %s\n' "$1"; }
good()  { printf '    %s%s%s\n' "$GREEN" "$1" "$OFF"; }
warn()  { printf '    %s%s%s\n' "$YELLOW" "$1" "$OFF"; }
die()   { printf '\n%sInstall stopped:%s %s\n\n' "$RED" "$OFF" "$1" >&2; exit 1; }

# ---------------------------------------------------------------- arguments
INSTALL_DAEMON=1
INSTALL_CLOUD=1
ASSUME_YES=0
ALLOW_ROOT=0
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
    case "$1" in
        --no-daemon) INSTALL_DAEMON=0 ;;
        --no-cloud)  INSTALL_CLOUD=0 ;;
        --yes|-y)    ASSUME_YES=1 ;;
        --allow-root) ALLOW_ROOT=1 ;;
        --prefix)    shift; PAIOS_HOME_OVERRIDE="${1:-}" ;;
        -h|--help)
            sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) die "Unknown option '$1'. Try --help." ;;
    esac
    shift
done

printf '%s\n' "$BOLD"
cat <<'BANNER'
  Personal AI OS
  a permissioned AI layer for your Mac
BANNER
printf '%s\n' "$OFF"

# ---------------------------------------------------------------- 1. system
step "Checking this computer"

if [ "$(id -u)" = "0" ]; then
    if [ "$ALLOW_ROOT" = "1" ]; then
        warn "Running as root because --allow-root was given."
        warn "This is only appropriate in a container or CI. On a real Mac the"
        warn "assistant should have exactly your permissions and no more."
    else
        die "Do not run this with sudo. The assistant must run as you, with your
    permissions and no more. Run:  ./install.sh

    (If this really is a container or CI, pass --allow-root.)"
    fi
fi

OS="$(uname -s)"
ARCH="$(uname -m)"
IS_MACOS=0
if [ "$OS" = "Darwin" ]; then
    IS_MACOS=1
    MACOS_VERSION="$(sw_vers -productVersion 2>/dev/null || echo unknown)"
    MACOS_MAJOR="${MACOS_VERSION%%.*}"
    good "macOS $MACOS_VERSION on $ARCH"
    if [ "$MACOS_MAJOR" != "unknown" ] && [ "$MACOS_MAJOR" -lt 12 ] 2>/dev/null; then
        die "macOS 12 (Monterey) or newer is required. This is $MACOS_VERSION."
    fi
    case "$ARCH" in
        arm64)  info "Apple silicon" ;;
        x86_64) info "Intel" ;;
        *)      warn "Unrecognised architecture '$ARCH' - continuing anyway." ;;
    esac
else
    warn "This is $OS, not macOS."
    warn "The core will work (files, search, memory, planning, permissions),"
    warn "but Calendar, Reminders, Notifications, Spotlight and the login"
    warn "service are macOS-only and will be unavailable."
fi

# ---------------------------------------------------------------- 2. python
step "Looking for Python 3.9 or newer"

PYTHON=""
for candidate in \
    /opt/homebrew/bin/python3 /usr/local/bin/python3 \
    "$(command -v python3.13 2>/dev/null || true)" \
    "$(command -v python3.12 2>/dev/null || true)" \
    "$(command -v python3.11 2>/dev/null || true)" \
    "$(command -v python3 2>/dev/null || true)" \
    /usr/bin/python3
do
    [ -n "$candidate" ] && [ -x "$candidate" ] || continue
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
        PYTHON="$candidate"
        break
    fi
done

[ -n "$PYTHON" ] || die "No Python 3.9+ was found.
    macOS normally provides one at /usr/bin/python3 once the Command Line
    Tools are installed. Install them with:

        xcode-select --install

    Or install a newer Python from https://www.python.org/downloads/"

PY_VERSION="$("$PYTHON" -c 'import platform; print(platform.python_version())')"
good "Using $PYTHON (Python $PY_VERSION)"

if [ "$PYTHON" = "/usr/bin/python3" ]; then
    warn "This is the Python that ships with Apple's developer tools. It works,"
    warn "but it is old and Apple may change it. A Homebrew or python.org"
    warn "Python is a better long-term choice:  brew install python"
fi

# SQLite must have FTS5, or search cannot work.
if ! "$PYTHON" - <<'PYEOF' 2>/dev/null
import sqlite3, sys
try:
    sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(a)")
except Exception:
    sys.exit(1)
PYEOF
then
    die "This Python's SQLite was built without the FTS5 extension, which the
    file search needs. Install Python from Homebrew (brew install python) or
    python.org and run this installer again."
fi
good "SQLite has full-text search (FTS5)"

# ---------------------------------------------------------------- 3. folders
step "Creating folders"

if [ "$IS_MACOS" = "1" ]; then
    DEFAULT_HOME="$HOME/Library/Application Support/PersonalAIOS"
else
    DEFAULT_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/personal-ai-os"
fi
PAIOS_HOME="${PAIOS_HOME_OVERRIDE:-${PAIOS_HOME:-$DEFAULT_HOME}}"
APP_DIR="$PAIOS_HOME/app"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$PAIOS_HOME" "$APP_DIR" "$PAIOS_HOME/db" "$PAIOS_HOME/run" "$BIN_DIR"
chmod 700 "$PAIOS_HOME" 2>/dev/null || true
good "$PAIOS_HOME"

# ---------------------------------------------------------------- 4. code
step "Installing the program"

rm -rf "$APP_DIR/src"
mkdir -p "$APP_DIR"
cp -R "$SOURCE_DIR/src" "$APP_DIR/src"
for extra in README.md ARCHITECTURE.md SECURITY.md PERMISSIONS.md \
             TROUBLESHOOTING.md UNINSTALL.md INSTALL.md DEVELOPMENT.md \
             uninstall.sh; do
    [ -f "$SOURCE_DIR/$extra" ] && cp "$SOURCE_DIR/$extra" "$APP_DIR/" || true
done
find "$APP_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
good "Code copied to $APP_DIR"

# ---------------------------------------------------------------- 5. venv
step "Creating a private Python environment"

VENV="$PAIOS_HOME/venv"
if [ ! -x "$VENV/bin/python" ]; then
    if "$PYTHON" -m venv "$VENV" 2>/dev/null; then
        good "Created $VENV"
    else
        warn "Could not create a virtual environment; using $PYTHON directly."
        warn "Everything still works, but optional libraries cannot be added."
        VENV=""
    fi
else
    good "Reusing $VENV"
fi

if [ -n "$VENV" ] && [ -x "$VENV/bin/python" ]; then
    RUNTIME="$VENV/bin/python"
else
    RUNTIME="$PYTHON"
fi

# ---------------------------------------------------------------- 6. library
if [ "$INSTALL_CLOUD" = "1" ] && [ -n "$VENV" ]; then
    step "Installing the Anthropic library (optional)"
    if "$VENV/bin/python" -m pip install --quiet --upgrade pip 2>/dev/null && \
       "$VENV/bin/python" -m pip install --quiet anthropic 2>/dev/null; then
        good "Installed - cloud models are available once you set an API key."
    else
        warn "Could not install it (no internet, or pip is blocked)."
        warn "Not a problem: the assistant works without it. You can either"
        warn "install it later:"
        warn "    $VENV/bin/pip install anthropic"
        warn "or use a fully local model with Ollama (https://ollama.com)."
    fi
fi

# ---------------------------------------------------------------- 7. database
step "Setting up the database"

export PAIOS_HOME
if PYTHONPATH="$APP_DIR/src" "$RUNTIME" - <<'PYEOF'
from assistant import config, db, paths
paths.ensure_dirs()
conn = db.connect()
config.init_default()
print("    schema version %d" % db.schema_version(conn))
PYEOF
then
    good "Database ready"
else
    die "Could not create the database at $PAIOS_HOME/db. Check that you can
    write to that folder."
fi

# ---------------------------------------------------------------- 8. command
step "Creating the 'assistant' command"

LAUNCHER="$BIN_DIR/assistant"
cat > "$LAUNCHER" <<LAUNCHEOF
#!/bin/bash
# Personal AI OS launcher - created by install.sh. Safe to delete.
export PAIOS_HOME="\${PAIOS_HOME:-$PAIOS_HOME}"
export PYTHONPATH="$APP_DIR/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$RUNTIME" -m assistant "\$@"
LAUNCHEOF
chmod 755 "$LAUNCHER"
good "$LAUNCHER"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        warn "$BIN_DIR is not on your PATH, so typing 'assistant' will not work yet."
        warn "Add this line to the end of your ~/.zshrc:"
        printf '\n        %sexport PATH="$HOME/.local/bin:$PATH"%s\n\n' "$BOLD" "$OFF"
        warn "Then open a new Terminal window, or run:  source ~/.zshrc"
        ;;
esac

# ---------------------------------------------------------------- 9. service
if [ "$INSTALL_DAEMON" = "1" ] && [ "$IS_MACOS" = "1" ]; then
    step "Setting it to start when you log in"
    if PYTHONPATH="$APP_DIR/src" PAIOS_HOME="$PAIOS_HOME" "$RUNTIME" - <<PYEOF
from assistant.macos import launchd
import pathlib, sys
result = launchd.install(python="$RUNTIME", app_dir=pathlib.Path("$APP_DIR"))
print("    " + ("installed" if result["installed"] else "not installed: " + result["error"]))
sys.exit(0 if result["installed"] else 1)
PYEOF
    then
        good "The background service will start automatically at login."
    else
        warn "Could not register the login service. This is optional - the"
        warn "'assistant' command works fine without it. You can retry with:"
        warn "    assistant daemon install"
    fi
elif [ "$INSTALL_DAEMON" = "1" ]; then
    info "Skipping the login service (macOS only)."
    info "Start it manually when you want it:  assistant daemon start"
fi

# ---------------------------------------------------------------- 10. verify
step "Checking everything works"
PYTHONPATH="$APP_DIR/src" PAIOS_HOME="$PAIOS_HOME" "$RUNTIME" -m assistant doctor || true

# ---------------------------------------------------------------- done
printf '\n%s%s%s\n' "$BOLD" "  Installed." "$OFF"
cat <<NEXT

  ${BOLD}What to do next${OFF}

  1. ${CYAN}Give it something to look at.${OFF} It can currently read NOTHING.
         assistant permissions grant ~/Documents
         assistant permissions grant ~/Downloads --mode readwrite

  2. ${CYAN}Build the file catalogue.${OFF}
         assistant index build

  3. ${CYAN}Give it a brain${OFF} (pick one):
         export ANTHROPIC_API_KEY=sk-ant-...      # cloud, most capable
         # or install Ollama from https://ollama.com for a fully local model,
         # then:  assistant config set ai.roles.reasoning ollama

  4. ${CYAN}Try it.${OFF}
         assistant status
         assistant ask "what is in my Documents folder?"

  ${DIM}Everything it does is recorded:   assistant logs
  Nothing risky happens without you:  assistant approvals
  Emergency stop, any time:           assistant stop
  Remove it completely:               ./uninstall.sh${OFF}

  Installed in: $PAIOS_HOME
  Read next:    $APP_DIR/README.md

NEXT

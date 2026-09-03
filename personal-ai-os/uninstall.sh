#!/bin/bash
#
# Personal AI OS - uninstaller
#
# By default this removes the PROGRAM and leaves your DATA (settings, memory,
# the file index, the audit log) exactly where it is, so you can reinstall and
# pick up where you left off.
#
# It never touches your own documents. Not once, not ever, not with --purge-data.
#
# Usage:   ./uninstall.sh [--purge-data] [--yes]

set -euo pipefail

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'
    YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
else
    BOLD=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; OFF=""
fi
step() { printf '\n%s==>%s %s%s%s\n' "$CYAN" "$OFF" "$BOLD" "$1" "$OFF"; }
info() { printf '    %s\n' "$1"; }
good() { printf '    %s%s%s\n' "$GREEN" "$1" "$OFF"; }
warn() { printf '    %s%s%s\n' "$YELLOW" "$1" "$OFF"; }

PURGE=0
ASSUME_YES=0
while [ $# -gt 0 ]; do
    case "$1" in
        --purge-data) PURGE=1 ;;
        --yes|-y)     ASSUME_YES=1 ;;
        -h|--help)    sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) printf 'Unknown option %s\n' "$1" >&2; exit 1 ;;
    esac
    shift
done

if [ "$(uname -s)" = "Darwin" ]; then
    DEFAULT_HOME="$HOME/Library/Application Support/PersonalAIOS"
else
    DEFAULT_HOME="${XDG_DATA_HOME:-$HOME/.local/share}/personal-ai-os"
fi
PAIOS_HOME="${PAIOS_HOME:-$DEFAULT_HOME}"
LABEL="com.personalaios.assistantd"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

printf '%s\n  Personal AI OS - uninstaller%s\n' "$BOLD" "$OFF"

if [ "$PURGE" = "1" ]; then
    printf '\n%s  This will PERMANENTLY DELETE:%s\n' "$RED" "$OFF"
    printf '    - the assistant memory (everything it learned about you)\n'
    printf '    - the file index\n'
    printf '    - your permission settings and rules\n'
    printf '    - the audit log\n'
    printf '\n  It will NOT delete any of your own documents.\n'
    if [ "$ASSUME_YES" != "1" ]; then
        printf '\n  Type %sDELETE%s to confirm: ' "$BOLD" "$OFF"
        read -r reply
        [ "$reply" = "DELETE" ] || { printf '\n  Cancelled. Nothing was removed.\n\n'; exit 0; }
    fi
fi

# ------------------------------------------------------------- stop it first
step "Stopping the background service"
if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && good "Stopped." \
        || info "It was not running."
else
    info "launchctl not available (not macOS) - nothing to stop."
fi
if [ -f "$PAIOS_HOME/run/assistantd.pid" ]; then
    PID="$(cat "$PAIOS_HOME/run/assistantd.pid" 2>/dev/null || true)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        kill "$PID" 2>/dev/null && good "Stopped process $PID."
    fi
fi
rm -f "$PAIOS_HOME/run/assistantd.sock" "$PAIOS_HOME/run/assistantd.pid" 2>/dev/null || true

step "Removing the login item"
if [ -f "$PLIST" ]; then rm -f "$PLIST"; good "Removed $PLIST"
else info "There was no login item."; fi

step "Removing the 'assistant' command"
if [ -f "$HOME/.local/bin/assistant" ]; then
    rm -f "$HOME/.local/bin/assistant"; good "Removed ~/.local/bin/assistant"
else info "It was not installed there."; fi

step "Removing the program"
if [ -d "$PAIOS_HOME/app" ]; then rm -rf "$PAIOS_HOME/app"; good "Removed $PAIOS_HOME/app"
else info "Already gone."; fi
if [ -d "$PAIOS_HOME/venv" ]; then rm -rf "$PAIOS_HOME/venv"; good "Removed $PAIOS_HOME/venv"
fi

if [ "$PURGE" = "1" ]; then
    step "Removing your data"
    rm -rf "$PAIOS_HOME"
    if [ "$(uname -s)" = "Darwin" ]; then
        rm -rf "$HOME/Library/Logs/PersonalAIOS"
    fi
    good "All assistant data removed."
    printf '\n%s  Fully uninstalled.%s Your documents were not touched.\n\n' "$BOLD" "$OFF"
else
    step "Keeping your data"
    info "Left in place at:  $PAIOS_HOME"
    info "  - config.json      your settings and permissions"
    info "  - db/assistant.db  memory, file index, plans, audit log"
    info "  - backups/         copies of files it edited"
    printf '\n%s  Uninstalled.%s Reinstall any time and it will remember everything.\n' "$BOLD" "$OFF"
    printf '  To erase the data too:  ./uninstall.sh --purge-data\n\n'
fi

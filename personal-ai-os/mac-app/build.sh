#!/bin/bash
#
# Build the Personal AI OS desktop app.
#
# You do NOT need Xcode. The Swift compiler ships with the Command Line Tools
# you already installed (that is what `xcode-select --install` gave you).
#
# Usage:   ./build.sh              build it, then tell you where it is
#          ./build.sh --install    also copy it into /Applications
#          ./build.sh --run        build and launch it straight away

set -euo pipefail

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'
    GREEN=$'\033[32m'; YELLOW=$'\033[33m'; CYAN=$'\033[36m'; OFF=$'\033[0m'
else
    BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; OFF=""
fi
step() { printf '\n%s==>%s %s%s%s\n' "$CYAN" "$OFF" "$BOLD" "$1" "$OFF"; }
good() { printf '    %s%s%s\n' "$GREEN" "$1" "$OFF"; }
warn() { printf '    %s%s%s\n' "$YELLOW" "$1" "$OFF"; }
die()  { printf '\n%sBuild stopped:%s %s\n\n' "$RED" "$OFF" "$1" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="Personal AI OS"
BUNDLE_ID="com.personalaios.desktop"
BUILD_DIR="$HERE/build"
APP="$BUILD_DIR/$APP_NAME.app"

DO_INSTALL=0
DO_RUN=0
while [ $# -gt 0 ]; do
    case "$1" in
        --install) DO_INSTALL=1 ;;
        --run)     DO_RUN=1 ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option '$1'. Try --help." ;;
    esac
    shift
done

printf '%s\n  Personal AI OS — desktop app%s\n' "$BOLD" "$OFF"

# ---------------------------------------------------------------- checks
step "Checking the tools"

[ "$(uname -s)" = "Darwin" ] || die "This app is macOS-only."

if ! command -v swiftc >/dev/null 2>&1; then
    die "The Swift compiler was not found.

    It comes with Apple's Command Line Tools. Install them with:

        xcode-select --install

    then run this script again. (You do NOT need the full Xcode app.)"
fi
good "swiftc: $(swiftc --version 2>/dev/null | head -1)"

MACOS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$MACOS_MAJOR" -lt 13 ] 2>/dev/null; then
    die "This app needs macOS 13 (Ventura) or newer; you have $(sw_vers -productVersion).
    The 'assistant' command in Terminal still works on your version."
fi
good "macOS $(sw_vers -productVersion) on $(uname -m)"

if [ ! -x "$HOME/.local/bin/assistant" ] && ! command -v assistant >/dev/null 2>&1; then
    warn "The 'assistant' command is not installed yet."
    warn "The app will build, but it will say so on launch until you run:"
    warn "    cd $HERE/.. && ./install.sh"
fi

# ---------------------------------------------------------------- compile
step "Compiling"

rm -rf "$BUILD_DIR"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# Any Swift error below is printed in full. If it fails, copy the whole
# message — it names the file and line, which is all that's needed to fix it.
export MACOSX_DEPLOYMENT_TARGET=13.0
if ! swiftc \
        -O \
        -parse-as-library \
        -framework SwiftUI -framework AppKit -framework Foundation \
        -o "$APP/Contents/MacOS/PersonalAIOS" \
        "$HERE"/Sources/*.swift
then
    die "The Swift compiler reported errors above.

    Copy everything from the first 'error:' line downwards and send it back —
    that output names the exact file and line, which is all that is needed."
fi
good "Built $(du -h "$APP/Contents/MacOS/PersonalAIOS" | cut -f1 | tr -d ' ')"

# ---------------------------------------------------------------- bundle
step "Assembling the app"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>              <string>$APP_NAME</string>
    <key>CFBundleDisplayName</key>       <string>$APP_NAME</string>
    <key>CFBundleIdentifier</key>        <string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key>           <string>1.0.0</string>
    <key>CFBundleShortVersionString</key><string>1.0.0</string>
    <key>CFBundleExecutable</key>        <string>PersonalAIOS</string>
    <key>CFBundlePackageType</key>       <string>APPL</string>
    <key>LSMinimumSystemVersion</key>    <string>13.0</string>
    <key>NSHighResolutionCapable</key>   <true/>
    <key>NSSupportsAutomaticTermination</key><false/>
    <key>NSHumanReadableCopyright</key>  <string>Runs entirely on your Mac.</string>
</dict>
</plist>
PLIST
printf 'APPL????' > "$APP/Contents/PkgInfo"
good "Bundle at $APP"

# Ad-hoc signature. Not a Developer ID - it just gives the app a stable
# identity so macOS stops re-asking for permissions on every rebuild.
step "Signing (ad-hoc)"
if codesign --force --deep --sign - "$APP" 2>/dev/null; then
    good "Signed"
else
    warn "Could not sign it. The app still runs; macOS may re-ask for"
    warn "permissions after each rebuild."
fi

# ---------------------------------------------------------------- finish
if [ "$DO_INSTALL" = "1" ]; then
    step "Installing to /Applications"
    rm -rf "/Applications/$APP_NAME.app"
    cp -R "$APP" "/Applications/"
    good "/Applications/$APP_NAME.app"
    APP="/Applications/$APP_NAME.app"
fi

if [ "$DO_RUN" = "1" ]; then
    step "Launching"
    open "$APP"
    good "Running. Look for the icon in your menu bar, top right."
fi

printf '\n%s  Built.%s\n\n' "$BOLD" "$OFF"
cat <<NEXT
  Open it:            open "$APP"
  Keep it forever:    ./build.sh --install
  Start automatically: System Settings → General → Login Items → +

  ${DIM}The app is a face for the assistant, not a second copy of it. It runs
  the same 'assistant' command your Terminal does, so permissions, memory
  and the audit log are shared — and the emergency stop still works from
  either place.${OFF}

NEXT

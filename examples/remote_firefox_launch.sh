#!/usr/bin/env bash
# Launch Firefox on this Ubuntu box with the Remote Agent enabled, so that
# OpenEyes on another machine can drive it (see examples/remote_firefox_bidi.py).
#
# House rule: the window stays visible. A headless instance has no session a
# human can look at, and an SMS login wall is exactly where a human needs to
# see what the browser is doing.
#
# The profile is dedicated: a throwaway profile keeps the automation session
# away from the personal browsing profile, and it keeps cookies (a logged-in
# Feishu account, for instance) alive between runs.
set -euo pipefail

PROFILE_NAME="openeyes-remote-profile"
REMOTE_PORT=9222
DISPLAY_NUM=":0"
URL=""
RESTART=0

usage() {
    cat <<'EOF'
Usage: remote_firefox_launch.sh [options] [url]

  --url URL          page to open in a new window (default: none)
  --port PORT        Remote Agent port (default: 9222)
  --profile NAME     profile directory name (default: openeyes-remote-profile)
  --display DISPLAY  X display to use (default: :0)
  --restart          close a running instance on that port first
  -h, --help         this text
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --url) URL="$2"; shift 2 ;;
        --port) REMOTE_PORT="$2"; shift 2 ;;
        --profile) PROFILE_NAME="$2"; shift 2 ;;
        --display) DISPLAY_NUM="$2"; shift 2 ;;
        --restart) RESTART=1; shift ;;
        -h|--help) usage; exit 0 ;;
        --headless)
            echo "refusing --headless: this script drives a visible window" >&2
            exit 2
            ;;
        http*) URL="$1"; shift ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if ! command -v firefox >/dev/null 2>&1; then
    echo "firefox is not installed on this box" >&2
    exit 3
fi

# A snap Firefox can only use a profile under $HOME; a deb install can use
# any path. Put the profile where the installed flavour allows it.
firefox_path="$(readlink -f "$(command -v firefox)")"
case "$firefox_path" in
    /snap/*) PROFILE_DIR="$HOME/snap/firefox/common/$PROFILE_NAME" ;;
    *)      PROFILE_DIR="$HOME/.openeyes/$PROFILE_NAME" ;;
esac
mkdir -p "$PROFILE_DIR"

if pgrep -af "remote-debugging-port $REMOTE_PORT" >/dev/null 2>&1; then
    if [ "$RESTART" -eq 1 ]; then
        echo "==> closing the running instance on port $REMOTE_PORT"
        pkill -f "remote-debugging-port $REMOTE_PORT" || true
        sleep 2
    else
        echo "firefox is already listening on $REMOTE_PORT"
        echo "  profile: $(pgrep -af 'remote-debugging-port' | head -1 | sed -n 's/.*--profile \([^ ]*\).*/\1/p')"
        exit 0
    fi
fi

export DISPLAY="$DISPLAY_NUM"
LOG_DIR="$HOME/.openeyes/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/firefox-$REMOTE_PORT.log"

echo "==> launching firefox"
echo "    display : $DISPLAY"
echo "    profile : $PROFILE_DIR"
echo "    port    : $REMOTE_PORT"

args=(
    --no-remote
    --marionette
    --profile "$PROFILE_DIR"
    --remote-debugging-port "$REMOTE_PORT"
    --remote-allow-hosts localhost
)
if [ -n "$URL" ]; then
    args+=(--new-window "$URL")
fi

# setsid detaches from the SSH session so the browser survives the logout of
# the terminal that started it.
setsid firefox "${args[@]}" >"$LOG" 2>&1 &
sleep 3

if ss -ltn 2>/dev/null | grep -q ":$REMOTE_PORT "; then
    echo "==> remote agent listening on 127.0.0.1:$REMOTE_PORT"
    echo "    log: $LOG"
    exit 0
fi

echo "the remote agent did not open port $REMOTE_PORT; see $LOG" >&2
tail -n 20 "$LOG" >&2 || true
exit 4

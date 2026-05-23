#!/usr/bin/env bash
# Start scp-for-me: sets up a venv + Flask on first run, then launches the app.
set -euo pipefail

cd "$(dirname "$0")"

PORT="${PORT:-5555}"
VENV=".venv"

# Colorful, block-filled banner (truecolor ANSI). Disable with NO_COLOR=1.
banner() {
  if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]; then
    cat <<'PLAIN'

   SCP-FOR-ME
   two-pane local <-> laptop file transfer

PLAIN
    return
  fi
  local R=$'\e[0m' B=$'\e[1m'
  local c1=$'\e[38;2;255;0;128m'    # hot pink
  local c2=$'\e[38;2;255;128;0m'    # orange
  local c3=$'\e[38;2;255;221;0m'    # yellow
  local c4=$'\e[38;2;0;221;128m'    # green
  local c5=$'\e[38;2;0;170;255m'    # cyan
  local c6=$'\e[38;2;160;80;255m'   # purple
  printf '\n'
  printf '  %s%s███████╗ ██████╗██████╗ %s███████╗ ██████╗ ██████╗ %s███╗   ███╗███████╗%s\n' "$B" "$c1" "$c3" "$c5" "$R"
  printf '  %s%s██╔════╝██╔════╝██╔══██╗%s██╔════╝██╔═══██╗██╔══██╗%s████╗ ████║██╔════╝%s\n' "$B" "$c1" "$c3" "$c5" "$R"
  printf '  %s%s███████╗██║     ██████╔╝%s█████╗  ██║   ██║██████╔╝%s██╔████╔██║█████╗  %s\n' "$B" "$c2" "$c4" "$c6" "$R"
  printf '  %s%s╚════██║██║     ██╔═══╝ %s██╔══╝  ██║   ██║██╔══██╗%s██║╚██╔╝██║██╔══╝  %s\n' "$B" "$c2" "$c4" "$c6" "$R"
  printf '  %s%s███████║╚██████╗██║     %s██║     ╚██████╔╝██║  ██║%s██║ ╚═╝ ██║███████╗%s\n' "$B" "$c1" "$c3" "$c5" "$R"
  printf '  %s%s╚══════╝ ╚═════╝╚═╝     %s╚═╝      ╚═════╝ ╚═╝  ╚═╝%s╚═╝     ╚═╝╚══════╝%s\n' "$B" "$c1" "$c3" "$c5" "$R"
  printf '\n      %s%stwo-pane local %s<->%s laptop file transfer%s\n\n' "$B" "$c4" "$c3" "$c5" "$R"
}
banner

# Create the venv + install Flask the first time round.
if [ ! -x "$VENV/bin/python" ]; then
  echo ">> first run: creating virtualenv and installing Flask..."
  python3 -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip flask
fi

echo ">> serving on http://127.0.0.1:$PORT"
echo ">> from your laptop:  ssh -L $PORT:localhost:$PORT user@this-host  then open http://localhost:$PORT"
echo

exec "$VENV/bin/python" app.py

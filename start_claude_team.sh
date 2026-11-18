#!/bin/bash
# Start the studyserver agent team in one gnome-terminal window.
#
# One tab for the main session and one per component owner. Owners are read
# from .claude/agents/*.md, so the list stays current when owners change.
# Each owner session runs from its agent definition and is named after it,
# so other sessions can message it by that name.
#
# Usage: ./start_claude_team.sh <moat environment>
#   e.g. ./start_claude_team.sh studyserver
# Without an argument, prints this help and exits.

if [ -z "$1" ]; then
  sed -n '2,/^$/{/^$/d;s/^# \{0,1\}//;p}' "$0"
  exit 1
fi
MOAT_NAME="$1"
REPO="$(cd "$(dirname "$0")" && pwd)"

# Open one tab running the given claude arguments inside moat. The tab stays
# open after claude exits, showing the exit code, so errors stay readable.
open_tab() {
  local title="$1"; shift
  gnome-terminal --tab --title="$title" --working-directory="$REPO" -- \
    bash -c 'moat run --name "$0" -- claude "$@"; echo "[exited with code $?]"; exec bash' \
    "$MOAT_NAME" "$@"
}

# One gnome-terminal call per tab: a single call with several tabs fails with
# "Failed to get screen". --tab opens each tab in the current window.
open_tab main -n main
for def in "$REPO"/.claude/agents/*.md; do
  agent="$(basename "$def" .md)"
  sleep 1
  open_tab "$agent" --agent "$agent" -n "$agent"
done

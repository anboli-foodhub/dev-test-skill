#!/usr/bin/env bash
# Install the dev-test skill for Claude Code.
#
#   ./install.sh                 link into ~/.claude/skills (git pull updates it)
#   ./install.sh --copy          copy instead of link
#   ./install.sh --project DIR   install into DIR/.claude/skills (one repo only)
#   ./install.sh --uninstall     remove from ~/.claude/skills (or --project DIR)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/skills/dev-test"
DEST_ROOT="$HOME/.claude/skills"
MODE=link
ACTION=install

while [ $# -gt 0 ]; do
  case "$1" in
    --copy) MODE=copy ;;
    --project) shift; DEST_ROOT="$(cd "${1:?--project needs a folder}" && pwd)/.claude/skills" ;;
    --uninstall) ACTION=uninstall ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

DEST="$DEST_ROOT/dev-test"

if [ "$ACTION" = uninstall ]; then
  if [ -L "$DEST" ] || [ -d "$DEST" ]; then rm -rf "$DEST"; echo "Removed $DEST"; else echo "Nothing at $DEST"; fi
  exit 0
fi

if [ -e "$DEST" ] && [ ! -L "$DEST" ]; then
  echo "$DEST already exists and is not a link from this repo." >&2
  echo "Move it away or run ./install.sh --uninstall first." >&2
  exit 1
fi

mkdir -p "$DEST_ROOT"
rm -f "$DEST"
if [ "$MODE" = link ]; then
  ln -s "$SRC" "$DEST"
else
  cp -R "$SRC" "$DEST"
fi
echo "Installed dev-test ($MODE) at $DEST"

command -v python3 >/dev/null || echo "Warning: python3 not found - the .xlsx report needs it." >&2
echo "Restart Claude Code, then run: /dev-test <TICKET-ID>"

#!/usr/bin/env bash
# Renders docs/scripts/demo-board.json (real finalize output) to docs/assets/board.webp.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
doc="$root/docs/scripts/demo-board.json"
out="$root/docs/assets/board.webp"
png="$(mktemp -d)/board.png"
# The getaway block pack needs the cc-present plugin's binary; the brew one predates packs.
present="$(ls -d "$HOME"/.claude/plugins/cache/cc-present/cc-present/*/bin/cc-present | sort -V | tail -1)"

cleanup() {
  "$present" close --session getaway-demo --cwd "$root" >/dev/null 2>&1 || true
  agent-browser close >/dev/null 2>&1 || true
  rm -rf "$(dirname "$png")"
}
trap cleanup EXIT

url="$("$present" start --new --session getaway-demo --cwd "$root" --doc "$doc" | grep -Eo 'https?://[^[:space:]]+' | head -1)"

agent-browser set viewport 1280 1600
agent-browser open "$url"
agent-browser wait 2500
board_ref="$(agent-browser snapshot | grep -o 'button "BOARD" \[ref=e[0-9]*' | grep -o 'e[0-9]*$')"
test -n "$board_ref" || { echo "BOARD toggle not found in agent-browser snapshot" >&2; exit 1; }
agent-browser click "@$board_ref"
agent-browser wait 1200
agent-browser screenshot "$png"
cwebp -q 82 "$png" -o "$out" >/dev/null 2>&1

size="$(stat -f%z "$out")"
test "$size" -lt 1048576 || { echo "board.webp is ${size} bytes, over the 1 MiB asset cap" >&2; exit 1; }
echo "wrote $out (${size} bytes)"

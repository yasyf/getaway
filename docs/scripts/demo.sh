#!/usr/bin/env bash
# Walks a trip's artifact spine — intake → finalists → booking sheet → close —
# and renders the live finalists round to docs/assets/board.webp.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
scripts="$root/docs/scripts"
intake="$scripts/demo-intake.json"
finalists="$scripts/demo-board.json"
booking="$scripts/demo-booking.json"
out="$root/docs/assets/board.webp"
png="$(mktemp -d)/board.png"
session=getaway-demo
# The getaway block pack needs the cc-present plugin's binary; the brew one predates packs.
present="$(ls -d "$HOME"/.claude/plugins/cache/cc-present/cc-present/*/bin/cc-present | sort -V | tail -1)"

cleanup() {
  "$present" close --session "$session" --cwd "$root" >/dev/null 2>&1 || true
  agent-browser close >/dev/null 2>&1 || true
  rm -rf "$(dirname "$png")"
}
trap cleanup EXIT

# One artifact per trip; each phase is a round on it.
url="$("$present" start --new --session "$session" --cwd "$root" --title "getaway demo" | grep -Eo 'https?://[^[:space:]]+' | head -1)"
"$present" push --session "$session" "$intake"

"$present" round --session "$session" --title "Finalists"
"$present" push --session "$session" "$finalists"

# Render the live finalists round.
agent-browser set viewport 1280 1600
agent-browser open "$url"
agent-browser wait 2500
board_ref="$(agent-browser snapshot | grep -o 'button "BOARD" \[ref=e[0-9]*' | grep -o 'e[0-9]*$' || true)"
if [ -n "$board_ref" ]; then
  agent-browser click "@$board_ref"
  agent-browser wait 1200
fi
agent-browser screenshot "$png"
cwebp -q 82 "$png" -o "$out" >/dev/null 2>&1

size="$(stat -f%z "$out")"
test "$size" -lt 1048576 || { echo "board.webp is ${size} bytes, over the 1 MiB asset cap" >&2; exit 1; }
echo "wrote $out (${size} bytes)"

# The single-pick path skips head to head straight to the booking sheet.
"$present" round --session "$session" --title "Booking sheet"
"$present" push --session "$session" "$booking"
"$present" close --session "$session" --summary "Honolulu all-business on 70k Alaska miles; sheet delivered."

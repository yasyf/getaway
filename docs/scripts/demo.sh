#!/usr/bin/env bash
# Renders docs/scripts/demo-board.json (real finalize output) to docs/assets/board.webp.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
doc="$root/docs/scripts/demo-board.json"
out="$root/docs/assets/board.webp"
png="$(mktemp -d)/board.png"

cleanup() {
  cc-present close --session getaway-demo --cwd "$root" >/dev/null 2>&1 || true
  agent-browser close >/dev/null 2>&1 || true
}
trap cleanup EXIT

url="$(cc-present start --new --session getaway-demo --cwd "$root" --doc "$doc" | grep -Eo 'https?://[^[:space:]]+' | head -1)"

agent-browser set viewport 1280 1200
agent-browser open "$url"
agent-browser wait 2500
agent-browser screenshot "$png"
cwebp -q 82 "$png" -o "$out" >/dev/null 2>&1

size="$(stat -f%z "$out")"
test "$size" -lt 1048576 || { echo "board.webp is ${size} bytes, over the 1 MiB asset cap" >&2; exit 1; }
echo "wrote $out (${size} bytes)"

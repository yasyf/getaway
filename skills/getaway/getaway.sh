#!/usr/bin/env bash
set -euo pipefail
umask 077

BASE_URL="https://seats.aero/partnerapi"
GETAWAY_DIR="$HOME/.getaway"
PREFS="$GETAWAY_DIR/preferences.json"
QUOTA="$GETAWAY_DIR/quota"
PLANS_DIR="$GETAWAY_DIR/plans"
PLANS_CURRENT="$PLANS_DIR/current"

usage() {
  local msg="${1:-}"
  [[ -n "$msg" ]] && echo "$msg" >&2
  cat >&2 <<'USAGE'
usage: getaway.sh <command> [flags]

commands:
  prefs-init                       write ~/.getaway/preferences.json (refuses if it exists)
  prefs                            print preferences as compact JSON
  prefs-status                     print configured/unconfigured; exit 0 if a balance is set, else 1
  prefs-set                        read a JSON patch on stdin and top-level-merge it into preferences
  plan-new <slug>                  create a trip-memory plan (refuses if the slug exists); sets it current
  plan-set [<slug>]                read a JSON patch on stdin and top-level-merge it into the named/current plan
  plan-show [<slug>]               print the named/current plan as compact JSON
  plan-list                        list plans as slug/status/created rows
  plan-done [<slug>]               mark the named/current plan done; clears the current pointer if it points there
  search  --origin A,B --dest C,D [--start YYYY-MM-DD] [--end YYYY-MM-DD]
          [--cabin business] [--sources aeroplan,united] [--carriers DL,AA]
          [--direct] [--order lowest_mileage] [--take 500] [--pages 1]
  availability --source aeroplan [--cabin business] [--start YYYY-MM-DD] [--end YYYY-MM-DD]
          [--origin-region "North America"] [--dest-region Africa] [--take 500] [--pages 1]
  routes --source aeroplan [--origin-region "North America"] [--dest-region Asia]
          region flags filter the response client-side (the API exposes no region param)
  trip <ID>                        print one trip object (segments, booking links)
  quota                            print the last recorded quota (no API call)
USAGE
  exit 64
}

resolve_key() {
  if [[ -n "${SEATS_AERO_API_KEY:-}" ]]; then
    printf '%s' "$SEATS_AERO_API_KEY"
    return 0
  fi
  if [[ -f "$PREFS" ]]; then
    local op_ref key
    op_ref=$(jq -r '.op_ref // empty' "$PREFS")
    if [[ -n "$op_ref" ]]; then
      [[ "$op_ref" == op://* ]] || { echo "op_ref must be a 1Password reference like op://Vault/item/field, got: $op_ref" >&2; exit 2; }
      key=$(op read "$op_ref") || { echo "op read failed for $op_ref; check the reference and 1Password sign-in" >&2; exit 2; }
      [[ -n "$key" ]] || { echo "op read returned nothing for $op_ref" >&2; exit 2; }
      printf '%s' "$key"
      return 0
    fi
  fi
  cat >&2 <<'REMEDY'
no seats.aero API key found. set one of:
  1. export SEATS_AERO_API_KEY=pro_...   (environment variable; wins when set)
  2. set .op_ref in ~/.getaway/preferences.json to the key's 1Password reference, e.g. op://Vault/item/field
REMEDY
  exit 2
}

record_quota() {
  local hdr="$1"
  local remaining
  remaining=$(grep -i '^X-RateLimit-Remaining:' "$hdr" | tr -d '\r' | tail -n1 | sed 's/^[^:]*:[[:space:]]*//' || true)
  if [[ -z "$remaining" ]]; then
    echo "quota header missing from response; leaving cached quota unchanged" >&2
    return 0
  fi
  mkdir -p "$GETAWAY_DIR"
  printf '%s\t%s\n' "$remaining" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$QUOTA"
  echo "quota remaining: $remaining" >&2
}

api_get() {
  local path="$1"
  shift
  local key cfg hdr
  key=$(resolve_key) || exit $?
  cfg=$(mktemp)
  hdr=$(mktemp)
  trap 'rm -f "$cfg" "$hdr"' RETURN
  printf 'header = "Partner-Authorization: %s"\n' "$key" >"$cfg"
  curl -q -G -fsS --globoff \
    --config "$cfg" \
    -H "Accept: application/json" \
    -D "$hdr" \
    "$@" \
    "$BASE_URL$path"
  record_quota "$hdr"
}

emit_dedup() {
  local resp="$1" seenfile="$2"
  local emitted
  emitted=$(printf '%s' "$resp" | jq -c --rawfile seen "$seenfile" '
    ($seen | split("\n") | map(select(length > 0)) | INDEX(.)) as $s
    | .data[] | select(($s[.ID | tostring]) == null)
  ')
  if [[ -n "$emitted" ]]; then
    printf '%s\n' "$emitted"
    printf '%s\n' "$emitted" | jq -r '.ID | tostring' >>"$seenfile"
  fi
}

paginate() {
  local path="$1" take="$2" pages="$3"
  shift 3
  local -a base=("$@")
  local seenfile cursor="" skip=0 page=1
  seenfile=$(mktemp)
  while [[ $page -le $pages ]]; do
    local -a params=("${base[@]}")
    [[ -n "$cursor" ]] && params+=(--data-urlencode "cursor=$cursor")
    [[ $skip -gt 0 ]] && params+=(--data-urlencode "skip=$skip")
    local resp count
    resp=$(api_get "$path" "${params[@]}")
    count=$(printf '%s' "$resp" | jq '.data | length')
    emit_dedup "$resp" "$seenfile"
    cursor=$(printf '%s' "$resp" | jq -r '.cursor // empty')
    [[ $count -lt $take ]] && break
    [[ -z "$cursor" ]] && break
    skip=$((skip + count))
    page=$((page + 1))
  done
  rm -f "$seenfile"
}

prefs_template() {
  cat <<'JSON'
{
  "op_ref": null,
  "home_airport": "SFO",
  "origin_airports": ["SFO", "SJC", "SAN", "PDX", "DEN", "LAS", "SLC", "YVR"],
  "avoid_transit": [],
  "avoid_airlines": [{"code": "ET", "name": "Ethiopian Airlines", "strength": "soft"}],
  "statuses": {},
  "balances": {"programs": {}, "transferable": {}},
  "learnings": []
}
JSON
}

plan_template() {
  cat <<'JSON'
{
  "slug": null,
  "created": null,
  "status": "planning",
  "ask": null,
  "window": {"start": null, "end": null, "trip_length_days": null},
  "cabin": null,
  "party": 1,
  "regions": {"include": [], "exclude": []},
  "vibe": [],
  "avoid_final_destinations": [],
  "decisions": []
}
JSON
}

cmd_prefs_init() {
  [[ -e "$PREFS" || -L "$PREFS" ]] && {
    echo "preferences already exist at $PREFS; refusing to overwrite" >&2
    exit 3
  }
  mkdir -p "$GETAWAY_DIR"
  prefs_template >"$PREFS"
  echo "$PREFS"
}

cmd_prefs_status() {
  [[ -f "$PREFS" ]] || { echo "unconfigured"; exit 1; }
  if jq -e '((.balances.programs // {}) | length) > 0 or ((.balances.transferable // {}) | length) > 0' "$PREFS" >/dev/null; then
    echo "configured"
  else
    echo "unconfigured"
    exit 1
  fi
}

cmd_prefs_set() {
  local patch base tmp unknown extra
  patch=$(cat)
  jq -es 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1 <<<"$patch" || usage "prefs-set: stdin must be a single JSON object"
  unknown=$(jq -c --argjson t "$(prefs_template)" '(keys - ($t | keys))' <<<"$patch")
  [[ "$unknown" == "[]" ]] || usage "prefs-set: unknown preference keys: $unknown"
  [[ -L "$PREFS" || ( -e "$PREFS" && ! -f "$PREFS" ) ]] && { echo "$PREFS is not a regular file; refusing to write" >&2; exit 3; }
  mkdir -p "$GETAWAY_DIR"
  if [[ -f "$PREFS" ]]; then
    base=$(cat "$PREFS")
  else
    base=$(prefs_template)
  fi
  tmp=$(mktemp "$GETAWAY_DIR/.prefs.XXXXXX")
  trap 'rm -f "${tmp:-}"' EXIT  # EXIT, not RETURN: this runs in the main shell, so cleanup must survive exit 3 and set -e
  jq --argjson patch "$patch" '. + $patch' <<<"$base" >"$tmp"
  extra=$(jq -c --argjson t "$(prefs_template)" '(keys - ($t | keys))' "$tmp")
  [[ "$extra" == "[]" ]] || { echo "prefs-set: merged preferences carry keys absent from the template: $extra; refusing to write" >&2; exit 3; }
  jq -e 'has("op_ref") and has("home_airport") and ((has("avoid_transit") | not) or (.avoid_transit | type == "array")) and ((has("statuses") | not) or (.statuses | type == "object")) and (.balances | type == "object") and ((.balances | has("programs") | not) or (.balances.programs | type == "object")) and ((.balances | has("transferable") | not) or (.balances.transferable | type == "object"))' "$tmp" >/dev/null || { echo "prefs-set produced invalid preferences; refusing to write" >&2; exit 3; }
  mv -f "$tmp" "$PREFS"
  echo "$PREFS"
}

cmd_prefs() {
  [[ -f "$PREFS" ]] || {
    echo "no preferences at $PREFS; run: getaway.sh prefs-init" >&2
    exit 3
  }
  jq -c . "$PREFS"
}

plan_resolve_slug() {
  local slug="$1"
  if [[ -z "$slug" ]]; then
    [[ -f "$PLANS_CURRENT" ]] || { echo "no current plan; run: getaway.sh plan-new <slug>" >&2; exit 3; }
    slug=$(cat "$PLANS_CURRENT")
  fi
  [[ "$slug" == */* ]] && { echo "plan slug must not contain '/': $slug" >&2; exit 3; }
  printf '%s' "$slug"
}

plan_current_guard() {
  if [[ -L "$PLANS_CURRENT" || ( -e "$PLANS_CURRENT" && ! -f "$PLANS_CURRENT" ) ]]; then
    echo "$PLANS_CURRENT is not a regular file; refusing to write" >&2
    exit 3
  fi
}

plan_write_current() {
  local slug="$1" tmp
  plan_current_guard
  tmp=$(mktemp "$PLANS_DIR/.current.XXXXXX")
  trap 'rm -f "${tmp:-}"' EXIT  # EXIT, not RETURN: this runs in the main shell, so cleanup must survive exit 3 and set -e
  printf '%s' "$slug" >"$tmp"
  mv -f "$tmp" "$PLANS_CURRENT"
}

plan_require() {
  local file="$1"
  [[ -f "$file" ]] || { echo "no plan at $file" >&2; exit 3; }
}

cmd_plan_new() {
  [[ $# -ge 1 ]] || usage "plan-new: usage: plan-new <slug>"
  local slug="$1"
  [[ "$slug" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]] || usage "plan-new: invalid slug: $slug (must match ^[A-Za-z0-9][A-Za-z0-9._-]*\$; letters, digits, dot, dash, underscore, no leading dot or slash)"
  local file="$PLANS_DIR/$slug.json"
  [[ -e "$file" || -L "$file" ]] && {
    echo "plan already exists at $file; refusing to overwrite" >&2
    exit 3
  }
  mkdir -p "$PLANS_DIR"
  plan_current_guard
  plan_template | jq --arg slug "$slug" --arg created "$(date -u +%Y-%m-%dT%H:%M:%SZ)" '.slug = $slug | .created = $created' >"$file"
  plan_write_current "$slug"
  echo "$file"
}

cmd_plan_set() {
  local slug patch base tmp unknown reserved file
  patch=$(cat)
  jq -es 'length == 1 and (.[0] | type == "object")' >/dev/null 2>&1 <<<"$patch" || usage "plan-set: stdin must be a single JSON object"
  unknown=$(jq -c --argjson t "$(plan_template)" '(keys - ($t | keys))' <<<"$patch")
  [[ "$unknown" == "[]" ]] || usage "plan-set: unknown plan keys: $unknown"
  reserved=$(jq -c '[keys[] | select(. == "slug" or . == "created")]' <<<"$patch")
  [[ "$reserved" == "[]" ]] || usage "plan-set: keys stamped by plan-new cannot be patched: $reserved"
  slug=$(plan_resolve_slug "${1:-}") || exit $?
  file="$PLANS_DIR/$slug.json"
  [[ -L "$file" || ( -e "$file" && ! -f "$file" ) ]] && { echo "$file is not a regular file; refusing to write" >&2; exit 3; }
  plan_require "$file"
  base=$(cat "$file")
  tmp=$(mktemp "$PLANS_DIR/.plan.XXXXXX")
  trap 'rm -f "${tmp:-}"' EXIT  # EXIT, not RETURN: this runs in the main shell, so cleanup must survive exit 3 and set -e
  jq --argjson patch "$patch" '. + $patch' <<<"$base" >"$tmp"
  jq -e 'has("slug") and (.status == "planning" or .status == "done") and ((has("window") | not) or (.window | type == "object" and ((has("start") | not) or (.start | type == "string" or . == null)) and ((has("end") | not) or (.end | type == "string" or . == null)) and ((has("trip_length_days") | not) or (.trip_length_days | type == "number" or . == null)))) and ((has("regions") | not) or (.regions | type == "object" and ((has("include") | not) or (.include | type == "array")) and ((has("exclude") | not) or (.exclude | type == "array")))) and ((has("vibe") | not) or (.vibe | type == "array")) and ((has("avoid_final_destinations") | not) or (.avoid_final_destinations | type == "array")) and ((has("decisions") | not) or (.decisions | type == "array"))' "$tmp" >/dev/null || { echo "plan-set produced an invalid plan; refusing to write" >&2; exit 3; }
  mv -f "$tmp" "$file"
  echo "$file"
}

cmd_plan_show() {
  local slug file
  slug=$(plan_resolve_slug "${1:-}") || exit $?
  file="$PLANS_DIR/$slug.json"
  plan_require "$file"
  jq -c . "$file"
}

cmd_plan_list() {
  [[ -d "$PLANS_DIR" ]] || return 0
  local f
  for f in "$PLANS_DIR"/*.json; do
    [[ -e "$f" ]] || continue
    plan_require "$f"
    jq -r '[.slug, .status, .created] | @tsv' "$f"
  done
}

cmd_plan_done() {
  local slug file tmp
  slug=$(plan_resolve_slug "${1:-}") || exit $?
  file="$PLANS_DIR/$slug.json"
  plan_require "$file"
  plan_current_guard
  tmp=$(mktemp "$PLANS_DIR/.plan.XXXXXX")
  trap 'rm -f "${tmp:-}"' EXIT  # EXIT, not RETURN: this runs in the main shell, so cleanup must survive exit 3 and set -e
  jq '.status = "done"' "$file" >"$tmp"
  mv -f "$tmp" "$file"
  [[ -f "$PLANS_CURRENT" && "$(cat "$PLANS_CURRENT")" == "$slug" ]] && rm -f "$PLANS_CURRENT"
  echo "$file"
}

cmd_search() {
  local origin="" dest="" start="" end="" cabin="" sources="" carriers="" order="" direct="" take=500 pages=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --origin) [[ $# -ge 2 ]] || usage "search: --origin needs a value"; origin="$2"; shift 2 ;;
      --dest) [[ $# -ge 2 ]] || usage "search: --dest needs a value"; dest="$2"; shift 2 ;;
      --start) [[ $# -ge 2 ]] || usage "search: --start needs a value"; start="$2"; shift 2 ;;
      --end) [[ $# -ge 2 ]] || usage "search: --end needs a value"; end="$2"; shift 2 ;;
      --cabin) [[ $# -ge 2 ]] || usage "search: --cabin needs a value"; cabin="$2"; shift 2 ;;
      --sources) [[ $# -ge 2 ]] || usage "search: --sources needs a value"; sources="$2"; shift 2 ;;
      --carriers) [[ $# -ge 2 ]] || usage "search: --carriers needs a value"; carriers="$2"; shift 2 ;;
      --order) [[ $# -ge 2 ]] || usage "search: --order needs a value"; order="$2"; shift 2 ;;
      --take) [[ $# -ge 2 ]] || usage "search: --take needs a value"; take="$2"; shift 2 ;;
      --pages) [[ $# -ge 2 ]] || usage "search: --pages needs a value"; pages="$2"; shift 2 ;;
      --direct) direct=1; shift ;;
      *) usage "search: unknown flag: $1" ;;
    esac
  done
  [[ -n "$origin" ]] || usage "search: --origin is required"
  [[ -n "$dest" ]] || usage "search: --dest is required"
  [[ $take =~ ^[1-9][0-9]*$ ]] || usage "search: --take must be a positive integer"
  [[ $pages =~ ^[1-9][0-9]*$ ]] || usage "search: --pages must be a positive integer"

  local -a params=(
    --data-urlencode "origin_airport=$origin"
    --data-urlencode "destination_airport=$dest"
    --data-urlencode "take=$take"
  )
  [[ -n "$start" ]] && params+=(--data-urlencode "start_date=$start")
  [[ -n "$end" ]] && params+=(--data-urlencode "end_date=$end")
  [[ -n "$cabin" ]] && params+=(--data-urlencode "cabins=$cabin")
  [[ -n "$sources" ]] && params+=(--data-urlencode "sources=$sources")
  [[ -n "$carriers" ]] && params+=(--data-urlencode "carriers=$carriers")
  [[ -n "$order" ]] && params+=(--data-urlencode "order_by=$order")
  [[ -n "$direct" ]] && params+=(--data-urlencode "only_direct_flights=true")

  paginate "/search" "$take" "$pages" "${params[@]}"
}

cmd_availability() {
  local source="" cabin="" start="" end="" origin_region="" dest_region="" take=500 pages=1
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) [[ $# -ge 2 ]] || usage "availability: --source needs a value"; source="$2"; shift 2 ;;
      --cabin) [[ $# -ge 2 ]] || usage "availability: --cabin needs a value"; cabin="$2"; shift 2 ;;
      --start) [[ $# -ge 2 ]] || usage "availability: --start needs a value"; start="$2"; shift 2 ;;
      --end) [[ $# -ge 2 ]] || usage "availability: --end needs a value"; end="$2"; shift 2 ;;
      --origin-region) [[ $# -ge 2 ]] || usage "availability: --origin-region needs a value"; origin_region="$2"; shift 2 ;;
      --dest-region) [[ $# -ge 2 ]] || usage "availability: --dest-region needs a value"; dest_region="$2"; shift 2 ;;
      --take) [[ $# -ge 2 ]] || usage "availability: --take needs a value"; take="$2"; shift 2 ;;
      --pages) [[ $# -ge 2 ]] || usage "availability: --pages needs a value"; pages="$2"; shift 2 ;;
      *) usage "availability: unknown flag: $1" ;;
    esac
  done
  [[ -n "$source" ]] || usage "availability: --source is required"
  [[ $take =~ ^[1-9][0-9]*$ ]] || usage "availability: --take must be a positive integer"
  [[ $pages =~ ^[1-9][0-9]*$ ]] || usage "availability: --pages must be a positive integer"

  local -a params=(
    --data-urlencode "source=$source"
    --data-urlencode "take=$take"
  )
  [[ -n "$cabin" ]] && params+=(--data-urlencode "cabin=$cabin")
  [[ -n "$start" ]] && params+=(--data-urlencode "start_date=$start")
  [[ -n "$end" ]] && params+=(--data-urlencode "end_date=$end")
  [[ -n "$origin_region" ]] && params+=(--data-urlencode "origin_region=$origin_region")
  [[ -n "$dest_region" ]] && params+=(--data-urlencode "destination_region=$dest_region")

  paginate "/availability" "$take" "$pages" "${params[@]}"
}

cmd_routes() {
  local source="" origin_region="" dest_region=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --source) [[ $# -ge 2 ]] || usage "routes: --source needs a value"; source="$2"; shift 2 ;;
      --origin-region) [[ $# -ge 2 ]] || usage "routes: --origin-region needs a value"; origin_region="$2"; shift 2 ;;
      --dest-region) [[ $# -ge 2 ]] || usage "routes: --dest-region needs a value"; dest_region="$2"; shift 2 ;;
      *) usage "routes: unknown flag: $1" ;;
    esac
  done
  [[ -n "$source" ]] || usage "routes: --source is required"

  local resp
  resp=$(api_get "/routes" --data-urlencode "source=$source")
  printf '%s' "$resp" | jq -c \
    --arg origin "$origin_region" \
    --arg dest "$dest_region" '
    .[]
    | select($origin == "" or .OriginRegion == $origin)
    | select($dest == "" or .DestinationRegion == $dest)
  '
}

cmd_trip() {
  [[ $# -ge 1 ]] || usage "trip: usage: trip <ID>"
  [[ "$1" == */* ]] && usage "trip: ID must not contain '/': $1"
  local resp
  resp=$(api_get "/trips/$1")
  printf '%s' "$resp" | jq -c .
}

cmd_quota() {
  [[ -s "$QUOTA" ]] || {
    echo "no quota recorded yet; run a search, availability, or trip first" >&2
    exit 4
  }
  local remaining ts
  IFS=$'\t' read -r remaining ts <"$QUOTA"
  echo "$remaining remaining (as of $ts)"
}

main() {
  [[ $# -ge 1 ]] || usage "no command given"
  local cmd="$1"
  shift
  case "$cmd" in
    prefs-init) cmd_prefs_init "$@" ;;
    prefs) cmd_prefs "$@" ;;
    prefs-status) cmd_prefs_status "$@" ;;
    prefs-set) cmd_prefs_set "$@" ;;
    plan-new) cmd_plan_new "$@" ;;
    plan-set) cmd_plan_set "$@" ;;
    plan-show) cmd_plan_show "$@" ;;
    plan-list) cmd_plan_list "$@" ;;
    plan-done) cmd_plan_done "$@" ;;
    search) cmd_search "$@" ;;
    availability) cmd_availability "$@" ;;
    routes) cmd_routes "$@" ;;
    trip) cmd_trip "$@" ;;
    quota) cmd_quota "$@" ;;
    *) usage "unknown command: $cmd" ;;
  esac
}

main "$@"

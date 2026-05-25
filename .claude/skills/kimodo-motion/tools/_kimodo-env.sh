# shellcheck shell=bash
# Shared helper sourced by every kimodo-motion tool. Resolves where the kimodo
# checkout is (KIMODO_DIR) and where the running motion API is (KIMODO_URL),
# and provides small colored-output helpers.
#
#   KIMODO_DIR  path to a kimodo checkout (has docker-compose.yaml + baker/).
#               If unset, autodetected: walk up from this skill (works when the
#               skill still lives inside the kimodo repo), then common paths.
#   KIMODO_URL  motion API base URL. Default http://127.0.0.1:7862

KIMODO_URL="${KIMODO_URL:-http://127.0.0.1:7862}"
KIMODO_DIR="${KIMODO_DIR:-}"   # default empty so `set -u` callers can test it safely

if [ -t 1 ]; then
  C_G=$'\033[32m'; C_R=$'\033[31m'; C_Y=$'\033[33m'; C_B=$'\033[1m'; C_0=$'\033[0m'
else
  C_G=; C_R=; C_Y=; C_B=; C_0=
fi
k_ok()    { printf '  %s✓%s %s\n' "$C_G" "$C_0" "$*"; }
k_bad()   { printf '  %s✗%s %s\n' "$C_R" "$C_0" "$*"; }
k_warn()  { printf '  %s⚠%s %s\n' "$C_Y" "$C_0" "$*"; }
k_head()  { printf '\n%s%s%s\n' "$C_B" "$*" "$C_0"; }
k_die()   { printf '%serror:%s %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }

# Directory this helper lives in (…/kimodo-motion/tools).
_KM_TOOLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_km_is_repo() { [ -f "$1/docker-compose.yaml" ] && [ -d "$1/baker" ]; }

km_resolve_dir() {
  # 1. explicit env wins.
  if [ -n "$KIMODO_DIR" ]; then
    _km_is_repo "$KIMODO_DIR" || k_die "KIMODO_DIR=$KIMODO_DIR is not a kimodo checkout (no docker-compose.yaml + baker/)."
    printf '%s\n' "$KIMODO_DIR"; return 0
  fi
  # 2. walk up from the skill (skill may live inside the kimodo repo).
  local d="$_KM_TOOLS_DIR"
  while [ "$d" != "/" ]; do
    if _km_is_repo "$d"; then printf '%s\n' "$d"; return 0; fi
    d="$(dirname "$d")"
  done
  # 3. common locations.
  local c
  for c in "$HOME/projects/kimodo" "$HOME/kimodo" "$PWD/kimodo" "$PWD/../kimodo"; do
    if _km_is_repo "$c"; then printf '%s\n' "$c"; return 0; fi
  done
  k_die "couldn't find a kimodo checkout. Set KIMODO_DIR to your kimodo repo path (the dir with docker-compose.yaml)."
}

# GET a motion-API path; prints body, returns curl's exit code.
km_api_get()  { curl -fsS "${KIMODO_URL%/}$1"; }
# True if the motion API answers /animations.
km_api_up()   { curl -fsS -o /dev/null "${KIMODO_URL%/}/animations" 2>/dev/null; }

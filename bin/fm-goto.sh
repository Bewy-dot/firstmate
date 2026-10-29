#!/usr/bin/env bash
# fm-goto.sh - focus the Herdr tab of a live worker so you land in its pane.
# Usage: fm-goto.sh <task-id>
# Resolves state/<id>.meta in this FM_HOME (or, failing that, in a local second
# mate's own home), validates the recorded endpoint exactly, confirms the pane
# still exists, then focuses its tab. Herdr has no focus-by-pane-id and no URL
# scheme, and each task owns a one-pane tab, so focusing the tab (which also
# selects its workspace) lands on the worker. Only focus changes; nothing is
# created, moved, or closed. Any backend other than Herdr is a safe no-op.
# Exit: 0 focused or nothing to do, 1 unknown task or pane gone, 2 usage.
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

case "${1:-}" in
  -h|--help) sed -n '2,11s/^# \{0,1\}//p' "$0"; exit 0 ;;
  ''|-*) echo 'usage: fm-goto.sh <task-id>' >&2; exit 2 ;;
esac
id=$1

# shellcheck source=bin/fm-backend.sh
. "$SCRIPT_DIR/fm-backend.sh"

meta="$STATE/$id.meta"
if [ ! -f "$meta" ]; then
  for owner in "$STATE"/*.meta; do
    [ -f "$owner" ] && [ "$(fm_meta_get "$owner" kind)" = secondmate ] || continue
    home=$(fm_meta_get "$owner" home)
    [ -n "$home" ] && [ -f "$home/state/$id.meta" ] && { meta="$home/state/$id.meta"; break; }
  done
fi
[ -f "$meta" ] || { echo "No worker named $id is recorded here." >&2; exit 1; }

fm_backend_validate_task_endpoint "$meta" "$id" >/dev/null || { echo "The recorded pane for $id failed its safety check." >&2; exit 1; }
if [ "$FM_BACKEND_VALIDATED_BACKEND" != herdr ]; then
  echo "$id is not on Herdr; there is no pane to focus from here."
  exit 0
fi

fm_backend_source herdr
fm_backend_herdr_tool_check
target=$FM_BACKEND_VALIDATED_TARGET
session=${target%%:*}
pane=${target#*:}
info=$(fm_backend_herdr_cli "$session" pane get "$pane" 2>/dev/null) || { echo "The pane for $id is gone." >&2; exit 1; }
[ "$(printf '%s' "$info" | jq -r '.result.pane.pane_id // empty')" = "$pane" ] || { echo "The pane for $id is gone." >&2; exit 1; }
tab=$(printf '%s' "$info" | jq -r '.result.pane.tab_id // empty')
[ -n "$tab" ] || { echo "The pane for $id has no tab to focus." >&2; exit 1; }
fm_backend_herdr_cli "$session" tab focus "$tab" >/dev/null || { echo "Herdr could not focus the tab for $id." >&2; exit 1; }
echo "Focused $id (${pane%%:*} / $tab / ${pane#*:})."

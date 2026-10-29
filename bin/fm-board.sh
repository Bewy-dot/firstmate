#!/usr/bin/env bash
# fm-board.sh - living board and Herdr pane labels over fm-fleet-snapshot.sh.
# Usage: fm-board.sh [refresh|render|view] [--snapshot <canonical-json-file>]
#                    [--home-snapshot <second-mate-id>=<file>]
# refresh is a fail-soft presentation operation, automatically called by the
# watcher. It no-ops outside Herdr, requires the adapter's verified launcher
# identity, and never starts a server or closes a pane. view is the terminal
# board (j/k, Enter to go to a worker's pane, o/b/r to open); view --once
# [--width N] [--select N] [--detail] prints one frame and exits.
# render writes data/board.md, data/board-full.md and state/board-model.json on
# any backend; --snapshot imports a previously captured fm-fleet-snapshot.v1
# object for offline previews and fixture tests, and --home-snapshot imports a
# second mate's own-home snapshot the same way.
# Data model, config, and rendering are owned by fm-board.py and fm_board_tui.py;
# fleet semantics remain owned by fm-fleet-snapshot.sh and fm-crew-state.sh.
set -eu
[ "${FM_BOARD_DISABLED:-0}" != 1 ] || exit 0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}}"
case "${1:-refresh}" in
  -h|--help) sed -n '2,17s/^# \{0,1\}//p' "$0"; exit 0 ;;
  render|view) exec python3 "$SCRIPT_DIR/fm-board.py" "$@" ;;
  refresh) [ "$#" -eq 0 ] || shift ;;
  *) echo 'fm-board: expected refresh, render, or view' >&2; exit 2 ;;
esac
# shellcheck source=bin/fm-backend.sh
. "$SCRIPT_DIR/fm-backend.sh"
[ "$(fm_backend_name)" = herdr ] || exit 0
fm_backend_source herdr
session=$(fm_backend_herdr_session)
fm_backend_herdr_launcher_identity "$session" || exit 1
export FM_BOARD_PARENT_PANE="$FM_BACKEND_HERDR_LAUNCHER_PANE_ID"
export FM_BOARD_PARENT_TAB="$FM_BACKEND_HERDR_LAUNCHER_TAB_ID"
export FM_BOARD_PARENT_WORKSPACE="$FM_BACKEND_HERDR_LAUNCHER_WORKSPACE_ID"
exec python3 "$SCRIPT_DIR/fm-board.py" refresh "$@"

#!/usr/bin/env bash
# Real Herdr board placement, labels, reuse, and closed-pane recovery.
# All Herdr operations, including adapter calls, pass through the lab helper.
# Opt in: FM_TEST_HERDR_BOARD=1 bash tests/fm-board-herdr-e2e.test.sh
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
[ "${FM_TEST_HERDR_BOARD:-0}" = 1 ] || { echo 'skip: set FM_TEST_HERDR_BOARD=1'; exit 0; }
command -v herdr >/dev/null || { echo 'skip: herdr not installed'; exit 0; }
original_path=$PATH
TMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/fm-board-e2e.XXXXXX")
HERDR_LAB_HELPER=${HERDR_LAB_HELPER:-$ROOT/bin/fm-herdr-lab.sh}
HERDR_LAB_SESSION=$("$HERDR_LAB_HELPER" name fm-board-and-labels)
cleanup() {
  local rc=$?
  trap - EXIT
  export PATH="$original_path"
  "$HERDR_LAB_HELPER" teardown "$HERDR_LAB_SESSION" || rc=1
  rm -rf "$TMP_ROOT"
  exit "$rc"
}
trap cleanup EXIT
"$HERDR_LAB_HELPER" provision "$HERDR_LAB_SESSION"
lab() { "$HERDR_LAB_HELPER" run "$HERDR_LAB_SESSION" "$@"; }
lab status --json | jq -r '"Herdr \(.client.version) / protocol \(.client.protocol)"'
created=$(lab workspace create --label firstmate-board-test)
parent=$(printf '%s' "$created" | jq -r '.result.root_pane.pane_id')
workspace=$(printf '%s' "$created" | jq -r '.result.workspace.workspace_id')
worker=$(lab pane split "$parent" --direction down --no-focus | jq -r '.result.pane.pane_id')
socket=$(lab session list --json | jq -r --arg s "$HERDR_LAB_SESSION" '.sessions[] | select(.name == $s) | .socket_path')
mkdir -p "$TMP_ROOT/home/config" "$TMP_ROOT/home/data" "$TMP_ROOT/home/state" "$TMP_ROOT/fakebin"
printf '## In flight\n\n## Queued\n\n## Done\n' > "$TMP_ROOT/home/data/backlog.md"
# A scoped transport shim, not a Herdr stub. The helper rejects lifecycle
# requests and appends its own session flag after this verifies the caller's.
python3 - "$TMP_ROOT/fakebin/herdr" "$PATH" "$HERDR_LAB_HELPER" "$HERDR_LAB_SESSION" <<'PY'
import pathlib, shlex, sys
path, original, helper, session = sys.argv[1:]
pathlib.Path(path).write_text('''#!/usr/bin/env bash
set -eu
args=("$@")
n=${#args[@]}
[ "$n" -ge 2 ] || exit 90
[ "${args[n-2]}" = --session ] || exit 91
[ "${args[n-1]}" = ''' + shlex.quote(session) + ''' ] || exit 92
unset 'args[n-1]' 'args[n-2]'
exec env PATH=''' + shlex.quote(original) + ' ' + shlex.quote(helper) + ' run ' + shlex.quote(session) + ' "${args[@]}"\n')
PY
chmod +x "$TMP_ROOT/fakebin/herdr"
export FM_BOARD_DISABLED=0 FM_BACKEND=herdr FM_HOME="$TMP_ROOT/home"
export HERDR_SESSION="$HERDR_LAB_SESSION" HERDR_SOCKET_PATH="$socket" HERDR_PANE_ID="$parent"
original_path=$PATH
export PATH="$TMP_ROOT/fakebin:$PATH"
# Long environment values must live in the private launch file, never in a
# terminal input line (the initial regression exceeded 1024 bytes).
PATH="$PATH:$(python3 -c 'print("/unused-board-path/" + "x" * 2200)')"
export PATH
python3 "$ROOT/tests/fm-board.test.py" --fixture "$TMP_ROOT/fixture.json"
python3 - "$TMP_ROOT/fixture.json" "$HERDR_LAB_SESSION:$worker" <<'PY'
import json, sys
p, target = sys.argv[1:]
s = json.load(open(p))
s['tasks'] = [s['tasks'][0]]
s['tasks'][0]['endpoint']['target'] = target
json.dump(s, open(p, 'w'))
PY
"$ROOT/bin/fm-board.sh" refresh --snapshot "$TMP_ROOT/fixture.json"
label=$(lab pane get "$worker" | jq -r '.result.pane.label')
[ "$label" = 'compensation-rules - NEEDS ANSWER' ] || fail "wrong worker label: $label"
count() { lab pane list --workspace "$workspace" | jq '[.result.panes[] | select(.label // "" | startswith("Firstmate board "))] | length'; }
[ "$(count)" = 1 ] || fail 'expected one board pane'
python3 - "$FM_HOME/state/board-viewer.sh" <<'PYMODE'
import pathlib, stat, sys
assert stat.S_IMODE(pathlib.Path(sys.argv[1]).stat().st_mode) == 0o700
PYMODE
board=$(lab pane list --workspace "$workspace" | jq -r '.result.panes[] | select(.label // "" | startswith("Firstmate board ")) | .pane_id')
"$ROOT/bin/fm-board.sh" refresh --snapshot "$TMP_ROOT/fixture.json"
[ "$(count)" = 1 ] || fail 'refresh created duplicate board pane'
[ "$(lab pane get "$board" | jq -r '.result.pane.tab_id')" = "$(lab pane get "$parent" | jq -r '.result.pane.tab_id')" ] || fail 'board must stay beside firstmate'
# Verify the process actually renders the file, rather than merely existing.
rendered=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
  if lab pane read "$board" --source recent --lines 80 | grep -q 'Crew board'; then rendered=1; break; fi
  sleep 0.2
done
[ "$rendered" = 1 ] || fail 'board viewer did not render'
lab pane close "$board" >/dev/null
"$ROOT/bin/fm-board.sh" refresh --snapshot "$TMP_ROOT/fixture.json"
[ "$(count)" = 1 ] || fail 'closed board was not recreated exactly once'
# All classification cases travel through the same public refresh interface.
python3 - "$ROOT" "$TMP_ROOT" "$worker" "$HERDR_LAB_SESSION" <<'PY'
import json, pathlib, subprocess, sys
root, tmp, pane, session = sys.argv[1:]
p = pathlib.Path(tmp) / 'fixture.json'
s = json.loads(p.read_text())
t = s['tasks'][0]
for state, detail, expected in [
    ('working', 'Implementing', 'WORKING'), ('working', 'ci running', 'CI RUNNING'),
    ('done', 'checks green', 'READY: merge?'),
    ('done', 'checks green; apply production database migration before merge', 'READY: apply + merge?'),
    ('paused', 'Resume after upstream release', 'PAUSED'), ('blocked', 'Need credentials', 'BLOCKED'),
    ('done', 'Report completed', 'DONE')]:
    t['hints'] = {}
    t['current_state'] = dict(state=state, detail=detail)
    t['pr'] = {} if expected == 'DONE' else dict(url='https://github.com/example/continuo/pull/1')
    p.write_text(json.dumps(s))
    subprocess.run([root+'/bin/fm-board.sh', 'refresh', '--snapshot', str(p)], check=True)
    info = json.loads(subprocess.check_output(['herdr', 'pane', 'get', pane, '--session', session]))
    actual = info['result']['pane']['label']
    assert actual == t['id'] + ' - ' + expected, (actual, expected)
PY
export PATH="$original_path"
pass 'real Herdr: labels, rendered board, adjacent placement, reuse, and closed-pane recovery'

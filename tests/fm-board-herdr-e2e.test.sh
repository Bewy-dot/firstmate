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
HERDR_LAB_SESSION=${HERDR_LAB_SESSION:-$("$HERDR_LAB_HELPER" name fm-board-tab-sections)}
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
  if lab pane read "$board" --source recent --lines 80 | grep -q 'Needs you now'; then rendered=1; break; fi
  sleep 0.2
done
[ "$rendered" = 1 ] || fail 'board viewer did not render'
# Exercise section keys on a stable offline fixture in an independent lab pane.
keys_pane=$(lab workspace create --label board-section-keys | jq -r '.result.root_pane.pane_id')
python3 - "$ROOT" "$TMP_ROOT" "$HERDR_LAB_SESSION" <<'PYKEYS'
import importlib.util, json, pathlib, shlex, sys
root, tmp, session = sys.argv[1:]
spec = importlib.util.spec_from_file_location('board_fixture', root + '/tests/fm-board.test.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
home = pathlib.Path(tmp) / 'keys-home'
(home / 'state').mkdir(parents=True)
snap = m.sequence_fixture()
snap['fm_home'] = str(home)
snap['tasks'].append(m.fixture()['tasks'][6])
for record in snap['backlog']['records']:
    record['since'] = '2026-10-28'
path = pathlib.Path(tmp) / 'keys.json'
path.write_text(json.dumps(snap))
import os, subprocess
subprocess.run([root + '/bin/fm-board.sh', 'render', '--snapshot', str(path)],
               env=dict(os.environ, FM_HOME=str(home)), check=True)
launch = pathlib.Path(tmp) / 'keys-view.sh'
launch.write_text('#!/bin/sh\nexec ' + shlex.join(['env', 'FM_HOME=' + str(home),
    'FM_BOARD_DISABLED=1', 'HERDR_SESSION=' + session, 'NO_COLOR=',
    'python3', root + '/bin/fm-board.py', 'view']) + '\n')
launch.chmod(0o700)
PYKEYS
lab pane wait-output "$keys_pane" --regex '[#$%>❯➜][ \t]*$' --lines 8 --timeout 5000 >/dev/null
lab pane run "$keys_pane" "sh '$TMP_ROOT/keys-view.sh'"
wait_key_frame() {
  lab pane wait-output "$keys_pane" --regex "$1" --lines 100 --timeout 5000 >/dev/null
}
wait_key_frame 'Needs you now'
if [ -n "${FM_BOARD_EVIDENCE_DIR:-}" ]; then
  mkdir -p "$FM_BOARD_EVIDENCE_DIR"
  lab pane read "$keys_pane" --source visible --format ansi --raw > "$FM_BOARD_EVIDENCE_DIR/before-tab.ansi"
fi
lab pane send-keys "$keys_pane" tab
wait_key_frame 'In motion'
lab pane send-keys "$keys_pane" tab
wait_key_frame 'Dependencies may wait'
if [ -n "${FM_BOARD_EVIDENCE_DIR:-}" ]; then
  lab pane read "$keys_pane" --source visible --format ansi --raw > "$FM_BOARD_EVIDENCE_DIR/after-queued.ansi"
fi
lab pane send-keys "$keys_pane" tab
wait_key_frame 'Bring back:'
lab pane send-keys "$keys_pane" tab
wait_key_frame 'Needs you now'
# Literal CSI Z is Shift-Tab's terminal encoding, independent of GUI bindings.
lab pane send-text "$keys_pane" $'\033[Z'
wait_key_frame 'Bring back:'
for section in 1 2 3 4; do
  lab pane send-text "$keys_pane" "$section"
  case "$section" in
    1) wait_key_frame 'Needs you now' ;;
    2) wait_key_frame 'In motion'
       if [ -n "${FM_BOARD_EVIDENCE_DIR:-}" ]; then
         lab pane read "$keys_pane" --source visible --format ansi --raw > "$FM_BOARD_EVIDENCE_DIR/after-moving.ansi"
       fi ;;
    3) wait_key_frame 'Dependencies may wait' ;;
    4) wait_key_frame 'Bring back:' ;;
  esac
done
lab pane send-text "$keys_pane" q
lab pane wait-output "$keys_pane" --regex '[#$%>❯➜][ \t]*$' --lines 8 --timeout 5000 >/dev/null
lab pane run "$keys_pane" "sh '$TMP_ROOT/keys-view.sh'"
wait_key_frame 'Bring back:'
lab pane send-text "$keys_pane" q
printf '%s\n' 'section keys: Tab forward/wrap, CSI Z backward, 1-4 jumps, restart retains Parked'
lab pane close "$board" >/dev/null
"$ROOT/bin/fm-board.sh" refresh --snapshot "$TMP_ROOT/fixture.json"
[ "$(count)" = 1 ] || fail 'closed board was not recreated exactly once'
# All classification cases travel through the same public refresh interface.
python3 - "$ROOT" "$TMP_ROOT" "$worker" "$HERDR_LAB_SESSION" <<'PY'
import json, pathlib, subprocess, sys, time
root, tmp, pane, session = sys.argv[1:]
p = pathlib.Path(tmp) / 'fixture.json'
s = json.loads(p.read_text())
t = s['tasks'][0]
def refresh_label(expected):
    # Refresh is deliberately fail-soft when the viewer owns board.lock.
    # Await the observable label instead of assuming that one call published it.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        subprocess.run([root+'/bin/fm-board.sh', 'refresh', '--snapshot', str(p)], check=True)
        info = json.loads(subprocess.check_output(['herdr', 'pane', 'get', pane, '--session', session]))
        if info['result']['pane']['label'] == expected:
            return
        time.sleep(0.05)
    raise AssertionError((expected, info))
for state, detail, expected, backlog in [
    ('working', 'Implementing', 'WORKING', {}), ('working', 'ci running', 'CI RUNNING', {}),
    ('working', 'Implementing', 'WAITING: Earlier step',
     dict(unresolved_blocker_ids=['earlier'])),
    ('done', 'checks green', 'CI GREEN',
     dict(captain_actionable=True, hold_kind='captain', hold_reason='Merge this')),
    ('done', 'checks green; apply production database migration before merge', 'CI GREEN', {}),
    ('blocked', 'Waiting for captain word', 'HOLDING',
     dict(hold_kind='captain', hold_reason='Wait for the captain word')),
    ('paused', 'Resume after upstream release', 'PAUSED', {}),
    ('blocked', 'Need credentials', 'BLOCKED', {}),
    ('done', 'Report completed', 'DONE', {})]:
    t['hints'] = dict(open_decisions=[dict(verb='needs-decision', summary='Awaiting your merge word')]) if expected == 'CI GREEN' else {}
    t['backlog'] = dict(id=t['id'], title='Test work', repo='continuo', **backlog)
    s['backlog']['records'] = [dict(id='earlier', title='Earlier step', structured=True, repo='continuo', state='in_flight')]
    t['current_state'] = dict(state=state, detail=detail)
    t['pr'] = dict(url='https://github.com/example/continuo/pull/1',
                   observed_at=s['generated'], merge_state='CLEAN',
                   check_runs=[dict(name='tests', status='COMPLETED', conclusion='SUCCESS')]) if expected == 'CI GREEN' else {}
    p.write_text(json.dumps(s))
    refresh_label(t['id'] + ' - ' + expected)
for runs, expected in [
    ([dict(name='tests', status='IN_PROGRESS', startedAt='2026-10-30T07:53:00Z'),
      dict(name='lint', state='PENDING')], 'CI RUNNING: 0 of 2 checks passed, 7m elapsed'),
    ([dict(name='lint', status='COMPLETED', conclusion='FAILURE')], 'CI FAILED: lint'),
    ([], 'no checks configured')]:
    t['hints'] = {}
    t['backlog'] = dict(id=t['id'], title='CI test', repo='continuo')
    t['current_state'] = dict(state='done', detail='PR opened')
    t['pr'] = dict(url='https://github.com/example/continuo/pull/1',
                   observed_at=s['generated'], merge_state='CLEAN', check_runs=runs)
    p.write_text(json.dumps(s))
    refresh_label(t['id'] + ' - ' + expected)
# Exercise the same three reported misreads on a second mate's aggregate pane.
s['tasks'] = [dict(id='mate', kind='secondmate', backend='herdr',
                   endpoint=dict(target=session + ':' + pane, exists=True))]
for state, detail, backlog, expected in [
    ('working', 'Implementing', dict(unresolved_blocker_ids=['earlier']), 'WAITING: Earlier step'),
    ('done', 'checks green', dict(hold_kind='captain', captain_actionable=True), 'CI NOT FETCHED'),
    ('blocked', 'Wait for captain word', dict(hold_kind='captain', hold_reason='Wait for captain word'), 'HOLDING')]:
    s['secondmate_current'] = dict(records=[dict(id='mate',
        current=dict(state='active_child_work' if state == 'working' else 'captain_decision'),
        freshness=dict(status='fresh'), provenance=dict(summary_valid=True),
        queued=[dict(id='child', repo='continuo', title='Next step', **backlog),
                dict(id='earlier', repo='continuo', title='Earlier step')],
        endpoints=[dict(id='child', state=state, detail=detail,
                        pr_url='https://github.com/example/continuo/pull/1')])])
    p.write_text(json.dumps(s))
    refresh_label('mate - ' + expected)
# New cold-cache and failed-fetch states must never aggregate to DONE.
for evidence, expected in [
    (dict(pending_fetch=True), 'CI FETCHING'),
    (dict(live=True, error='GitHub unreachable'), 'PR FETCH FAILED: GitHub unreachable'),
    (dict(live=True, state='MERGED'), 'MERGED'),
    (dict(live=True, state='CLOSED'), 'CLOSED')]:
    owner = s['secondmate_current']['records'][0]
    owner['queued'] = []
    owner['endpoints'] = [dict(id='child', state='done', detail='PR opened',
        pr_url='https://github.com/example/continuo/pull/1',
        pr=dict(url='https://github.com/example/continuo/pull/1', **evidence))]
    p.write_text(json.dumps(s))
    refresh_label('mate - ' + expected)

PY
# The real viewer must observe file/forge events without any supervisor/chat turn.
printf '%s\n' 'latency proof: creating isolated viewer workspace'
latency_parent=$(lab workspace create --label board-latency | jq -r '.result.root_pane.pane_id')
lab pane wait-output "$latency_parent" --regex '[#$%>❯➜][ \t]*$' --lines 8 --timeout 10000 >/dev/null
HERDR_PANE_ID="$latency_parent" python3 - "$ROOT" "$TMP_ROOT" "$worker" "$workspace" <<'PYLATENCY'
import hashlib, json, os, pathlib, subprocess, sys, time
root, tmp, worker, workspace = sys.argv[1:]
home = pathlib.Path(tmp) / 'latency-home'
for folder in ('state', 'data', 'config'):
    (home / folder).mkdir(parents=True, exist_ok=True)
(home / 'data/backlog.md').write_text('## In flight\n\n## Queued\n\n## Done\n')
session = os.environ['HERDR_SESSION']
url = 'https://github.com/example/cadenza/pull/1'
(home / 'state/latency.meta').write_text(f'kind=scout\nharness=codex\nbackend=herdr\nwindow={session}:{worker}\nherdr_session={session}\nherdr_workspace_id={workspace}\nherdr_pane_id={worker}\nworktree={home}\nproject=cadenza\npr={url}\n')
status = home / 'state/latency.status'
status.write_text('working: Implementing\n')
# Only the forge is fake; the viewer, canonical producer, adapter, panes and
# guarded transport are real. Worker status reports are the controlled input.
fakebin = pathlib.Path(tmp) / 'latency-bin'
fakebin.mkdir()
forge = home / 'forge-state'
forge.write_text('SUCCESS')
cli = fakebin / 'gh'
cli.write_text('#!/usr/bin/env python3\nimport json,pathlib,sys\nstate=pathlib.Path(' + repr(str(forge)) + ').read_text()\nif sys.argv[2]=="view": print(json.dumps(dict(state="OPEN",headRefOid="a")))\nelse: print(json.dumps([dict(name="test",state=state,bucket="pending" if state=="IN_PROGRESS" else "pass")]))\n')
cli.chmod(0o700)
env = dict(os.environ, FM_HOME=str(home), PATH=str(fakebin) + os.pathsep + os.environ['PATH'])
# Seed a healthy cache so initialization measures the canonical collection only.
cache = home / 'state/board-pr-cache'
cache.mkdir()
(cache / (hashlib.sha256(url.encode()).hexdigest() + '.json')).write_text(json.dumps(dict(url=url, live=True, state='OPEN', fetched_at=time.time(), check_runs=[dict(name='test', state='SUCCESS')])))
start = time.monotonic()
subprocess.run([root + '/bin/fm-board.sh', 'refresh'], env=env, check=True)
print(f'cold canonical collection + pane setup: {time.monotonic()-start:.3f}s', flush=True)
def lab(*args):
    return subprocess.check_output(['herdr', *args, '--session', session], text=True)
label = 'Firstmate board ' + hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:12]
pane = next(p['pane_id'] for p in json.loads(lab('pane', 'list'))['result']['panes'] if p.get('label') == label)
def wait_visible(needle, start, budget=10):
    text = ''
    while time.monotonic() - start < budget:
        text = lab('pane', 'read', pane, '--source', 'visible', '--lines', '80')
        if needle in text:
            return time.monotonic() - start
        time.sleep(.1)
    raise AssertionError(f'{needle} not visible within {budget}s:\n{text}')
wait_visible('In motion', time.monotonic())
start = time.monotonic()
with status.open('a') as out:
    out.write(f'done: Implemented | pr={url} | checks=green | next=merge | owner=captain\n')
status_latency = wait_visible('CI green', start)
print(f'status append -> real viewer: {status_latency:.3f}s (budget 10s)', flush=True)
start = time.monotonic()
forge.write_text('IN_PROGRESS')
ci_latency = wait_visible('CI restarted', start)
model = json.loads((home / 'state/board-model.json').read_text())
assert model['counts']['need'] == 0 and model['counts']['moving'] == 1, model
assert 'moved from Needs you to Running' in model['just_moved'][-1]['text'], model
assert 'restarted after fix' in (home / 'data/board-full.md').read_text()
assert 'elapsed' in (home / 'data/board-full.md').read_text()
text = lab('pane', 'read', pane, '--source', 'visible', '--lines', '80')
assert 'Just moved: latency: Needs -> Running' in text, text
print(f'forge check change -> real viewer: {ci_latency:.3f}s (budget 10s)', flush=True)
print('movement strip: latest Needs -> Running and restarted CI elapsed visible', flush=True)
# A durable asked marker must change the same live viewer independently of chat.
import shutil
shutil.copyfile(pathlib.Path(root) / '.tasks.toml', home / '.tasks.toml')
origin = home / 'data/board-review'
origin.mkdir()
(origin / 'report.md').write_text('# Board review\n')
hold = subprocess.check_output([root + '/bin/fm-decision-hold.sh', 'hold', 'board-review', 'pilot',
    '--title', 'Choose pilot', '--reason', 'Which pilot firm?', '--repo', 'cadenza'], env=env, text=True).strip()
wait_visible('1 decision queued', time.monotonic())
start = time.monotonic()
subprocess.run([root + '/bin/fm-decision-hold.sh', 'ask', hold, '--question', 'Which pilot firm?'], env=env, check=True)
asked_latency = wait_visible('Which pilot firm?', start)
model = json.loads((home / 'state/board-model.json').read_text())
assert model['counts']['need'] == 1 and model['counts']['queued'] == 0, model
assert 'Waiting' in model['need'][0]['action'], model
print(f'asked marker -> real viewer: {asked_latency:.3f}s (budget 10s)', flush=True)

PYLATENCY

# fm-goto lands on the worker: Herdr cannot focus a pane by id, so it focuses the owning tab.
worker_tab=$(lab pane get "$worker" | jq -r '.result.pane.tab_id')
cat > "$FM_HOME/state/goto-task.meta" <<META
window=$HERDR_LAB_SESSION:$worker
endpoint_task_id=goto-task
worktree=$TMP_ROOT/wt
project=$TMP_ROOT/project
backend=herdr
herdr_session=$HERDR_LAB_SESSION
herdr_workspace_id=$workspace
herdr_tab_id=$worker_tab
herdr_pane_id=$worker
META
other_tab=$(lab tab create --workspace "$workspace" --label elsewhere | jq -r '.result.tab.tab_id')
focused_tab() { lab tab list --workspace "$workspace" | jq -r '.result.tabs[] | select(.focused) | .tab_id'; }
lab tab focus "$other_tab" >/dev/null
[ "$(focused_tab)" = "$other_tab" ] || fail 'setup: the other tab should be focused first'
"$ROOT/bin/fm-goto.sh" goto-task | grep -q 'Focused goto-task' || fail 'fm-goto did not report success'
[ "$(focused_tab)" = "$worker_tab" ] || fail 'fm-goto did not focus the worker tab'
lab pane close "$worker" >/dev/null
"$ROOT/bin/fm-goto.sh" goto-task >/dev/null 2>&1 && fail 'fm-goto must refuse a closed pane'
export PATH="$original_path"
pass 'real Herdr: labels, rendered board, adjacent placement, reuse, closed-pane recovery, go-to-pane focus, and section keys'

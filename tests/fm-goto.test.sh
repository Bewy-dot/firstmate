#!/usr/bin/env bash
# Behavior tests for bin/fm-goto.sh with a recording fake Herdr client.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-goto)
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
LOG="$TMP_ROOT/herdr.log"
HOME_DIR="$TMP_ROOT/home"
MATE_DIR="$TMP_ROOT/mate"
mkdir -p "$HOME_DIR/state" "$MATE_DIR/state"
: > "$LOG"

cat > "$FAKEBIN/herdr" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FM_FAKE_HERDR_LOG"
case "$1 ${2:-}" in
  "pane get")
    [ "${FM_FAKE_HERDR_GONE:-0}" != 1 ] || { echo '{"error":{"code":"pane_not_found"}}'; exit 1; }
    printf '{"result":{"pane":{"pane_id":"%s","tab_id":"w1:tAB"}}}\n' "$3" ;;
  "tab focus") printf '{"result":{"tab":{"tab_id":"%s"}}}\n' "$3" ;;
  *) exit 90 ;;
esac
SH
chmod +x "$FAKEBIN/herdr"

herdr_meta() { # <state-dir> <id> <pane-suffix>
  fm_write_meta "$1/$2.meta" "window=default:w1:$3" "endpoint_task_id=$2" "worktree=$TMP_ROOT/wt" \
    "project=$TMP_ROOT/proj" backend=herdr herdr_session=default herdr_workspace_id=w1 \
    "herdr_tab_id=w1:tAB" "herdr_pane_id=w1:$3"
}
herdr_meta "$HOME_DIR/state" alpha pAB
herdr_meta "$MATE_DIR/state" child pCD
fm_write_secondmate_meta "$HOME_DIR/state/mate.meta" "$MATE_DIR"
fm_write_meta "$HOME_DIR/state/legacy.meta" "window=main:fm-legacy" "worktree=$TMP_ROOT/wt" "project=$TMP_ROOT/proj"

goto() {
  PATH="$FAKEBIN:$PATH" FM_HOME="$HOME_DIR" FM_FAKE_HERDR_LOG="$LOG" "$ROOT/bin/fm-goto.sh" "$@" 2>&1
}

out=$(goto alpha); rc=$?
expect_code 0 "$rc" 'a live Herdr worker is focused'
assert_contains "$out" 'Focused alpha (w1 / w1:tAB / pAB)' 'the focus is reported in plain terms'
assert_grep 'pane get w1:pAB --session default' "$LOG" 'the recorded pane is checked first'
assert_grep 'tab focus w1:tAB --session default' "$LOG" 'the pane own tab is focused'
assert_no_grep 'pane focus' "$LOG" 'no directional pane focus is used'
pass 'focuses the worker tab through the exact recorded pane'

: > "$LOG"
out=$(goto child); rc=$?
expect_code 0 "$rc" 'a second mate child is found through its owner home'
assert_grep 'pane get w1:pCD --session default' "$LOG" 'the child own recorded pane is used'
pass 'resolves a second mate child through the owner home'

: > "$LOG"
out=$(goto legacy); rc=$?
expect_code 0 "$rc" 'a tmux worker is a no-op'
assert_contains "$out" 'not on Herdr' 'the no-op is explained'
[ ! -s "$LOG" ] || fail 'a non-Herdr worker must not call Herdr'
pass 'is a safe no-op for other backends'

out=$(goto nobody); rc=$?
expect_code 1 "$rc" 'an unknown worker fails'
assert_contains "$out" 'No worker named nobody' 'the unknown worker is named'
out=$(goto); rc=$?
expect_code 2 "$rc" 'a missing id is a usage error'

: > "$LOG"
out=$(FM_FAKE_HERDR_GONE=1 goto alpha); rc=$?
expect_code 1 "$rc" 'a closed pane fails'
assert_contains "$out" 'is gone' 'the closed pane is reported'
assert_no_grep 'tab focus' "$LOG" 'nothing is focused when the pane is gone'
pass 'refuses unknown workers and closed panes without focusing'

: > "$LOG"
fm_write_meta "$HOME_DIR/state/bad.meta" "window=default:w1:pZZ" "endpoint_task_id=other" "worktree=$TMP_ROOT/wt" \
  "project=$TMP_ROOT/proj" backend=herdr herdr_session=default herdr_workspace_id=w1 "herdr_tab_id=w1:tAB" "herdr_pane_id=w1:pZZ"
out=$(goto bad); rc=$?
expect_code 1 "$rc" 'metadata bound to another task is refused'
[ ! -s "$LOG" ] || fail 'a failed safety check must not call Herdr'
pass 'validates the recorded endpoint before any Herdr call'

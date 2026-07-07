#!/usr/bin/env bash
# tests/fm-tier.test.sh - bin/fm-tier.sh deterministic risk/size tier
# classifier: boundary behavior at the captain-decided 100-line/5-file T1
# thresholds, high-risk and unknown/binary force-to-T2, docs-only -> T0, the
# rename-crossing-class-boundary disqualification of T0, the additive
# per-project tier-policy extension file, and the non-zero-exit-on-error
# contract callers must treat as tier=2.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TIER="$ROOT/bin/fm-tier.sh"
TMP_ROOT=$(fm_test_tmproot fm-tier)
fm_git_identity

# mkrepo <dir>: a fresh repo on branch "main" with one commit (README.md),
# deterministic regardless of the host's git init.defaultBranch config.
mkrepo() {
  local dir=$1
  mkdir -p "$dir"
  git -C "$dir" init -q -b main
  printf '# repo\n' > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m base
}

# start_change <dir> <branch>: branch off main so later commits diff cleanly
# against it with `fm-tier.sh <dir> main`.
start_change() {
  local dir=$1 branch=$2
  git -C "$dir" checkout -q -b "$branch" main
}

commit_all() {
  local dir=$1 msg=$2
  git -C "$dir" add -A
  git -C "$dir" commit -q -m "$msg"
}


test_usage_and_notgit_are_errors() {
  local out status
  out=$("$TIER" 2>&1); status=$?
  expect_code 1 "$status" "no args should be a usage error"
  assert_contains "$out" "usage" "missing-arg error should mention usage"

  local notgit
  notgit="$TMP_ROOT/notgit"
  mkdir -p "$notgit"
  out=$("$TIER" "$notgit" 2>&1); status=$?
  expect_code 1 "$status" "a non-git directory should be a classifier error"
  assert_not_contains "$out" "tier=" "a classifier error must never print a tier= line"
  pass "fm-tier: usage and non-git-directory errors exit non-zero with no tier= output"
}

test_bad_base_ref_errors_non_zero() {
  local dir out status
  dir="$TMP_ROOT/badbase"
  mkrepo "$dir"
  out=$("$TIER" "$dir" no-such-ref-xyz 2>&1); status=$?
  expect_code 1 "$status" "an unresolvable base-ref should be a classifier error"
  assert_not_contains "$out" "tier=" "a bad base-ref error must never print a tier= line"
  pass "fm-tier: unresolvable base-ref exits non-zero, callers must treat as tier=2"
}

test_docs_only_is_tier0() {
  local dir out
  dir="$TMP_ROOT/docsonly"
  mkrepo "$dir"
  start_change "$dir" docs-change
  printf 'more docs\n' >> "$dir/README.md"
  mkdir -p "$dir/docs"
  printf 'notes\n' > "$dir/docs/notes.md"
  commit_all "$dir" "docs change"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=0" "a docs-only diff should classify as tier=0"
  assert_contains "$out" "reason=docs-only" "tier=0 reason should be docs-only"
  pass "fm-tier: an all-docs diff classifies as tier=0"
}

test_rename_crossing_docs_boundary_disqualifies_tier0() {
  local dir out
  dir="$TMP_ROOT/renamecross"
  mkrepo "$dir"
  start_change "$dir" rename-cross
  printf 'console.log(1)\n' > "$dir/weird.js"
  commit_all "$dir" "add code"
  git -C "$dir" mv weird.js notes.md
  commit_all "$dir" "rename code to a docs-looking path"
  out=$("$TIER" "$dir" "$(git -C "$dir" rev-parse HEAD~1)")
  assert_not_contains "$out" "tier=0" "renaming code to a docs-looking path must not classify as tier=0"
  pass "fm-tier: a rename crossing the docs boundary disqualifies tier=0"
}

test_tier1_at_the_100_5_boundary() {
  local dir out i
  dir="$TMP_ROOT/t1boundary"
  mkrepo "$dir"
  mkdir -p "$dir/src/__tests__"
  printf 'console.log(0)\n' > "$dir/src/app.js"
  printf "test('x', ()=>{})\n" > "$dir/src/__tests__/app.test.js"
  commit_all "$dir" "seed src with a sibling test tree"
  start_change "$dir" t1-boundary
  for i in a b c d; do printf 'x\n' > "$dir/src/$i.js"; done
  for i in $(seq 1 96); do printf 'line%s\n' "$i" >> "$dir/src/app.js"; done
  commit_all "$dir" "exactly 100 lines across 5 files"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=1" "100 lines / 5 files with a test signal should be tier=1 (at the boundary)"
  assert_contains "$out" "lines=100" "boundary case should report lines=100"
  assert_contains "$out" "files=5" "boundary case should report files=5"
  pass "fm-tier: exactly 100 lines and 5 files (with a test signal) classifies as tier=1"
}

test_tier2_one_line_past_the_boundary() {
  local dir out i
  dir="$TMP_ROOT/t2overboundary"
  mkrepo "$dir"
  mkdir -p "$dir/src/__tests__"
  printf 'console.log(0)\n' > "$dir/src/app.js"
  printf "test('x', ()=>{})\n" > "$dir/src/__tests__/app.test.js"
  commit_all "$dir" "seed src with a sibling test tree"
  start_change "$dir" t2-over
  for i in a b c d; do printf 'x\n' > "$dir/src/$i.js"; done
  for i in $(seq 1 97); do printf 'line%s\n' "$i" >> "$dir/src/app.js"; done
  commit_all "$dir" "101 lines across 5 files"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "101 lines should push past the T1 threshold to tier=2"
  assert_contains "$out" "reason=too-large" "over-the-line-threshold reason should be too-large"
  pass "fm-tier: one line past the 100-line threshold classifies as tier=2 (too-large)"
}

test_tier2_too_many_files() {
  local dir out i
  dir="$TMP_ROOT/t2files"
  mkrepo "$dir"
  mkdir -p "$dir/src/__tests__"
  printf "test('x', ()=>{})\n" > "$dir/src/__tests__/app.test.js"
  commit_all "$dir" "seed sibling test tree"
  start_change "$dir" t2-files
  for i in a b c d e f; do printf 'x\n' > "$dir/src/$i.js"; done
  commit_all "$dir" "six small files"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "6 files should push past the T1 file-count threshold"
  assert_contains "$out" "reason=too-large" "over-the-file-count reason should be too-large"
  pass "fm-tier: 6 changed files (over the 5-file threshold) classifies as tier=2 (too-large)"
}

test_high_risk_forces_tier2_regardless_of_size() {
  local dir out
  dir="$TMP_ROOT/highrisk"
  mkrepo "$dir"
  start_change "$dir" highrisk-change
  mkdir -p "$dir/migrations"
  printf 'ALTER TABLE x ADD y int;\n' > "$dir/migrations/0001.sql"
  commit_all "$dir" "a one-line migration"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a migration file must force tier=2 even at 1 line / 1 file"
  assert_contains "$out" "reason=high-risk:migrations/0001.sql" "high-risk reason should name the offending path"
  pass "fm-tier: a high-risk path (migrations/*.sql) forces tier=2 regardless of diff size"
}

test_pnpm_lockfile_forces_tier2() {
  local dir out
  dir="$TMP_ROOT/pnpmlock"
  mkrepo "$dir"
  start_change "$dir" pnpm-change
  # pnpm-lock.yaml ends in .yaml, which is_code_ext would otherwise claim as
  # code; a dependency lockfile must always force tier=2.
  printf 'lockfileVersion: 9.0\n' > "$dir/pnpm-lock.yaml"
  commit_all "$dir" "a one-line pnpm lockfile change"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a pnpm-lock.yaml change must force tier=2 even at 1 line / 1 file"
  assert_contains "$out" "reason=high-risk:pnpm-lock.yaml" "pnpm lockfile reason should be high-risk"
  pass "fm-tier: pnpm-lock.yaml (a .yaml lockfile) forces tier=2 (high-risk)"
}

test_unknown_binary_forces_tier2() {
  local dir out
  dir="$TMP_ROOT/binary"
  mkrepo "$dir"
  start_change "$dir" binary-change
  head -c 32 /dev/urandom > "$dir/blob.bin"
  commit_all "$dir" "add a binary blob"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a binary file must force tier=2"
  assert_contains "$out" "reason=unknown:blob.bin" "unknown reason should name the offending binary path"
  pass "fm-tier: a binary/unclassifiable file forces tier=2 (unknown)"
}

test_permission_only_change_is_unknown() {
  local dir out
  dir="$TMP_ROOT/chmod"
  mkrepo "$dir"
  printf '#!/bin/sh\n' > "$dir/run.sh"
  git -C "$dir" add run.sh
  git -C "$dir" commit -q -m "add script"
  start_change "$dir" chmod-change
  chmod +x "$dir/run.sh"
  commit_all "$dir" "chmod +x, no content change"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a permission-bit-only change must force tier=2"
  assert_contains "$out" "reason=unknown:run.sh" "permission-bit change reason should be unknown"
  pass "fm-tier: a permission-bit-only change classifies as unknown, forcing tier=2"
}

test_code_change_without_test_signal_is_tier2() {
  local dir out
  dir="$TMP_ROOT/notestsignal"
  mkrepo "$dir"
  start_change "$dir" notest-change
  mkdir -p "$dir/lib"
  printf 'console.log(2)\n' > "$dir/lib/util.js"
  commit_all "$dir" "small code change, no sibling tests anywhere"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a small code change with no test-presence signal must not get tier=1"
  assert_contains "$out" "reason=no-test-signal" "reason should be no-test-signal"
  pass "fm-tier: a small code change with no test-presence signal escalates to tier=2"
}

test_policy_file_additively_forces_high_risk() {
  local dir home out
  dir="$TMP_ROOT/policyproj"
  home="$TMP_ROOT/policyhome"
  mkrepo "$dir"
  mkdir -p "$home/data/tier-policy"
  printf 'special/**\n' > "$home/data/tier-policy/$(basename "$dir")"
  start_change "$dir" policy-change
  mkdir -p "$dir/special"
  printf 'just prose\n' > "$dir/special/notes.md"
  commit_all "$dir" "a docs-looking file under an extra-flagged dir"
  out=$(FM_ROOT_OVERRIDE="$home" "$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "a per-project tier-policy glob must force tier=2 even for an otherwise-docs path"
  assert_contains "$out" "reason=high-risk:special/notes.md" "policy-forced reason should be high-risk"
  pass "fm-tier: the additive per-project tier-policy file forces high-risk on matching paths"
}

test_substringy_code_filenames_are_not_tests() {
  local dir out
  dir="$TMP_ROOT/substringtest"
  mkrepo "$dir"
  start_change "$dir" substring-change
  mkdir -p "$dir/lib"
  # filenames that contain "test"/"spec" only as a substring are code, not
  # tests: without a real test signal a small code change must escalate to T2.
  printf 'console.log(1)\n' > "$dir/lib/latest.py"
  printf 'console.log(2)\n' > "$dir/lib/special.js"
  printf 'console.log(3)\n' > "$dir/lib/respective.rb"
  commit_all "$dir" "substringy-but-not-test code files"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=2" "code files merely containing test/spec as a substring must not count as a test signal"
  assert_contains "$out" "reason=no-test-signal" "no genuine test present, so reason should be no-test-signal"
  pass "fm-tier: latest/special/respective classify as code, not tests"
}

test_real_test_filename_conventions_signal_tier1() {
  local dir out
  dir="$TMP_ROOT/realtestname"
  mkrepo "$dir"
  start_change "$dir" realtest-change
  mkdir -p "$dir/lib"
  # a genuine test-convention filename in the diff supplies the test signal
  # directly (no sibling tree needed), so a small code+test change is tier=1.
  printf 'console.log(1)\n' > "$dir/lib/latest.js"
  printf 'test("x", function(){})\n' > "$dir/lib/latest.test.js"
  commit_all "$dir" "code plus a real .test.js file"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=1" "a real foo.test.js in the diff should supply the test signal for tier=1"
  assert_contains "$out" "reason=code-small" "with a genuine test present the reason should be code-small"
  pass "fm-tier: a real foo.test.js filename is recognised as a test signal"
}

test_no_changes_is_tier0() {
  local dir out
  dir="$TMP_ROOT/nochanges"
  mkrepo "$dir"
  start_change "$dir" empty-change
  git -C "$dir" commit -q --allow-empty -m "no file changes"
  out=$("$TIER" "$dir" main)
  assert_contains "$out" "tier=0" "an empty diff should classify as tier=0"
  pass "fm-tier: an empty diff (no changed files) classifies as tier=0"
}

test_usage_and_notgit_are_errors
test_bad_base_ref_errors_non_zero
test_docs_only_is_tier0
test_rename_crossing_docs_boundary_disqualifies_tier0
test_tier1_at_the_100_5_boundary
test_tier2_one_line_past_the_boundary
test_tier2_too_many_files
test_high_risk_forces_tier2_regardless_of_size
test_pnpm_lockfile_forces_tier2
test_unknown_binary_forces_tier2
test_permission_only_change_is_unknown
test_code_change_without_test_signal_is_tier2
test_policy_file_additively_forces_high_risk
test_substringy_code_filenames_are_not_tests
test_real_test_filename_conventions_signal_tier1
test_no_changes_is_tier0

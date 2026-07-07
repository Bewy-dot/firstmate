#!/usr/bin/env bash
# Deterministic risk/size tier classifier for +tiered projects (AGENTS.md
# project management, delivery mode). Pure git + shell, no agent, no network.
#
# Usage: fm-tier.sh <worktree-or-repo-dir> [base-ref]
#
# Prints exactly one line to stdout on success:
#   tier=0 lines=<n> files=<n> reason=docs-only
#   tier=1 lines=<n> files=<n> reason=code-small
#   tier=2 lines=<n> files=<n> reason=<high-risk:<path>|unknown:<path>|too-large|no-test-signal>
#
# Exits non-zero (message on stderr, nothing on stdout) on ANY internal error.
# Callers MUST treat a non-zero exit, or output that fails to parse, as tier=2:
# the failure direction is always over-gating, never under.
#
# Tiers (report data/pipeline-tier-f3/report.md section 2, captain-decided
# thresholds): T0 docs-only -> every changed file is docs/prose class, no
# renames or unknowns. T1 small/charted -> total changed lines <= 100 AND
# files <= 5 AND every file is code/test/docs AND zero high-risk/unknown files
# AND a test-presence signal holds (a changed file is itself a test, or every
# changed code file's directory has a sibling test tree). T2 is everything
# else, unconditionally, and is the default on any doubt. `review` is never in
# any tier's skip set for code - that is a fixed constraint enforced by the
# caller (bin/fm-brief.sh), not by this classifier.
#
# Renames: this script diffs with --no-renames throughout, so a renamed file
# appears as a plain delete (old path) plus add (new path) rather than a
# single R entry. That means every rename endpoint is classified on its own
# path, which for a fully-renamed file with no content change over-counts
# lines/files versus a rename-aware diff - a deliberately conservative choice
# (over-gates, never under-gates) that also gives the T0 "no renames into or
# out of non-docs paths" rule for free: both endpoints must independently be
# docs-class for T0 to hold.
#
# Per-project extension: an optional data/tier-policy/<project> file (one glob
# per line, '#'-prefixed lines ignored) additively forces matching paths to
# high-risk. It can only add to the built-in whitelist, never remove from it
# or loosen a threshold.
#
# Written for bash 3.2 (macOS system bash): no associative arrays, no ${var,,}.
set -u

die() {
  echo "fm-tier: error: $*" >&2
  exit 1
}

[ "$#" -ge 1 ] && [ "$#" -le 2 ] || die "usage: fm-tier.sh <worktree-or-repo-dir> [base-ref]"

DIR=$1
BASE_ARG=${2:-}

[ -d "$DIR" ] || die "not a directory: $DIR"
DIR=$(cd "$DIR" && pwd -P) || die "cannot resolve directory: $DIR"
git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "not a git work tree: $DIR"
git -C "$DIR" rev-parse --verify --quiet HEAD >/dev/null 2>&1 || die "no HEAD commit in $DIR"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
DATA="${FM_DATA_OVERRIDE:-$FM_HOME/data}"
PROJECT=$(basename "$DIR")
POLICY_FILE="$DATA/tier-policy/$PROJECT"

# Captain-decided T1 "small" thresholds (2026-07-07): <=100 changed lines AND
# <=5 files. Tune here only; a change to these constants ships through this
# project's own gate like any other change.
T1_MAX_LINES=100
T1_MAX_FILES=5

resolve_base() {
  if [ -n "$BASE_ARG" ]; then
    git -C "$DIR" rev-parse --verify --quiet "${BASE_ARG}^{commit}" >/dev/null 2>&1 || return 1
    printf '%s\n' "$BASE_ARG"
    return 0
  fi
  local ref b
  ref=$(git -C "$DIR" symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null || true)
  if [ -n "$ref" ] && git -C "$DIR" rev-parse --verify --quiet "${ref}^{commit}" >/dev/null 2>&1; then
    printf '%s\n' "$ref"
    return 0
  fi
  for b in main master; do
    if git -C "$DIR" show-ref --verify --quiet "refs/heads/$b"; then
      printf '%s\n' "$b"
      return 0
    fi
  done
  return 1
}

BASE=$(resolve_base) || die "cannot determine base ref (pass one explicitly): tried origin/HEAD, main, master"
MERGE_BASE=$(git -C "$DIR" merge-base "$BASE" HEAD 2>/dev/null) || die "cannot compute merge-base of $BASE and HEAD"
[ -n "$MERGE_BASE" ] || die "empty merge-base result for $BASE and HEAD"

TMP=$(mktemp -d "${TMPDIR:-/tmp}/fm-tier.XXXXXX") || die "cannot create temp dir"
trap 'rm -rf "$TMP"' EXIT

if ! git -C "$DIR" diff --numstat --no-renames "$MERGE_BASE" HEAD > "$TMP/numstat" 2>"$TMP/numstat.err"; then
  die "git diff --numstat failed: $(cat "$TMP/numstat.err")"
fi
if ! git -C "$DIR" diff --raw --no-renames "$MERGE_BASE" HEAD > "$TMP/raw" 2>"$TMP/raw.err"; then
  die "git diff --raw failed: $(cat "$TMP/raw.err")"
fi

# --- pass 1: size, file list, binary detection (from --numstat) ------------

TOTAL_LINES=0
FILE_COUNT=0
: > "$TMP/paths"
: > "$TMP/binary"
while IFS=$'\t' read -r ins del path; do
  [ -n "$path" ] || continue
  FILE_COUNT=$((FILE_COUNT + 1))
  printf '%s\n' "$path" >> "$TMP/paths"
  if [ "$ins" = "-" ] || [ "$del" = "-" ]; then
    printf '%s\n' "$path" >> "$TMP/binary"
    continue
  fi
  case "$ins" in *[!0-9]*) die "unparseable numstat insertions for $path: $ins" ;; esac
  case "$del" in *[!0-9]*) die "unparseable numstat deletions for $path: $del" ;; esac
  TOTAL_LINES=$((TOTAL_LINES + ins + del))
done < "$TMP/numstat"

# --- pass 2: mode/type changes (symlink, submodule, type change, chmod) ----
# from --raw: ":<old_mode> <new_mode> <old_sha> <new_sha> <status>\t<path>"

: > "$TMP/mode_unknown"
while IFS=$'\t' read -r meta path; do
  [ -n "$path" ] || continue
  old_mode=$(printf '%s\n' "$meta" | awk '{print $1}' | sed 's/^://')
  new_mode=$(printf '%s\n' "$meta" | awk '{print $2}')
  status=$(printf '%s\n' "$meta" | awk '{print $NF}')
  case "$status" in
    T*) printf '%s\n' "$path" >> "$TMP/mode_unknown"; continue ;;
  esac
  case "$old_mode" in 120000|160000) printf '%s\n' "$path" >> "$TMP/mode_unknown"; continue ;; esac
  case "$new_mode" in 120000|160000) printf '%s\n' "$path" >> "$TMP/mode_unknown"; continue ;; esac
  if [ "$old_mode" != "000000" ] && [ "$new_mode" != "000000" ] && [ "$old_mode" != "$new_mode" ]; then
    printf '%s\n' "$path" >> "$TMP/mode_unknown"
  fi
done < "$TMP/raw"

# --- classification whitelists (report section 2, S2) ----------------------

lc() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }

is_high_risk() {
  local p=$1
  case "$p" in
    migrations/*|*/migrations/*) return 0 ;;
    *.sql) return 0 ;;
    auth/*|*/auth/*) return 0 ;;
    rls/*|*/rls/*) return 0 ;;
    policies/*|*/policies/*) return 0 ;;
    .env|.env.*|*/.env|*/.env.*) return 0 ;;
    secrets*|*/secrets*) return 0 ;;
    *.pem|*.key) return 0 ;;
    .github/workflows/*) return 0 ;;
    .circleci/*|.gitlab-ci.yml|*.gitlab-ci.yml) return 0 ;;
    Dockerfile|Dockerfile.*|*/Dockerfile|*/Dockerfile.*) return 0 ;;
    docker-compose*.yml|docker-compose*.yaml|*/docker-compose*.yml|*/docker-compose*.yaml) return 0 ;;
    package.json|package-lock.json|npm-shrinkwrap.json) return 0 ;;
    */package.json|*/package-lock.json|*/npm-shrinkwrap.json) return 0 ;;
    *.lock) return 0 ;;
    go.mod|go.sum|*/go.mod|*/go.sum) return 0 ;;
    Gemfile|*/Gemfile) return 0 ;;
    requirements*.txt|*/requirements*.txt) return 0 ;;
    Pipfile|*/Pipfile) return 0 ;;
    pyproject.toml|*/pyproject.toml) return 0 ;;
    Cargo.toml|*/Cargo.toml) return 0 ;;
  esac
  return 1
}

is_docs() {
  local p=$1
  case "$p" in
    *.md|*.mdx|*.txt|*.rst) return 0 ;;
    docs/*|*/docs/*) return 0 ;;
    LICENSE|LICENSE.*|*/LICENSE|*/LICENSE.*) return 0 ;;
    CODEOWNERS|*/CODEOWNERS) return 0 ;;
  esac
  return 1
}

# is_test_token <string>: true when the string, split into word tokens on the
# separators / . _ - , contains a token that IS exactly test(s) or spec(s), so
# real path-component / filename conventions match (tests/, __tests__/,
# foo.test.js, foo_test.py, test_foo.py, foo.spec.ts, spec_helper.rb) while a
# bare substring (latest.py, special.js, inspector.ts, respective.rb) does not.
is_test_token() {
  lc "$1" | tr '/._-' '\n\n\n\n' | grep -qxE 'tests?|specs?'
}

is_test() {
  is_test_token "$1"
}

is_code_ext() {
  local lcp
  lcp=$(lc "$1")
  case "$lcp" in
    *.js|*.jsx|*.mjs|*.cjs|*.ts|*.tsx|*.py|*.rb|*.go|*.java|*.kt|*.kts|*.scala)  return 0 ;;
    *.c|*.h|*.cc|*.cpp|*.hpp|*.cxx|*.rs|*.swift|*.php|*.cs|*.m|*.mm)             return 0 ;;
    *.sh|*.bash|*.zsh|*.ps1)                                                    return 0 ;;
    *.html|*.htm|*.css|*.scss|*.sass|*.less)                                    return 0 ;;
    *.json|*.yml|*.yaml|*.toml|*.ini|*.cfg|*.xml|*.proto)                       return 0 ;;
  esac
  return 1
}

policy_matches() {
  local p=$1 pat
  [ -f "$POLICY_FILE" ] || return 1
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    case "$pat" in \#*) continue ;; esac
    # shellcheck disable=SC2254  # deliberate: $pat is a glob pattern from the
    # per-project policy file, meant to be matched as a case pattern, not a
    # literal string.
    case "$p" in
      $pat) return 0 ;;
    esac
  done < "$POLICY_FILE"
  return 1
}

classify_one() {
  local p=$1
  if policy_matches "$p" || is_high_risk "$p"; then echo high-risk; return; fi
  if grep -Fxq -- "$p" "$TMP/mode_unknown" 2>/dev/null; then echo unknown; return; fi
  if grep -Fxq -- "$p" "$TMP/binary" 2>/dev/null; then echo unknown; return; fi
  if is_docs "$p"; then echo docs; return; fi
  if is_test "$p"; then echo test; return; fi
  if is_code_ext "$p"; then echo code; return; fi
  echo unknown
}

sibling_tests_exist() {
  local d=$1 target entry
  target="$DIR/$d"
  [ -d "$target" ] || target="$DIR"
  # -iname is a cheap superset prefilter; is_test_token then refines each hit to
  # the same token convention, so a sibling merely containing "test"/"spec" as a
  # substring (latest.js) is not counted as a test tree.
  find "$target" -maxdepth 2 \( -iname '*test*' -o -iname '*spec*' \) 2>/dev/null | {
    while IFS= read -r entry; do
      if is_test_token "$(basename "$entry")"; then
        echo y
        break
      fi
    done
  } | grep -q y
}

# --- classify every changed file --------------------------------------------

ALL_DOCS=1
HIGH_RISK_PATH=""
UNKNOWN_PATH=""
HAS_TEST_SIGNAL=0
: > "$TMP/code_dirs"

while IFS= read -r p; do
  [ -n "$p" ] || continue
  cls=$(classify_one "$p")
  case "$cls" in
    high-risk)
      ALL_DOCS=0
      [ -n "$HIGH_RISK_PATH" ] || HIGH_RISK_PATH=$p
      ;;
    unknown)
      ALL_DOCS=0
      [ -n "$UNKNOWN_PATH" ] || UNKNOWN_PATH=$p
      ;;
    docs)
      : ;;
    test)
      ALL_DOCS=0
      HAS_TEST_SIGNAL=1
      ;;
    code)
      ALL_DOCS=0
      dirname "$p" >> "$TMP/code_dirs"
      ;;
  esac
done < "$TMP/paths"

# --- tier decision -----------------------------------------------------------

if [ "$FILE_COUNT" -eq 0 ]; then
  echo "tier=0 lines=0 files=0 reason=no-changes"
  exit 0
fi

if [ "$ALL_DOCS" -eq 1 ]; then
  echo "tier=0 lines=$TOTAL_LINES files=$FILE_COUNT reason=docs-only"
  exit 0
fi

if [ -n "$HIGH_RISK_PATH" ]; then
  echo "tier=2 lines=$TOTAL_LINES files=$FILE_COUNT reason=high-risk:$HIGH_RISK_PATH"
  exit 0
fi

if [ -n "$UNKNOWN_PATH" ]; then
  echo "tier=2 lines=$TOTAL_LINES files=$FILE_COUNT reason=unknown:$UNKNOWN_PATH"
  exit 0
fi

if [ "$TOTAL_LINES" -le "$T1_MAX_LINES" ] && [ "$FILE_COUNT" -le "$T1_MAX_FILES" ]; then
  S3_OK=1
  if [ "$HAS_TEST_SIGNAL" -ne 1 ] && [ -s "$TMP/code_dirs" ]; then
    while IFS= read -r d; do
      [ -n "$d" ] || continue
      if ! sibling_tests_exist "$d"; then
        S3_OK=0
        break
      fi
    done < "$TMP/code_dirs"
  fi
  if [ "$S3_OK" -eq 1 ]; then
    echo "tier=1 lines=$TOTAL_LINES files=$FILE_COUNT reason=code-small"
    exit 0
  fi
  echo "tier=2 lines=$TOTAL_LINES files=$FILE_COUNT reason=no-test-signal"
  exit 0
fi

echo "tier=2 lines=$TOTAL_LINES files=$FILE_COUNT reason=too-large"
exit 0

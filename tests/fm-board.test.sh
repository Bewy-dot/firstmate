#!/usr/bin/env bash
# Portable board regressions; every write uses a temporary FM_HOME.
set -eu
# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
python3 "$ROOT/tests/fm-board.test.py"

#!/usr/bin/env bash
# Runtime check: run a test command with an empty TMPDIR, and fail if it leaves anything there.
#
#   check_test_tmp.sh [--ignore NAME]... -- COMMAND [ARGS...]
#
# NAME: an entry allowed to remain (a glob, e.g. node-compile-cache: Node's own cache, one dir reused every run).
# The command's own exit code is passed through when it fails; a clean, passing run prints "check-test-tmp: OK".
# Leftovers print "LEFT <name>" and exit 1. Only programs that honour TMPDIR are covered (Python's tempfile, Node's
# os.tmpdir(), mktemp); one that hard-codes /tmp is not.
set -uo pipefail
ignore=()
while (($#)); do
    case $1 in
        --ignore) ignore+=("$2"); shift 2 ;;
        --) shift; break ;;
        *) echo "check_test_tmp.sh: unexpected '$1' (usage: [--ignore NAME]... -- COMMAND)" >&2; exit 2 ;;
    esac
done
(($#)) || { echo "check_test_tmp.sh: no command given" >&2; exit 2; }
base=$(mktemp -d); trap 'rm -rf "$base"' EXIT
mkdir "$base/tmp"
TMPDIR="$base/tmp" "$@"
rc=$?
if ((rc)); then echo "check-test-tmp: the command failed (exit $rc)"; exit "$rc"; fi
left=()
shopt -s dotglob nullglob
for p in "$base/tmp"/*; do
    n=${p##*/}
    for g in "${ignore[@]}"; do [[ $n == $g ]] && continue 2; done
    left+=("$n")
done
if ((${#left[@]})); then
    printf 'LEFT   %s\n' "${left[@]:0:20}"
    ((${#left[@]} > 20)) && echo "       ... and $((${#left[@]} - 20)) more"
    echo "check-test-tmp: FAILED (${#left[@]} entries left in TMPDIR)"
    exit 1
fi
echo "check-test-tmp: OK"

# shellcheck shell=bash
# Which directory names under operator/customers/ are NOT seats. Sourced, never run.
#
# The bash home of the one seat rule. The Python home is
# operator/bin/lib/seat_dirs.py and the TypeScript home is
# scripts/lib/seat-inventory.ts; tests/seat-inventory.test.ts runs all three
# over one fixture tree and fails if they disagree. The CI projection scripts
# (ci-sync, ci-publish, ci-reconcile-customer-configs) each carried their own
# copy of this test, and ci-sync had no tombstone case, so a decommissioned
# seat's `<slug>.decommissioned.<date>` dir reached its slug guard as if it were
# an authored seat and failed the job.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/seat-dir.sh"
#   if reason=$(seat_dir_skip_reason "$slug"); then echo "skip ($reason)"; fi
#
# Prints scaffold | hidden | tombstone and returns 0 when the name is not a seat;
# prints nothing and returns 1 when it could be one. A name that passes still
# needs a customer.yaml and a valid slug; the callers keep their own slug guards,
# because those protect SQL literals and R2 keys and must stay loud.
# bash 3.2 compatible (ci-publish-customer-configs.sh runs on a laptop too).

seat_dir_skip_reason() {
  local name="$1"
  case "$name" in
  _*)
    echo scaffold
    return 0
    ;;
  .*)
    echo hidden
    return 0
    ;;
  *.decommissioned.*)
    echo tombstone
    return 0
    ;;
  esac
  return 1
}

# seat_dir_of_path <path>: the directory name a repo path belongs to when the
# path is exactly operator/customers/<name>/<file>. Prints <name> and returns 0;
# prints nothing and returns 1 for any other path, deeper ones included. A git
# pathspec or `case` glob `operator/customers/*/customer.yaml` also matches the
# presets of the one template (operator/customers/_template/presets/<preset>/
# customer.yaml, ADR 0100), and `basename "$(dirname ...)"` would read each as
# a seat named after its preset. Callers still run seat_dir_skip_reason on the
# name this prints.
seat_dir_of_path() {
  local rest="${1#operator/customers/}"
  [[ "$rest" != "$1" ]] || return 1
  case "$rest" in
  */*/*) return 1 ;;
  ?*/?*)
    printf '%s\n' "${rest%%/*}"
    return 0
    ;;
  esac
  return 1
}

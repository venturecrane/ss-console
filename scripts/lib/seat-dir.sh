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

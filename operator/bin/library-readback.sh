#!/usr/bin/env bash
# library-readback.sh: one seat's read-back of its filed firm documents, from a laptop.
#
# WHY (2026-11-07, design 2026-11-06 piece 10). The live proof of a firm's
# document library is the first real request, read back by code: after each
# delivered drafting or demand job the root daemon scores the filed documents
# against their library record (jobs/library_readback) and keeps the score.
# This prints, per test (a made in the file, b attorney, c reader, d firm edit,
# e rules), how many jobs pass, fail, are not applicable or not recorded, and
# the newest job id in each. Job ids and counts only, never a name or a word.
#
# Usage:
#   operator/bin/library-readback.sh <slug> status [--json]
#
# READ ONLY. Runs as root on the seat by the same route as lane-proof.sh (the
# job tree is root's), starting from `env -i` so no secret in the ssh session's
# environment reaches the command. The command writes nothing and calls no
# model. To score one saved job folder at $0, in the client ops workspace:
#   jobs library-readback replay <job_dir> [--spec DIR] [--roster DIR] [--drafting-inputs DIR] [--demand-inputs DIR]
set -euo pipefail

SLUG="${1:-}"
ACTION="${2:-}"
[ -n "${SLUG}" ] && [ "${ACTION}" = "status" ] || {
  echo "usage: $0 <slug> status [--json]" >&2
  exit 2
}
shift 2
if [[ ! "${SLUG}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "invalid slug '${SLUG}'" >&2
  exit 2
fi
FLAGS=()
for arg in "$@"; do
  case "${arg}" in
    --json) FLAGS+=("--json") ;;
    *)
      echo "unknown flag '${arg}' (only --json)" >&2
      exit 2
      ;;
  esac
done
fly ssh console -a "hermes-${SLUG}" --pty=false \
  -C "env -i PATH=/usr/bin:/bin /opt/jobs/.venv/bin/jobs library-readback status ${FLAGS[*]:-}"

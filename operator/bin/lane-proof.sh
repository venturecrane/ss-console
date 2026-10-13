#!/usr/bin/env bash
# lane-proof.sh: SMD's hands on a seat's lane-proof gate, from a laptop.
#
# WHY (2026-10-13). A job of a kind not proven on a seat waits, and SMD is
# paged (a lane_unproven request card) the minute it is asked for. This is the
# path from that page to a released job within hours: run the held job's
# pipeline as a PROOF RUN on its own matter (nothing written to the firm,
# nothing emailed, no ledger row moved), read what it produced, accept it.
# Runbook: docs/runbooks/operator/lane-proof.md.
#
# Usage:
#   operator/bin/lane-proof.sh <slug> status
#   operator/bin/lane-proof.sh <slug> dry-run <job_id> [--wait SECONDS]
#   operator/bin/lane-proof.sh <slug> pull <job_id> [DEST]
#   operator/bin/lane-proof.sh <slug> accept <job_id> --evidence vfy_... --by <name>
#   operator/bin/lane-proof.sh <slug> revoke <lane-key> --why "..." --by <name>
#
# Runs as ROOT on the seat, on purpose and unlike seat-probe.sh: the proof
# record lives in the root-owned medchron tree so that no other uid (the
# agent's above all) can prove a lane. The command is the runner's own
# `medchron lane-proof`, which writes only that record and the proof-run
# marker; it starts from `env -i`, so no secret in the ssh session's
# environment reaches it or its argv.
#
# `pull` copies a proof run's twin (the rendered documents, the reports) to
# DEST, default ${TMPDIR}/lane-proof/<slug>/<job_id>. It is client material:
# DEST is refused inside any git checkout, so it can never be committed here.
set -euo pipefail

SLUG="${1:-}"
ACTION="${2:-}"
[ -n "${SLUG}" ] && [ -n "${ACTION}" ] || {
  echo "usage: $0 <slug> status | dry-run <job> [--wait S] | pull <job> [DEST] | accept <job> --evidence vfy_... --by NAME | revoke <key> --why ... --by NAME" >&2
  exit 2
}
shift 2
if [[ ! "${SLUG}" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "invalid slug '${SLUG}'" >&2
  exit 2
fi
APP="hermes-${SLUG}"
MEDCHRON="/opt/medchron/.venv/bin/medchron"
RUN_DIR="/run/smd-medchron"

on_seat() {
  local quoted=""
  for arg in "$@"; do quoted+=" $(printf '%q' "${arg}")"; done
  fly ssh console -a "${APP}" --pty=false -C "env -i PATH=/usr/bin:/bin ${quoted}"
}

case "${ACTION}" in
  status | dry-run | accept | revoke)
    on_seat "${MEDCHRON}" lane-proof "${ACTION}" "$@"
    ;;
  pull)
    JOB="${1:-}"
    [[ "${JOB}" =~ ^[0-9A-HJKMNP-TV-Z]{26}$ ]] || { echo "pull needs a job id" >&2; exit 2; }
    DEST="${2:-${TMPDIR:-/tmp}/lane-proof/${SLUG}/${JOB}}"
    mkdir -p "${DEST}"
    if git -C "${DEST}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      echo "refused: ${DEST} is inside a git checkout; proof artifacts are client material" >&2
      exit 2
    fi
    # The newest twin of that job, whichever lane ran it.
    TWIN="$(on_seat sh -c "ls -1d ${RUN_DIR}/lane-proofs/*/*/${JOB} 2>/dev/null | sort | tail -1")"
    [ -n "${TWIN}" ] || { echo "no proof run of ${JOB} on ${SLUG}" >&2; exit 1; }
    on_seat tar -C "$(dirname "${TWIN}")" -czf - --exclude "${JOB}/data/raw" --exclude "${JOB}/data/*/raw" \
      "${JOB}" proof.json 2>/dev/null >"${DEST}/twin.tgz" || true
    tar -C "${DEST}" -xzf "${DEST}/twin.tgz" && rm -f "${DEST}/twin.tgz"
    echo "pulled ${JOB}'s proof run to ${DEST}"
    find "${DEST}" -type f \( -name '*.docx' -o -name '*.xlsx' -o -name 'proof.json' -o -name 'dry-run-report.txt' \) | sort
    ;;
  *)
    echo "unknown action '${ACTION}' (status, dry-run, pull, accept, revoke)" >&2
    exit 2
    ;;
esac

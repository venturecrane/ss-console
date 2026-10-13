#!/usr/bin/env bash
# entrypoint-lane-proofs.sh: the lane-proof gate's boot-time files, SOURCED by
# entrypoint.sh after the lane entrypoints (same shell, as root, before the
# broker and the medchron daemon launch). It reads MEDCHRON_DATA_DIR,
# CUSTOMER_SLUG, the R2_* env and _seed_endpoint from the entrypoint.
# shellcheck shell=bash
# shellcheck disable=SC2154 # _seed_endpoint, CUSTOMER_SLUG and the R2_* names are set by entrypoint.sh, which sources this file

# The kinds of job proven on this seat (runners/medchron/medchron/lane_proof.py):
# the AUTHORED seed from vaults/<slug>/lane-proofs.yaml (staged by the
# provisioner from operator/customers/<slug>/lane-proofs.yaml), refreshed every
# boot, the existing copy kept on a failed fetch (fail-static, like the firm
# inputs). A seat with no seed proves nothing, so its jobs wait and SMD is
# paged. Beside it the RUNTIME events
# (lane-proofs.runtime.json), written only by `medchron lane-proof` as root and
# never touched here: they survive a reprovision on the volume by design.
# Root-owned 0644 in the root 0755 tree: the daemon and the gate read them, and
# no other uid can write them (an agent that could would prove its own lanes).
LANE_PROOFS="${MEDCHRON_DATA_DIR}/lane-proofs.yaml"
rm -f "${LANE_PROOFS}.r2.tmp"
if AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID:?}" \
     AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY:?}" \
       aws s3 cp \
         --endpoint-url "${_seed_endpoint}" \
         --only-show-errors \
         "s3://${R2_BUCKET_CONFIG}/vaults/${CUSTOMER_SLUG}/lane-proofs.yaml" \
         "${LANE_PROOFS}.r2.tmp" 2>/dev/null; then
  mv "${LANE_PROOFS}.r2.tmp" "${LANE_PROOFS}"
  log "lane-proofs.yaml refreshed from R2"
elif [ -f "${LANE_PROOFS}" ]; then
  # Absent from the vault and an R2 outage look alike from here; keeping the
  # last copy is the fail-static choice. A lane is un-proven by a revocation
  # (authored or `medchron lane-proof revoke`), never by deleting the seed.
  log "WARN: no lane-proofs.yaml fetched from R2; keeping the existing copy"
else
  log "No lane-proofs.yaml for ${CUSTOMER_SLUG}: every job waits for a proof run"
fi
rm -f "${LANE_PROOFS}.r2.tmp"
for _f in "${LANE_PROOFS}" "${MEDCHRON_DATA_DIR}/lane-proofs.runtime.json"; do
  if [ -f "${_f}" ]; then chown root:root "${_f}"; chmod 0644 "${_f}"; fi
done
# The holds the daemon notes (root only) and the proof runs' twins: root,
# group medchron execute-only, like jobs/ (the child traverses to its own dir).
install -d -o root -g root -m 0700 "${MEDCHRON_DATA_DIR}/lane-holds"
install -d -o root -g medchron -m 0710 "${MEDCHRON_DATA_DIR}/lane-proofs"

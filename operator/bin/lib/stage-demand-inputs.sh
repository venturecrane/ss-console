#!/usr/bin/env bash
# stage-demand-inputs.sh: the demand job's firm-inputs staging block, sourced by
# provision-customer.sh in the same shell (it reads SLUG, REPO_ROOT, the R2_*
# operator env, SS_ENGAGEMENTS_DIR, and calls die / log from there). Its own
# file from the start because the provisioner sits at its shell size ratchet
# (tests/operator-module-size.test.ts): the ratchet's instruction is to split.
# shellcheck shell=bash

# ---------- Step 2d: the demand job's firm inputs (2026-10-06) ----------
# The demand job (docs/specs/operator/demand-drafting-routine.md) reads the
# firm's voice profile, fixed strings, skeleton, prompts and house reference
# .docx, all private, all in engagements operator/customers/<slug>/demand/, and
# demand-firm.yaml pins every one by sha256. Validated against THIS checkout's
# runner (python -m jobs.demand.firm: schema AND pins), refused unless
# demand-firm.yaml is engagements origin/main (its pins then make every pinned
# file main's too), and SYNCED with --delete to vaults/<slug>/demand/, so a file
# removed from the firm inputs is removed from the vault (gone means gone). The
# entrypoint pulls the prefix into a root-owned tree on every boot. A seat with
# no demand inputs runs no demand jobs; the demand lane defers them.
# The write itself (validation, the key guard, the --delete sync, the read-back)
# is lib/publish-firm-inputs.sh, the one writer, which the engagements repo's
# merge workflow also runs (2026-11-05): provisioning and a merge publish the
# same bytes the same way. This block keeps only the engagements-main gate.
# >>> demand-inputs-stage
DEMAND_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/demand"
if [ -f "${DEMAND_INPUTS_DIR}/demand-firm.yaml" ]; then
  _eng="${DEMAND_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/demand/demand-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${DEMAND_INPUTS_DIR}/demand-firm.yaml")" ]; then
    die "the demand firm inputs at ${DEMAND_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  R2_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" R2_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
  R2_ENDPOINT_URL="${R2_ENDPOINT_URL}" R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG}" \
    bash "${BIN_DIR}/lib/publish-firm-inputs.sh" "${SLUG}" demand "${DEMAND_INPUTS_DIR}" --runner-checkout "${REPO_ROOT}" \
    || die "the demand firm inputs for ${SLUG} were not published (publish-firm-inputs exit $?: 1 refused by this checkout's runner, 3 R2, 4 the validator could not run; the lines above say which)"
  log "R2 upload OK (demand firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/demand/demand-firm.yaml"
  log "No demand firm inputs for ${SLUG} (${DEMAND_INPUTS_DIR}); the demand lane will defer demand jobs"
fi
# <<< demand-inputs-stage

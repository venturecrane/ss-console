#!/usr/bin/env bash
# stage-marketing-inputs.sh: the marketing seat's firm registers staging block,
# sourced by provision-customer.sh in the same shell (it reads SLUG, REPO_ROOT,
# the R2_* operator env, SS_ENGAGEMENTS_DIR, and calls die / log from there).
# Its own file because the provisioner sits at its shell size ratchet
# (tests/operator-module-size.test.ts).
# shellcheck shell=bash

# ---------- Step 2g: the marketing seat's advertising-gate registers (ADR 0098) ----------
# The California attorney-advertising gate checks every draft against the
# firm's authored registers: its names, office cities, lawyer roster, and the
# results, testimonials, awards and fee terms it may cite (each empty list
# blocks any claim that needs it). They are the firm's private record, so they
# live in engagements operator/customers/<slug>/marketing/marketing-firm.yaml,
# never here. Validated with the gate's own loader from THIS checkout
# (operator/templates/drafting/advertising_gate_registers.py: a file the gate
# would refuse is refused here instead), refused unless it is engagements
# origin/main, and SYNCED with --delete to vaults/<slug>/marketing/. The
# entrypoint (entrypoint-marketing.sh) pulls the prefix into a root-owned tree
# on every boot. A seat with no registers runs no marketing drafts: the gate
# refuses each one, and says so.
# >>> marketing-inputs-stage
MARKETING_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/marketing"
if [ -f "${MARKETING_INPUTS_DIR}/marketing-firm.yaml" ]; then
  _eng="${MARKETING_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/marketing/marketing-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${MARKETING_INPUTS_DIR}/marketing-firm.yaml")" ]; then
    die "the marketing registers at ${MARKETING_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  uv run --quiet --with pyyaml python3 "${REPO_ROOT}/operator/bin/lib/check-marketing-registers.py" \
      "${MARKETING_INPUTS_DIR}/marketing-firm.yaml" \
    || die "the marketing registers for ${SLUG} do not load under this checkout's advertising gate (see the line above); not uploading them"
  log "Syncing the marketing registers to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/marketing/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${MARKETING_INPUTS_DIR}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/marketing/" --delete \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || die "R2 sync of the marketing registers failed"
  log "R2 upload OK (marketing registers)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/marketing/marketing-firm.yaml"
  log "No marketing registers for ${SLUG} (${MARKETING_INPUTS_DIR}); the advertising gate will refuse every marketing draft"
fi
# <<< marketing-inputs-stage

#!/usr/bin/env bash
# stage-drafting-inputs.sh: the drafting job's firm-inputs staging block,
# sourced by provision-customer.sh in the same shell right after the demand
# block (it reads SLUG, REPO_ROOT, the R2_* operator env, SS_ENGAGEMENTS_DIR,
# and calls die / log from there). Its own file for the reason the demand one
# is: the provisioner sits at its shell size ratchet.
# shellcheck shell=bash

# ---------- Step 2e: the drafting job's firm inputs (2026-10-07) ----------
# The drafting job (runners/medchron/medchron/drafting/) reads the firm's house
# style, per-class skeletons, prompts and exemplars, and the served attachments,
# all private, all in engagements operator/customers/<slug>/drafting/, and
# drafting-firm.yaml pins every one by sha256. Validated against THIS
# checkout's runner (python -m medchron.drafting.firm: schema AND pins),
# refused unless drafting-firm.yaml is engagements origin/main, and SYNCED with
# --delete to vaults/<slug>/drafting/ (gone means gone). The entrypoint pulls
# the prefix into a root-owned tree on every boot. A seat with no drafting
# inputs is skipped quietly here; its drafting lane defers drafting jobs.
# >>> drafting-inputs-stage
DRAFTING_INPUTS_DIR="${SS_ENGAGEMENTS_DIR:-${HOME}/dev/engagements}/operator/customers/${SLUG}/drafting"
if [ -f "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml" ]; then
  _eng="${DRAFTING_INPUTS_DIR%/operator/customers/*}"
  _rel="operator/customers/${SLUG}/drafting/drafting-firm.yaml"
  if [ "${SS_ALLOW_DIVERGENT_SOURCE:-}" != "1" ] \
     && [ "$(git -C "${_eng}" show "origin/main:${_rel}" 2>/dev/null)" != "$(cat "${DRAFTING_INPUTS_DIR}/drafting-firm.yaml")" ]; then
    die "the drafting firm inputs at ${DRAFTING_INPUTS_DIR} are not engagements origin/main; merge them first (an unmerged file is an unreviewed one)"
  fi
  # CLIENT DOCUMENTS ARE NOT IN THE REPO (Law 15, 2026-10-17). A pinned input
  # the firm supplied (its own past briefs used as exemplars) lives only in R2,
  # where the seat reads it, and in the client's ops workspace. A pinned input
  # missing from the checkout is taken from R2 into a private temp copy, so the
  # pin check below still proves the exact bytes and the --delete sync keeps
  # it; the temp copy is removed when this block ends. An input missing from
  # BOTH places still fails the pin check, as before.
  _drafting_stage="$(mktemp -d)"
  cp -R "${DRAFTING_INPUTS_DIR}/." "${_drafting_stage}/"
  _missing="$(uv run --quiet --with pyyaml python3 - "${_drafting_stage}" <<'PY'
import sys, pathlib, yaml
root = pathlib.Path(sys.argv[1])
data = yaml.safe_load((root / "drafting-firm.yaml").read_text(encoding="utf-8")) or {}
for rel in (data.get("inputs") or {}):
    if not (root / rel).is_file():
        print(rel)
PY
)" || { rm -rf "${_drafting_stage}"; die "could not read the drafting inputs list for ${SLUG}"; }
  while IFS= read -r _rel_in; do
    [ -n "${_rel_in}" ] || continue
    mkdir -p "$(dirname "${_drafting_stage}/${_rel_in}")"
    AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
      aws s3 cp "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/drafting/${_rel_in}" "${_drafting_stage}/${_rel_in}" \
        --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
      || log "WARN pinned drafting input ${_rel_in} is in neither the checkout nor R2"
  done <<< "${_missing}"
  PYTHONPATH="${REPO_ROOT}/operator/runners/medchron" \
    uv run --quiet --with pyyaml python3 -m medchron.drafting.firm "${_drafting_stage}" \
    || { rm -rf "${_drafting_stage}"; die "the drafting firm inputs for ${SLUG} do not validate against this checkout's runner (see the line above); not uploading them"; }
  log "Syncing the drafting firm inputs to R2: s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/drafting/"
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws s3 sync "${_drafting_stage}/" "s3://${R2_BUCKET_CONFIG}/vaults/${SLUG}/drafting/" --delete \
      --exclude "eval/*" --exclude "request-brief-*" \
      --endpoint-url "${R2_ENDPOINT_URL}" --only-show-errors \
    || { rm -rf "${_drafting_stage}"; die "R2 sync of the drafting firm inputs failed"; }
  rm -rf "${_drafting_stage}"
  log "R2 upload OK (drafting firm inputs)"
else
  assert_lane_not_on_main "operator/customers/${SLUG}/drafting/drafting-firm.yaml"
fi
# <<< drafting-inputs-stage

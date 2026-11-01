#!/usr/bin/env bash
# Reconciler, READ-ONLY: prove that what every seat boots from in R2 is what
# main authors, and page when it is not (ss #2305; ADR 0094 as amended by
# Wave 2 W4). It never writes R2. There is no `s3 cp <file> s3://`, no put, no
# delete anywhere in this file, and tests/r2-config-reconcile.test.ts runs it
# against a stub that records every aws call and asserts none of them writes.
#
# WHY READ-ONLY. Until W4 this job republished HEAD's bytes on drift, which made
# it a second writer of vaults/<slug>/customer.yaml. A second writer of a key
# carries its own copy of every guard or undoes the first writer's refusals
# (it did: #3397). The one writer is now ci-publish-customer-configs.sh, which
# converges every seat on every push to main; this job is the alarm that tells
# a human when that writer did not land, and it names the push that will heal it.
#
# PER SEAT on HEAD's tree (scaffolds and tombstones skipped by scripts/lib/seat-dir.sh):
#   DRIFT    the object's bytes are not the blob's bytes.
#   REFUSED  HEAD's customer.yaml names what the seat's running image lacks
#            (the image guard, scripts/lib/seat-spec.ts). It will not converge
#            until the seat runs an image that carries it.
#   SPEC     the signed seat spec is missing, does not verify against the
#            committed key, or differs from what HEAD compiles (stale), or HEAD's
#            spec is refused for a lane schema the seat's image does not run.
# and one warn-only observation:
#   UNAUTHORED  an object under vaults/ whose customer.yaml is gone from HEAD.
#
# NEVER CREATES, NEVER DELETES, NEVER WRITES: a 404 on the object is a skip with
# a warning; any other read failure is "cannot evaluate" (exit 1), never clean.
#
# The shallow refusal is load-bearing: a finding names the commit and date that
# authored the bytes R2 should hold, and in a shallow slice `git log -1` names
# the newest commit in the slice instead.
#
# bash 3.2 compatible (exercised on macOS operator boxes).
#
# Inputs (env): SEAT_SPEC_ENABLED=true to judge the signed spec (SPEC findings);
# R2_BUCKET_CONFIG (default smd-customer-config); FLEET_IMAGE_LOOKUP=1
# to resolve each seat's image through D1. R2_RECONCILE_DRY_RUN is accepted and
# changes nothing: every run is read-only. R2 auth: scripts/lib/r2-creds.sh.
#
# Exit codes (the #2292 HOLDS-vs-FINDS contract):
#   0  converged   1  cannot evaluate   2  findings (the workflow opens an issue)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/seat-dir.sh
source "${SCRIPT_DIR}/lib/seat-dir.sh"
# shellcheck source=lib/r2-creds.sh
source "${SCRIPT_DIR}/lib/r2-creds.sh"

R2_CONFIG_BASENAME="customer.yaml"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"

if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo unknown)" != "false" ]]; then
  echo "::error::refusing to reconcile from a shallow clone (the checkout needs fetch-depth: 0)"
  exit 1
fi
head_sha=$(git rev-parse HEAD 2>/dev/null || true)
if [[ -z "$head_sha" ]]; then
  echo "::error::could not resolve HEAD; nothing to reconcile against"
  exit 1
fi
# The comparison bytes are the checked-out files, honest only while the checkout
# IS what HEAD authored.
if ! dirty=$(git status --porcelain -- operator/customers 2>/dev/null); then
  echo "::error::could not read the working-tree status of operator/customers"
  exit 1
fi
if [[ -n "$dirty" ]]; then
  echo "::error::operator/customers is not clean at HEAD; refusing to reconcile against a working tree main did not author"
  printf '%s\n' "$dirty" | sed 's/^/    /'
  exit 1
fi
echo "Reconciling s3://${R2_BUCKET_CONFIG}/vaults/*/ against HEAD ${head_sha} (read-only)"

command -v aws >/dev/null 2>&1 || {
  echo "::error::aws CLI not found (required to read R2)"
  exit 1
}
derive_r2_creds

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

# shellcheck source=lib/seat-spec-inputs.sh
source "${SCRIPT_DIR}/lib/seat-spec-inputs.sh"

digest() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi
}

work_dir=$(mktemp -d)
trap 'rm -rf "${work_dir}"' EXIT

if ! tree_paths=$(git ls-tree -r --name-only HEAD -- operator/customers 2>/dev/null); then
  echo "::error::could not list operator/customers at HEAD"
  exit 1
fi
paths=()
while IFS= read -r _line; do
  case "$_line" in */customer.yaml) seat_dir_of_path "$_line" >/dev/null && paths+=("$_line") ;; esac
done <<<"$tree_paths"
if [[ ${#paths[@]} -eq 0 ]]; then
  echo "::error::HEAD carries no operator/customers/*/customer.yaml; that is not a converged state, it is an unreadable one"
  exit 1
fi

hard_fail=0
checked=0
n_drift=0
n_missing=0
n_unauthored=0
n_refused=0
n_spec=0
findings=""
authored_slugs=" "
HEAL="the next push to main republishes it (ci-publish-customer-configs.sh converges every seat), or re-run the Deploy workflow's publish job"

add_finding() { findings="${findings}$1
"; }

# The seat's signed spec, judged against what HEAD compiles from the same inputs.
check_spec() {
  local slug="$1" cfg="$2" inputs="$3" rc=0 why
  seat_spec_compile "$slug" "$cfg" "$inputs" "${work_dir}/${slug}.out" >"${work_dir}/${slug}.compile" 2>&1 || rc=$?
  case "$rc" in
    1)
      n_refused=$((n_refused + 1))
      add_finding "  REFUSED  ${slug}: HEAD's customer.yaml names what the seat's running image lacks; the image guard refused:
$(sed 's/^/             /' "${work_dir}/${slug}.compile")"
      return 0
      ;;
    3) why="HEAD's spec is refused: $(grep '^  lane' "${work_dir}/${slug}.compile" | sed 's/^  //' | tr '\n' ' ')" ;;
    0) why="the published spec differs from what HEAD compiles (stale or missing); ${HEAL}" ;;
    10) why="" ;;
    *)
      echo "::error::${slug}: the spec compiler failed (exit ${rc})"
      sed 's/^/    /' "${work_dir}/${slug}.compile"
      hard_fail=1
      return 0
      ;;
  esac
  # Signal, not volume: until signing is enabled (SEAT_SPEC_ENABLED, the same
  # repository variable that gates the publisher's key) there is no signed spec
  # to expect, so its absence or staleness is not a finding. The image guard
  # above (REFUSED) and the customer.yaml byte comparison run either way.
  if [[ "${SEAT_SPEC_ENABLED:-}" != "true" ]]; then
    echo "${slug}: seat-spec signing is not enabled (SEAT_SPEC_ENABLED); the signed spec is not judged"
    return 0
  fi
  if [[ -z "$why" && -f "${inputs}/spec-pointer.json" ]]; then
    if ! why=$(_seat_tsx "$SEAT_SPEC_COMPILER" verify --pointer "${inputs}/spec-pointer.json" --spec "${inputs}/current-spec.json" 2>&1 >/dev/null); then
      why="the published spec does not verify: ${why}"
    else
      why=""
    fi
  fi
  if [[ -n "$why" ]]; then
    n_spec=$((n_spec + 1))
    add_finding "  SPEC  ${slug}: ${why}"
  else
    echo "${slug}: signed spec current and verified"
  fi
}

reconcile_seat() {
  local cfg="$1" slug key head_err inputs authored want_sha live_sha skip
  slug=$(basename "$(dirname "$cfg")")
  if skip=$(seat_dir_skip_reason "$slug"); then
    echo "Skipping ${skip} dir: ${slug}"
    return 0
  fi
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]]; then
    echo "::error::refusing to reconcile suspicious slug: ${slug}"
    hard_fail=1
    return 0
  fi
  authored_slugs="${authored_slugs}${slug} "
  checked=$((checked + 1))
  key="vaults/${slug}/${R2_CONFIG_BASENAME}"
  if [[ ! -f "$cfg" ]]; then
    echo "::error::${slug}: ${cfg} is in HEAD's tree but not on disk"
    hard_fail=1
    return 0
  fi
  head_err="${work_dir}/${slug}.head.err"
  if ! r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "$key" >/dev/null 2>"$head_err"; then
    if grep -q '(404)' "$head_err"; then
      echo "::warning::${slug} has no object at s3://${R2_BUCKET_CONFIG}/${key}; first publish is Captain-gated; run operator/bin/provision-customer.sh."
      n_missing=$((n_missing + 1))
      return 0
    fi
    echo "::error::${slug}: could not read s3://${R2_BUCKET_CONFIG}/${key} (not a 404); refusing to guess whether the seat is provisioned"
    sed 's/^/    /' "$head_err"
    hard_fail=1
    return 0
  fi
  inputs="${work_dir}/${slug}.in"
  if ! seat_spec_inputs "$slug" "$inputs" || [[ ! -f "${inputs}/published.yaml" ]]; then
    echo "::error::${slug}: the object exists but what the seat boots from could not be read; cannot compare"
    hard_fail=1
    return 0
  fi
  want_sha=$(digest "$cfg")
  live_sha=$(digest "${inputs}/published.yaml")
  if [[ "$live_sha" == "$want_sha" ]]; then
    echo "${slug}: customer.yaml in sync (sha256 ${want_sha})"
  else
    authored=$(git log -1 --format='%h %ad' --date=short -- "$cfg" 2>/dev/null || true)
    echo "── ${slug} DRIFTED: R2 has ${live_sha}, HEAD carries ${want_sha} (authored ${authored:-unknown}) ──"
    n_drift=$((n_drift + 1))
    add_finding "  DRIFT  ${slug}: R2 ${live_sha} != git ${want_sha} (authored ${authored:-unknown}); ${HEAL}"
  fi
  check_spec "$slug" "$cfg" "$inputs"
}

for cfg in "${paths[@]}"; do
  reconcile_seat "$cfg"
done

# Objects with no authored file: warn-only, and a failed listing does not fail
# the control (it feeds a warning that never alerts; the drift check above did run).
listing="${work_dir}/listing.txt"
if r2 s3api list-objects-v2 --bucket "${R2_BUCKET_CONFIG}" --prefix "vaults/" \
     --query 'Contents[].Key' --output text >"$listing" 2>"${work_dir}/list.err"; then
  for k in $(tr '\t' '\n' <"$listing"); do
    case "$k" in vaults/*/"${R2_CONFIG_BASENAME}") ;; *) continue ;; esac
    obj_slug=${k#vaults/}
    obj_slug=${obj_slug%/${R2_CONFIG_BASENAME}}
    case "${authored_slugs}" in *" ${obj_slug} "*) continue ;; esac
    echo "::warning::${obj_slug} has an object at s3://${R2_BUCKET_CONFIG}/${k} but no customer.yaml on HEAD; seat retirement is manual (no automatic R2 delete)."
    n_unauthored=$((n_unauthored + 1))
    add_finding "  UNAUTHORED  ${obj_slug}"
  done
else
  echo "::warning::could not list s3://${R2_BUCKET_CONFIG}/vaults/; the unauthored-object check did not run (the drift check above did)"
  sed 's/^/    /' "${work_dir}/list.err"
fi

echo
echo "── R2 reconcile summary (read-only) ──"
echo "seats checked:   ${checked}"
echo "drifted:         ${n_drift}"
echo "unprovisioned:   ${n_missing}"
echo "unauthored:      ${n_unauthored}"
echo "refused (image): ${n_refused}"
echo "spec findings:   ${n_spec}"
[[ -n "$findings" ]] && printf '%s' "$findings"

if [[ "$hard_fail" -ne 0 ]]; then
  echo "R2 reconcile FAILED: the control could not complete."
  exit 1
fi
if [[ "$n_drift" -gt 0 || "$n_refused" -gt 0 || "$n_spec" -gt 0 ]]; then
  echo "R2 reconcile found seats whose R2 state is not what main authors (above). Nothing was written."
  exit 2
fi
echo "R2 reconcile clean: every authored seat's customer.yaml and signed spec are what HEAD carries."
exit 0

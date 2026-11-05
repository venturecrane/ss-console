#!/usr/bin/env bash
# publish-firm-inputs.sh: the ONE writer of a firm's lane inputs to R2.
#
#   publish-firm-inputs.sh <slug> <lane> <src-dir> [--runner-checkout <path>] [--validator-log <file>]
#
# Two callers, the same bytes the same way (2026-11-05):
#   * provisioning: lib/stage-<lane>-inputs.sh and provision-customer.sh's
#     medchron step, which keep their own engagements-main gate around it;
#   * the engagements repo's publish-firm-inputs workflow, on every merge to its
#     main (docs/handbook/firm-inputs-publish-workflow.yml.example), so a firm's
#     configuration reaches its seat without a reprovision. The seat's live
#     applier adopts what lands here.
#
# <lane> is demand | drafting | litigation | casefile | records | medchron.
# <src-dir> is engagements operator/customers/<slug>/<lane>/ (for medchron, the
# directory holding firm.yaml). Nothing here reads git: whoever calls this
# decides that <src-dir> is reviewed (engagements origin/main).
#
# WHAT IT DOES, in order; any refusal writes nothing:
#   1. validates <src-dir> against the runner at --runner-checkout (default: this
#      checkout). The caller names the runner the SEAT runs (its jobs bundle's
#      ss_console_sha), so inputs are never ahead of the schema that reads them.
#      Both runner layouts are understood: operator/runners/jobs (package jobs,
#      ADR 0103) and the older operator/runners/medchron (package medchron).
#      drafting first takes any pinned input the checkout lacks out of R2 into a
#      private temp copy (client documents are not in the repo, Law 15), so the
#      pin check proves the exact bytes and the --delete sync below keeps them.
#      A publisher that skipped this step would DELETE the firm's exemplars.
#   2. lists every key it will write, through the lane's own filters, and
#      refuses the whole publish if any key falls outside
#      vaults/<slug>/<lane>/[A-Za-z0-9._/-]+ (medchron: exactly
#      vaults/<slug>/medchron-firm.yaml) or carries a ".." segment;
#   3. syncs to vaults/<slug>/<lane>/ with --delete (gone means gone; medchron
#      is one object, copied);
#   4. reads the lane's marker yaml back and compares it byte for byte.
#
# Exit: 0 published (last line "published <slug>/<lane> etag=<8hex>");
#       1 refused by the validator (its lines are printed, or written to
#         --validator-log, whose first line is echoed: the validator may quote
#         config, so a public log takes the file form);
#       2 usage; 3 R2 unreachable, sync failed, or the read-back differs;
#       4 the validator could not run (no module for this lane in that runner,
#         or no interpreter): nothing was judged, nothing was written.
#
# Env: R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_ENDPOINT_URL (required);
#      R2_BUCKET_CONFIG (default smd-customer-config). Needs aws and uv on PATH.
# Values are never printed. bash 3.2 compatible (operator laptops are macOS).
set -euo pipefail

_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_DEFAULT_RUNNER="$(cd "${_HERE}/../../.." && pwd)"

_say() { echo "publish-firm-inputs: $*" >&2; }
_usage() { _say "$*"; _say "usage: publish-firm-inputs.sh <slug> <lane> <src-dir> [--runner-checkout <path>] [--validator-log <file>]"; exit 2; }

[ "$#" -ge 3 ] || _usage "expected <slug> <lane> <src-dir>"
SLUG="$1"; LANE="$2"; SRC="$3"; shift 3
RUNNER="${_DEFAULT_RUNNER}"; VLOG=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --runner-checkout) [ "$#" -ge 2 ] || _usage "--runner-checkout needs a path"; RUNNER="$2"; shift 2 ;;
    --validator-log) [ "$#" -ge 2 ] || _usage "--validator-log needs a file"; VLOG="$2"; shift 2 ;;
    *) _usage "unknown argument $1" ;;
  esac
done

[[ "${SLUG}" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]] || _usage "not a seat slug: ${SLUG}"
case "${LANE}" in
  demand|drafting|litigation|casefile|records) MARKER="${LANE}-firm.yaml" ;;
  medchron) MARKER="firm.yaml" ;;
  *) _usage "not a lane: ${LANE}" ;;
esac
[ -d "${SRC}" ] || _usage "no such directory: ${SRC}"
[ -f "${SRC}/${MARKER}" ] || _usage "no ${MARKER} in ${SRC}"
[ -d "${RUNNER}" ] || _usage "no runner checkout at ${RUNNER}"
for _v in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_ENDPOINT_URL; do
  [ -n "${!_v:-}" ] || _usage "${_v} is not set"
done
BUCKET="${R2_BUCKET_CONFIG:-smd-customer-config}"
if [ "${LANE}" = "medchron" ]; then PREFIX="vaults/${SLUG}/medchron-firm.yaml"; else PREFIX="vaults/${SLUG}/${LANE}/"; fi

_r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

WORK="$(mktemp -d)"
trap 'rm -rf "${WORK}"' EXIT

# ---------- the lane's filters (aws s3 sync semantics: last match wins) ----------
# One table, used twice: passed to aws, and applied here to list the keys the
# sync will write, so the key guard judges exactly what is uploaded.
FILTERS=()
case "${LANE}" in
  demand|drafting) FILTERS=(--exclude "eval/*" --exclude "request-brief-*") ;;
  litigation) FILTERS=(--exclude "eval/*" --exclude "*.md") ;;
  casefile) FILTERS=(--exclude "eval/*" --exclude "*.md" --include "prompts/*.md") ;;
  records) FILTERS=(--exclude "*" --include "records-firm.yaml") ;;
esac

_included() { # <relpath> -> 0 when the filters keep it
  local rel="$1" keep=0 i=0 verb pat
  while [ "${i}" -lt "${#FILTERS[@]}" ]; do
    verb="${FILTERS[$i]}"; pat="${FILTERS[$((i + 1))]}"; i=$((i + 2))
    # shellcheck disable=SC2053 # the pattern is a glob on purpose
    if [[ "${rel}" == ${pat} ]]; then
      if [ "${verb}" = "--exclude" ]; then keep=1; else keep=0; fi
    fi
  done
  return "${keep}"
}

# ---------- 1. the runner and the validator ----------
if [ -d "${RUNNER}/operator/runners/jobs/jobs" ]; then
  PYPATH="${RUNNER}/operator/runners/jobs"; PKG="jobs"
elif [ -d "${RUNNER}/operator/runners/medchron/medchron" ]; then
  PYPATH="${RUNNER}/operator/runners/medchron"; PKG="medchron"
else
  _say "the runner checkout at ${RUNNER} carries no jobs runner; nothing was judged or written"
  exit 4
fi
if [ "${LANE}" = "medchron" ]; then
  if [ "${PKG}" = "jobs" ]; then VMOD="jobs.medchron.config"; else VMOD="medchron.config"; fi
else
  VMOD="${PKG}.${LANE}.firm"
fi
[ -f "${PYPATH}/$(echo "${VMOD}" | tr . /).py" ] || {
  _say "the runner at ${RUNNER} has no ${LANE} validator (${VMOD}); not publishing unvalidated ${LANE} inputs"
  exit 4
}
command -v uv >/dev/null 2>&1 || { _say "uv is not on PATH; the validator cannot run"; exit 4; }

STAGE="${SRC}"
if [ "${LANE}" = "drafting" ]; then
  # The R2 backfill (Law 15). Runs BEFORE the delete-sync, always.
  STAGE="${WORK}/stage"
  mkdir -p "${STAGE}"
  cp -R "${SRC}/." "${STAGE}/"
  if ! missing="$(uv run --quiet --with pyyaml python3 - "${STAGE}" <<'PY'
import sys, pathlib, yaml
root = pathlib.Path(sys.argv[1])
data = yaml.safe_load((root / "drafting-firm.yaml").read_text(encoding="utf-8")) or {}
for rel in (data.get("inputs") or {}):
    if not (root / rel).is_file():
        print(rel)
PY
)"; then
    _say "could not read the drafting inputs list for ${SLUG}; nothing was written"
    exit 4
  fi
  while IFS= read -r rel_in; do
    [ -n "${rel_in}" ] || continue
    if [[ ! "${rel_in}" =~ ^[A-Za-z0-9._/-]+$ || "/${rel_in}/" == */../* ]]; then
      _say "refusing a pinned drafting input outside the lane's key space: ${rel_in}"
      exit 1
    fi
    mkdir -p "$(dirname "${STAGE}/${rel_in}")"
    _r2 s3 cp "s3://${BUCKET}/vaults/${SLUG}/drafting/${rel_in}" "${STAGE}/${rel_in}" --only-show-errors \
      || _say "WARN pinned drafting input ${rel_in} is in neither the checkout nor R2"
  done <<< "${missing}"
fi

vrc=0
if [ "${LANE}" = "medchron" ]; then
  vout="$(PYTHONPATH="${PYPATH}" uv run --quiet --with pyyaml python3 - "${VMOD}" "${STAGE}/${MARKER}" 2>&1 <<'PY'
import importlib, sys
try:
    config = importlib.import_module(sys.argv[1])
except Exception as exc:  # the validator itself could not start
    print(f"medchron firm validator could not run: {exc}")
    sys.exit(4)
try:
    cfg = config.load(sys.argv[2])
except config.ConfigError as exc:
    print(f"medchron firm config: {exc}")
    sys.exit(1)
print(f"medchron firm config OK ({cfg.slug})")
PY
)" || vrc=$?
else
  vout="$(PYTHONPATH="${PYPATH}" uv run --quiet --with pyyaml python3 -m "${VMOD}" "${STAGE}" 2>&1)" || vrc=$?
fi
if [ -n "${VLOG}" ]; then
  printf '%s\n' "${vout}" > "${VLOG}"
  [ "${vrc}" -eq 0 ] || _say "validator exit ${vrc}: $(printf '%s\n' "${vout}" | head -1)"
else
  [ -z "${vout}" ] || printf '%s\n' "${vout}" >&2
fi
case "${vrc}" in
  0) ;;
  1) _say "the ${LANE} firm inputs for ${SLUG} do not validate against the runner at ${RUNNER}; nothing was written"; exit 1 ;;
  *) _say "the ${LANE} validator for ${SLUG} could not run (rc=${vrc}); the inputs were not judged, nothing was written"; exit 4 ;;
esac

# ---------- 2. every key this publish writes, guarded ----------
nkeys=0
if [ "${LANE}" = "medchron" ]; then
  [[ "${PREFIX}" =~ ^vaults/${SLUG}/medchron-firm\.yaml$ ]] || { _say "refusing to write R2 key ${PREFIX}"; exit 1; }
  nkeys=1
else
  keys_ok=1
  while IFS= read -r -d '' f; do
    rel="${f#"${STAGE}"/}"
    _included "${rel}" || continue
    key="${PREFIX}${rel}"
    if [[ ! "${key}" =~ ^vaults/${SLUG}/(demand|drafting|litigation|casefile|records)/[A-Za-z0-9._/-]+$ || "/${rel}/" == */../* ]]; then
      _say "refusing to write R2 key outside vaults/${SLUG}/${LANE}/: ${key}"
      keys_ok=0
    fi
    nkeys=$((nkeys + 1))
  done < <(find "${STAGE}" \( -type f -o -type l \) -print0)
  [ "${keys_ok}" -eq 1 ] || { _say "nothing was written"; exit 1; }
fi
_included "${MARKER}" || [ "${LANE}" = "medchron" ] || { _say "the lane filters drop ${MARKER}; refusing"; exit 2; }

# ---------- 3. the write ----------
_say "publishing ${nkeys} file(s) to s3://${BUCKET}/${PREFIX}"
if [ "${LANE}" = "medchron" ]; then
  _r2 s3 cp "${STAGE}/${MARKER}" "s3://${BUCKET}/${PREFIX}" --only-show-errors \
    || { _say "R2 upload of the medchron firm config failed"; exit 3; }
  MKEY="${PREFIX}"
else
  _r2 s3 sync "${STAGE}/" "s3://${BUCKET}/${PREFIX}" --delete "${FILTERS[@]}" --only-show-errors \
    || { _say "R2 sync of the ${LANE} firm inputs failed"; exit 3; }
  MKEY="${PREFIX}${MARKER}"
fi

# ---------- 4. read the marker back, byte for byte ----------
_r2 s3 cp "s3://${BUCKET}/${MKEY}" "${WORK}/readback" --only-show-errors \
  || { _say "could not read ${MKEY} back"; exit 3; }
cmp -s "${STAGE}/${MARKER}" "${WORK}/readback" \
  || { _say "the read-back of ${MKEY} differs from what was published"; exit 3; }
etag="$(_r2 s3api head-object --bucket "${BUCKET}" --key "${MKEY}" --query ETag --output text 2>/dev/null | tr -d '"' || true)"
echo "published ${SLUG}/${LANE} etag=${etag:0:8}"

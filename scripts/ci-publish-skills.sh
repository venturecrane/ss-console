#!/usr/bin/env bash
# CI: publish a CANDIDATE skills release to R2. Never live on merge.
#
# Skills reach seats through `operator/bin/fleet release --skills <version>`,
# ring by ring, with a capability gate and a heartbeat read-back. This script
# only makes a version AVAILABLE to that command:
#
#   fleet/skills/objects/<sha256>   every shipped skill file, content-addressed
#   fleet/skills/<version>.json     the manifest (operator/bin/skills-manifest.py):
#                                   files -> sha256/size/mode, retired names,
#                                   and each skill's NEEDS from the image
#
# version = <utc yyyymmddHHMM>-<AFTER_SHA[:8]>. It writes NO per-seat pointer
# (vaults/<slug>/skills-pointer.json): only `fleet release --skills` does, so a
# merge cannot change what any seat runs. Every key is checked against the two
# fleet/skills shapes before any write.
#
# Runs when the push range touches operator/skills/** or the retired list
# (FORCE=1 publishes regardless, for a first release or a by-hand run). Also
# records the release in D1 fleet_releases (kind skills) when wrangler can reach
# it; a failure there is a warning, because `fleet release --skills` records a
# missing row itself.
#
# The NEEDS for tools and action classes are judged against the overlay's tool
# map at the PINNED OVERLAY_REF (Dockerfile), fetched as data (never executed);
# OVERLAY_ACTION_CLASSES_FILE overrides it for tests.
#
# Inputs (env): BEFORE_SHA, AFTER_SHA. R2 auth: R2_* if set, else derived from
# CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID (scripts/lib/r2-creds.sh, the
# same derivation as ci-publish-customer-configs.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AFTER="${AFTER_SHA:?AFTER_SHA required}"
BEFORE="${BEFORE_SHA:-}"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"

[[ "$AFTER" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::AFTER_SHA must be a 40-hex commit"; exit 1; }

if [[ "${FORCE:-}" != "1" ]]; then
  if [[ -z "$BEFORE" || "$BEFORE" =~ ^0+$ ]]; then BEFORE="${AFTER}~1"; fi
  if [[ -z "$(git -C "$REPO_ROOT" diff --name-only "$BEFORE" "$AFTER" -- 'operator/skills/' 'operator/templates/retired-skills.txt' || true)" ]]; then
    echo "No skill changes in ${BEFORE}..${AFTER}; no skills release to publish."
    exit 0
  fi
fi

command -v aws >/dev/null 2>&1 || { echo "::error::aws CLI not found"; exit 1; }
# shellcheck source=lib/r2-creds.sh
source "${SCRIPT_DIR}/lib/r2-creds.sh"
derive_r2_creds

VERSION="$(date -u +%Y%m%d%H%M)-${AFTER:0:8}"
MANIFEST_KEY="fleet/skills/${VERSION}.json"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

assert_skills_key() {
  if [[ ! "$1" =~ ^fleet/skills/(objects/[0-9a-f]{64}|[0-9]{12}-[0-9a-f]{8}\.json)$ ]]; then
    echo "::error::refusing to write R2 key outside the skills release key space: $1"
    exit 1
  fi
}

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

# ---------- the overlay's tool map at the commit being published (data, not code) ----------
# The overlay is operator/hermes-smd-overlay in this tree (ADR 0095), so the map
# a skills release is checked against is the one at AFTER_SHA, read from git so a
# dirty checkout cannot substitute its own.
ACTION_CLASSES="${OVERLAY_ACTION_CLASSES_FILE:-}"
if [[ -z "$ACTION_CLASSES" ]]; then
  git -C "$REPO_ROOT" show "${AFTER}:operator/hermes-smd-overlay/shared/action_classes.py" > "${work}/action_classes.py" \
    || { echo "::error::operator/hermes-smd-overlay/shared/action_classes.py is not in ${AFTER}"; exit 1; }
  ACTION_CLASSES="${work}/action_classes.py"
fi

# ---------- the manifest and its objects ----------
( cd "${REPO_ROOT}/operator" && PYTHONPATH=. python3 bin/skills-manifest.py \
    --skills skills --version "$VERSION" --source-sha "$AFTER" \
    --retired templates/retired-skills.txt --verbs workspace_broker/verbs.py \
    --action-classes "$ACTION_CLASSES" --out "${work}/manifest.json" --objects-dir "${work}/objects" )

existing="${work}/existing.txt"
r2 s3 ls "s3://${R2_BUCKET_CONFIG}/fleet/skills/objects/" 2>/dev/null | awk '{print $4}' > "$existing" || true
uploaded=0
for obj in "${work}/objects"/*; do
  sha="$(basename "$obj")"
  grep -qx "$sha" "$existing" && continue
  key="fleet/skills/objects/${sha}"
  assert_skills_key "$key"
  r2 s3 cp "$obj" "s3://${R2_BUCKET_CONFIG}/${key}" --only-show-errors --content-type application/octet-stream
  uploaded=$((uploaded + 1))
done
# Every object the manifest names must now be present before the manifest is.
r2 s3 ls "s3://${R2_BUCKET_CONFIG}/fleet/skills/objects/" | awk '{print $4}' > "$existing"
missing=0
for obj in "${work}/objects"/*; do
  grep -qx "$(basename "$obj")" "$existing" || { echo "::error::object $(basename "$obj") missing after upload"; missing=1; }
done
[[ "$missing" = 0 ]] || exit 1

assert_skills_key "$MANIFEST_KEY"
r2 s3 cp "${work}/manifest.json" "s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY}" --only-show-errors --content-type application/json
r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY}" "${work}/readback.json" --only-show-errors
cmp -s "${work}/manifest.json" "${work}/readback.json" || { echo "::error::manifest read-back does not match"; exit 1; }
echo "skills release ${VERSION} published: s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY} (${uploaded} new object(s))"
echo "  candidate only. Roll it out with: operator/bin/fleet release --skills ${VERSION}"

# ---------- D1 record (best effort; fleet release records a missing row) ----------
if [[ -n "${CLOUDFLARE_API_TOKEN:-}" ]]; then
  sql="INSERT INTO fleet_releases (release_id, kind, skills_version, ss_console_sha, volume_forward_compatible, notes, created_at) VALUES ('skl-${VERSION}', 'skills', '${VERSION}', '${AFTER}', 1, 'published by CI', strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
  if ( cd "$REPO_ROOT" && npx --quiet wrangler d1 execute ss-console-db --remote --command "$sql" >/dev/null ); then
    echo "  recorded fleet_releases skl-${VERSION}"
  else
    echo "::warning::could not record skl-${VERSION} in D1; fleet release --skills will record it"
  fi
fi

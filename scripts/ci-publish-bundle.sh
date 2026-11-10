#!/usr/bin/env bash
# CI: build and publish a CANDIDATE `jobs` bundle to R2 (ADR 0100 W3). Never live on merge.
#
# The jobs bundle is what the seat image installs at /opt/jobs (the runner
# venv and its pricing table) and /opt/connectors/smokeball (the connector venv),
# built again on merge so a change to the job runtime reaches only the seats that
# author a job lane, without a new image for every seat. The release controller
# (release-tick.yml, `fleet release latest --tick`, operator/bin/lib/fleet/
# bundles.py) walks it through the rings; this script only makes a version
# AVAILABLE. It writes NO per-seat pointer.
#
# HOW IT IS BUILT. Inside the published core seat image (the image this merge
# built, else the newest completed image built from main: `fleet bundle-base`),
# at the image's own /opt paths, by operator/templates/jobs-runtime.sh, the same
# script the Dockerfile runs, from this merge's tree. The paths are the ones a
# seat bind-mounts the bundle on, so no shebang or venv prefix moves. As root,
# because the image build ran those steps as root (root:root a+rX).
#
#   fleet/bundles/jobs/<version>.tar.zst   root: jobs/ and connectors/smokeball/
#   fleet/bundles/jobs/<version>.json      {"name","version","sha256","size","layout",
#                                           "ss_console_sha","ss_console_seq",
#                                           "base_image_ref","python"}
#   fleet/bundles/jobs/layout-<n>/latest.json
#                                          {"version","manifest_key","manifest_sha256"}
#
# THE LAYOUT (ADR 0103). A bundle's venvs carry absolute paths, so a bundle runs
# only at the mount paths it was built for; LAYOUT names them (1: medchron/ at
# /opt/medchron, every manifest before the key; 2: jobs/ at /opt/jobs). It must
# equal the seat installer's BundleSpec.layout (bundle_installer/manifest.py;
# operator/bin/tests/test_ci_publish_bundle.py pins the two). layout-<n>/latest.json
# is written LAST, after the D1 record, so it only ever names a recorded release:
# a seat whose pointer names another layout's bundle boots this one instead
# (bundle_installer.decide), which is what lets an image and its bundle change
# layout in one release.
#
# ss_console_seq is AFTER_SHA's commit number (`fleet commit-seq`, rev-list
# --count, refused on a shallow clone). A seat has no git, so its self-test
# orders the mounted bundle against the image's bundle floor by this number
# (operator/templates/jobs-bundle-commit-probe.py, jobs-bundle-built-for-image).
#
# version = <utc yyyymmddHHMM>-<AFTER_SHA[:8]>, the skills shape; release id
# bnd-<version>. The tarball goes up first and is read back (sha256 compared)
# before the manifest, which is read back too; then the D1 row (kind 'bundle').
#
# Inputs (env): AFTER_SHA, FLY_REGISTRY_TOKEN (pull from registry.fly.io), and
# R2_* if set, else derived from CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
# (scripts/lib/r2-creds.sh). BUNDLE_DRY_RUN=1 builds and checks without any
# upload or record (a by-hand proof of the build).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
AFTER="${AFTER_SHA:?AFTER_SHA required}"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"
NAME="jobs"
LAYOUT=2

[[ "$AFTER" =~ ^[0-9a-f]{40}$ ]] || { echo "::error::AFTER_SHA must be a 40-hex commit"; exit 1; }
for tool in docker zstd sha256sum aws; do
  command -v "$tool" >/dev/null 2>&1 || { echo "::error::${tool} not found"; exit 1; }
done

VERSION="$(date -u +%Y%m%d%H%M)-${AFTER:0:8}"
TARBALL_KEY="fleet/bundles/${NAME}/${VERSION}.tar.zst"
MANIFEST_KEY="fleet/bundles/${NAME}/${VERSION}.json"
LATEST_KEY="fleet/bundles/${NAME}/layout-${LAYOUT}/latest.json"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

assert_bundle_key() {
  if [[ ! "$1" =~ ^fleet/bundles/[a-z][a-z0-9-]{0,31}/([0-9]{12}-[0-9a-f]{8}\.(tar\.zst|json)|layout-[1-9][0-9]*/latest\.json)$ ]]; then
    echo "::error::refusing to write R2 key outside the bundle release key space: $1"
    exit 1
  fi
}

SEQ="$(cd "$REPO_ROOT" && operator/bin/fleet commit-seq --sha "$AFTER")"
[[ "$SEQ" =~ ^[1-9][0-9]*$ ]] || { echo "::error::fleet commit-seq answered ${SEQ}, not a commit number"; exit 1; }

# ---------- the image to build in ----------
BASE_IMAGE="$(cd "$REPO_ROOT" && operator/bin/fleet bundle-base --sha "$AFTER")"
[[ "$BASE_IMAGE" =~ ^registry\.fly\.io/hermes-fleet[:@] ]] \
  || { echo "::error::bundle-base answered ${BASE_IMAGE}, not a hermes-fleet image"; exit 1; }
echo "building ${NAME} ${VERSION} inside ${BASE_IMAGE}"
printf '%s' "${FLY_REGISTRY_TOKEN:?FLY_REGISTRY_TOKEN required}" \
  | docker login registry.fly.io --username x --password-stdin >/dev/null
docker pull --quiet --platform linux/amd64 "$BASE_IMAGE" >/dev/null

# ---------- the build, inside the image, at the final paths ----------
# The merge's operator tree is mounted read-only and copied in, because a local
# `uv pip install` may write build files beside its source. The runner's path is
# the merge's own generated seat layout (operator/contracts/seat-layout.yaml),
# read inside the build from the mounted tree: the base image may predate it.
mkdir -p "${work}/out"
docker run --rm --platform linux/amd64 --user 0 --entrypoint /bin/sh \
  -v "${REPO_ROOT}/operator:/src:ro" -v "${work}/out:/out" "$BASE_IMAGE" -c '
    set -eu
    mkdir -p /tmp/src
    cp -R /src/connectors /src/runners /src/requirements /src/templates /tmp/src/
    . /src/templates/_seat-layout.generated.sh
    rm -rf "$SMD_LAYOUT_JOBS_BUNDLE" /opt/connectors/smokeball
    export SRC=/tmp/src
    sh /tmp/src/templates/jobs-runtime.sh connector smokeball
    PRICING_JSON=/src/adapter/cost_telemetry/anthropic_pricing.json sh /tmp/src/templates/jobs-runtime.sh jobs
    "$SMD_LAYOUT_JOBS_PYTHON" -c "import platform; print(platform.python_version())" > /out/python
    tar --numeric-owner -C /opt -cf /out/bundle.tar jobs connectors/smokeball
  '

PYTHON_VERSION="$(tr -d '[:space:]' < "${work}/out/python")"
[[ "$PYTHON_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "::error::the bundle venv reports python ${PYTHON_VERSION}"; exit 1; }
# List once into a file, then search it: `tar -tf | grep -q` under pipefail
# fails on a FOUND entry, because grep exits at the first match and tar dies
# writing to the closed pipe (the first real publish, 2026-10-31, run 41238271283).
tar -tf "${work}/out/bundle.tar" > "${work}/listing.txt"
# The guaranteed set is one file (ADR 0100 W5): the image records the same list
# as bundle_paths, and skills that cite one pass the skills release on it.
mapfile -t guaranteed < <(grep -vE '^[[:space:]]*(#|$)' "${REPO_ROOT}/operator/templates/jobs-bundle-paths.txt")
[ "${#guaranteed[@]}" -gt 0 ] || { echo "::error::operator/templates/jobs-bundle-paths.txt lists no path"; exit 1; }
for entry in "${guaranteed[@]}"; do
  grep -qxF "${entry}" "${work}/listing.txt" \
    || { echo "::error::the tarball holds no ${entry} ($(wc -l < "${work}/listing.txt") entries; nearest below)"
         grep -F "$(basename "${entry}")" "${work}/listing.txt" | head -5 || true
         exit 1; }
done
zstd -q -19 -T0 "${work}/out/bundle.tar" -o "${work}/bundle.tar.zst"
SHA256="$(sha256sum "${work}/bundle.tar.zst" | awk '{print $1}')"
SIZE="$(wc -c < "${work}/bundle.tar.zst" | tr -d ' ')"

python3 - "$NAME" "$VERSION" "$SHA256" "$SIZE" "$LAYOUT" "$AFTER" "$SEQ" "$BASE_IMAGE" "$PYTHON_VERSION" > "${work}/manifest.json" <<'PY'
import json, sys
name, version, sha, size, layout, src, seq, base, python = sys.argv[1:]
print(json.dumps({"name": name, "version": version, "sha256": sha, "size": int(size), "layout": int(layout),
                  "ss_console_sha": src, "ss_console_seq": int(seq), "base_image_ref": base, "python": python},
                 indent=1, sort_keys=True))
PY
MANIFEST_SHA256="$(sha256sum "${work}/manifest.json" | awk '{print $1}')"
python3 - "$VERSION" "$MANIFEST_KEY" "$MANIFEST_SHA256" > "${work}/latest.json" <<'PY'
import json, sys
version, key, sha = sys.argv[1:]
print(json.dumps({"version": version, "manifest_key": key, "manifest_sha256": sha}, indent=1, sort_keys=True))
PY
echo "built ${NAME} ${VERSION} (layout ${LAYOUT}): ${SIZE} bytes, sha256 ${SHA256}, python ${PYTHON_VERSION}"

if [[ "${BUNDLE_DRY_RUN:-}" = "1" ]]; then
  echo "BUNDLE_DRY_RUN=1: nothing uploaded, nothing recorded"
  cat "${work}/manifest.json"
  echo "would write ${LATEST_KEY}:"
  cat "${work}/latest.json"
  exit 0
fi

# ---------- upload, tarball first, each read back ----------
# shellcheck source-path=SCRIPTDIR source=lib/r2-creds.sh
source "${SCRIPT_DIR}/lib/r2-creds.sh"
derive_r2_creds
r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

assert_bundle_key "$TARBALL_KEY"
r2 s3 cp "${work}/bundle.tar.zst" "s3://${R2_BUCKET_CONFIG}/${TARBALL_KEY}" --only-show-errors --content-type application/zstd
r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${TARBALL_KEY}" "${work}/readback.tar.zst" --only-show-errors
[[ "$(sha256sum "${work}/readback.tar.zst" | awk '{print $1}')" = "$SHA256" ]] \
  || { echo "::error::tarball read-back does not match"; exit 1; }

assert_bundle_key "$MANIFEST_KEY"
r2 s3 cp "${work}/manifest.json" "s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY}" --only-show-errors --content-type application/json
r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY}" "${work}/readback.json" --only-show-errors
cmp -s "${work}/manifest.json" "${work}/readback.json" || { echo "::error::manifest read-back does not match"; exit 1; }
echo "bundle ${NAME} ${VERSION} published: s3://${R2_BUCKET_CONFIG}/${MANIFEST_KEY}"

# ---------- D1 record: the controller rolls only recorded releases ----------
( cd "$REPO_ROOT" && operator/bin/fleet bundle-record --version "$VERSION" --sha "$AFTER" --image-ref "$BASE_IMAGE" \
    --notes "merge ${AFTER:0:8} (CI)" )

# ---------- layout-<n>/latest.json: last, so it names only a recorded release ----------
assert_bundle_key "$LATEST_KEY"
r2 s3 cp "${work}/latest.json" "s3://${R2_BUCKET_CONFIG}/${LATEST_KEY}" --only-show-errors --content-type application/json
r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${LATEST_KEY}" "${work}/readback-latest.json" --only-show-errors
cmp -s "${work}/latest.json" "${work}/readback-latest.json" || { echo "::error::${LATEST_KEY} read-back does not match"; exit 1; }
echo "  ${LATEST_KEY} -> ${VERSION}: a seat whose pointer names another layout's bundle boots this one"
echo "  candidate only. The release controller (release-tick.yml) rolls it to the seats that author a job lane."

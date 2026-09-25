#!/usr/bin/env bash
# Regenerate the pinned, hash-locked requirement files the seat image installs
# from (operator/templates/Dockerfile). One file per venv that is built from
# third-party packages:
#
#   broker.txt                  the workspace broker venv (broker.in is the input)
#   connector-<name>.txt        one per author-built connector under connectors/
#   medchron.txt                the medchron runner venv
#
# The connector and runner files are compiled FROM THEIR pyproject.toml, so the
# declared ranges stay the single source of truth and this file is their
# resolution on a given day. Our own local packages (operator-connector-sdk,
# smokeball-connector) are resolved from the tree for the compile and OMITTED
# from the output: the image installs them afterwards with `--no-deps`, because
# a local path cannot carry a hash and hash-checking mode refuses a mixed file.
#
# Resolution targets the seat image's interpreter and platform (Debian 13 ships
# python3 3.13; x86_64 Linux), never the workstation's, so a wheel that exists
# only for macOS never ends up pinned.
#
# Run it whenever a pyproject under operator/ changes its dependencies, or to
# take a routine currency bump; commit the result. bin/tests/test_requirements_pins.py
# fails when a declared dependency has no pin here.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPERATOR="$(cd "${HERE}/.." && pwd)"
PY=3.13
PLATFORM=linux
# Compile from the repo root with repo-relative inputs, so the "# via" annotations
# uv writes name the same path whoever runs this and from wherever: uv relativises
# them to the invoking cwd, and the committed files used to carry one
# workstation's absolute worktree path.
ROOT="$(cd "${OPERATOR}/.." && pwd)"
cd "${ROOT}"

overrides="$(mktemp)"
trap 'rm -f "${overrides}"' EXIT
{
  echo "operator-connector-sdk @ file://${OPERATOR}/connectors/_sdk"
  echo "smokeball-connector @ file://${OPERATOR}/connectors/smokeball"
} > "${overrides}"

compile() {
  local out="$1"; shift
  uv pip compile "$@" \
    --override "${overrides}" \
    --no-emit-package operator-connector-sdk \
    --no-emit-package smokeball-connector \
    --generate-hashes \
    --python-version "${PY}" \
    --python-platform "${PLATFORM}" \
    --no-header \
    --quiet \
    -o "${HERE}/${out}"
  echo "wrote requirements/${out} ($(grep -cE '^[a-z0-9]' "${HERE}/${out}") pins)"
}

compile broker.txt operator/requirements/broker.in
for cdir in operator/connectors/*/; do
  name="$(basename "${cdir}")"
  [ "${name}" = "_sdk" ] && continue
  [ -f "${cdir}pyproject.toml" ] || continue
  compile "connector-${name}.txt" "${cdir}pyproject.toml"
done
compile medchron.txt operator/runners/medchron/pyproject.toml

# hermes-overlay.txt: the overlay's third-party dependencies that the Hermes
# venv does not already carry (2026-09-25 code review, Dependencies 3). The
# image installs the overlay into /opt/hermes/.venv with `--no-deps`, so
# before that line these pins go in with `--require-hashes`, and `uv pip check`
# then proves the overlay's declared requirements are all satisfied. Without
# this file the overlay line resolved boto3 and sentry-sdk from PyPI on every
# build, outside any lock and outside pip_audit_gate.py.
#
# Inputs are the two pins the Dockerfile ships, read from it so they cannot
# disagree: the overlay's pyproject at OVERLAY_REF (its declared ranges), and
# Hermes' uv.lock at HERMES_UPSTREAM_SHA exported with the same extras the
# image's `uv sync` installs. That export is the constraint set, so a
# dependency both sides need resolves to the version Hermes already locked,
# and every package Hermes installs is omitted from the output: its version
# belongs to Hermes' lock, and pinning it here would let this file move a
# Hermes package the Hermes lock did not choose.
#
# When to re-run: the overlay's pyproject gains or changes a dependency
# (verify-overlay-pairs.py fails in substrate when a declared dependency has
# neither a pin here nor a place in the Hermes set), or HERMES_UPSTREAM_SHA
# moves. An OVERLAY_REF move alone needs nothing.
DOCKERFILE="operator/templates/Dockerfile"
overlay_ref="$(sed -n 's/^ARG OVERLAY_REF="\([0-9a-f]\{40\}\)"$/\1/p' "${DOCKERFILE}")"
hermes_sha="$(sed -n 's/^ARG HERMES_UPSTREAM_SHA=\([0-9a-f]\{40\}\)$/\1/p' "${DOCKERFILE}")"
[ -n "${overlay_ref}" ] || { echo "compile.sh: no OVERLAY_REF in ${DOCKERFILE}" >&2; exit 1; }
[ -n "${hermes_sha}" ] || { echo "compile.sh: no HERMES_UPSTREAM_SHA in ${DOCKERFILE}" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -f "${overrides}"; rm -rf "${work}"' EXIT
mkdir -p "${work}/hermes" "${work}/overlay"
curl -fsSL -o "${work}/hermes/pyproject.toml" \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/${hermes_sha}/pyproject.toml"
curl -fsSL -o "${work}/hermes/uv.lock" \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/${hermes_sha}/uv.lock"
curl -fsSL -o "${work}/overlay/pyproject.toml" \
  "https://raw.githubusercontent.com/venturecrane/hermes-smd-overlay/${overlay_ref}/pyproject.toml"

# The extras here are the Dockerfile's `uv sync --frozen --no-install-project`
# extras; tests/operator-dockerfile.test.ts holds the two lists equal.
(cd "${work}/hermes" && uv export --frozen --no-hashes --no-emit-project \
  --no-header --no-annotate --extra all --extra messaging --extra anthropic) \
  > "${work}/hermes-installed.txt"

no_emit=()
while IFS= read -r name; do
  no_emit+=(--no-emit-package "${name}")
done < <(sed -n 's/^\([A-Za-z0-9][A-Za-z0-9._-]*\)==.*/\1/p' "${work}/hermes-installed.txt")
[ "${#no_emit[@]}" -gt 0 ] || { echo "compile.sh: the Hermes export named no packages" >&2; exit 1; }

# Compiled from inside the work dir so the "# via" annotation reads the stable
# relative path overlay/pyproject.toml, not this machine's temp directory.
(cd "${work}" && uv pip compile overlay/pyproject.toml \
  -c hermes-installed.txt \
  "${no_emit[@]}" \
  --generate-hashes \
  --python-version "${PY}" \
  --python-platform "${PLATFORM}" \
  --no-header \
  --quiet \
  -o "${HERE}/hermes-overlay.txt")
echo "wrote requirements/hermes-overlay.txt ($(grep -cE '^[a-z0-9]' "${HERE}/hermes-overlay.txt") pins)"

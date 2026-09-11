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

compile broker.txt "${HERE}/broker.in"
for cdir in "${OPERATOR}"/connectors/*/; do
  name="$(basename "${cdir}")"
  [ "${name}" = "_sdk" ] && continue
  [ -f "${cdir}pyproject.toml" ] || continue
  compile "connector-${name}.txt" "${cdir}pyproject.toml"
done
compile medchron.txt "${OPERATOR}/runners/medchron/pyproject.toml"

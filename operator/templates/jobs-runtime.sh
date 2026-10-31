#!/bin/sh
# The job runtime's install steps, written once (ADR 0100 W3).
#
# Two builds install the job runner (/opt/medchron) and the author-built
# connector venvs (/opt/connectors/<name>): the seat image (operator/templates/
# Dockerfile) and the `jobs` bundle CI publishes on merge (scripts/
# ci-publish-bundle.sh, which runs this script INSIDE the published core image so
# the venvs land at the paths a seat mounts them on). Both call this file, so the
# two builds cannot drift: same hash-locked pins, same --no-deps local installs,
# same build-time import list, same antiword assertion, same root:root a+rX.
# The seat's bundle installer calls `import-check` too, so the import list a
# bundle must pass on a seat is the one it passed at build.
#
#   jobs-runtime.sh connectors           every connector under $SRC/connectors (the image)
#   jobs-runtime.sh connector <name>     one connector venv (the bundle: smokeball)
#   jobs-runtime.sh medchron             the chronology runner venv
#   jobs-runtime.sh import-check <py>    the import list + antiword, against <py>
#
# SRC (default /app) is where connectors/, runners/ and requirements/ are read
# from; the bundle build points it at the merge's checkout. PRICING_JSON, when
# set, is copied to /opt/medchron/anthropic_pricing.json (the image COPYs it
# there itself). JOBS_RUNTIME_OPT (default /opt) exists only so the equivalence
# test (operator/bin/tests/test_jobs_runtime.py) can run this off a real /opt.
# POSIX sh: the image runs it with /bin/sh, as the RUN lines it replaced did.
set -eu

SRC="${SRC:-/app}"
OPT="${JOBS_RUNTIME_OPT:-/opt}"

# Each connector under $SRC/connectors/ (except the `_sdk` library it shares) is
# a Python stdio MCP server, installed into its OWN venv so connectors never
# share a dependency closure with each other or with the Hermes venv. The overlay
# registry launches each by the ABSOLUTE path to its venv console-script
# (/opt/connectors/<name>/.venv/bin/<name>-mcp). The `*-mcp` assertion fails the
# build loudly rather than letting a missing console-script surface as a runtime
# MCP-spawn failure on a live Machine.
install_connector() {
  cdir="$1"
  name=$(basename "$cdir")
  venv="${OPT}/connectors/${name}/.venv"
  echo "== installing author-built connector: ${name} =="
  mkdir -p "${OPT}/connectors/${name}"
  uv venv "$venv"
  reqs="${SRC}/requirements/connector-${name}.txt"
  [ -f "$reqs" ] || { echo "FATAL: connector ${name} has no pinned requirements at operator/requirements/connector-${name}.txt; run operator/requirements/compile.sh and commit the result"; exit 1; }
  uv pip install --python "$venv/bin/python" --no-cache --no-config --require-hashes -r "$reqs"
  uv pip install --python "$venv/bin/python" --no-cache --no-deps "${SRC}/connectors/_sdk" "$cdir"
  ls "$venv"/bin/*-mcp >/dev/null 2>&1 || { echo "FATAL: connector ${name} produced no *-mcp console-script"; exit 1; }
}

connectors() {
  for cdir in "${SRC}"/connectors/*/; do
    name=$(basename "$cdir")
    [ "$name" = "_sdk" ] && continue
    [ -f "${cdir}pyproject.toml" ] || continue
    install_connector "$cdir"
  done
  chmod -R a+rX "${OPT}/connectors"
}

connector() {
  cdir="${SRC}/connectors/$1/"
  [ -f "${cdir}pyproject.toml" ] || { echo "FATAL: no connector $1 under ${SRC}/connectors"; exit 1; }
  install_connector "$cdir"
  chmod -R a+rX "${OPT}/connectors/$1"
}

# The build-time import smoke: every lane module the runner ships, plus the two
# readers the litigation job needs. FAILS THE BUILD, not a seat.
import_check() {
  "$1" -c "import medchron.daemon, medchron.stages.upload, medchron.demand_lane, medchron.demand.run, medchron.drafting_lane, medchron.drafting.run, medchron.negotiation_lane, medchron.negotiation.run, medchron.note_lane, medchron.note_by_smd, medchron.litigation_lane, medchron.litigation.run, striprtf.striprtf, smokeball_connector.docx_format, docx, medchron.casefile_lane, medchron.casefile.run, medchron.casefile.firm"
  command -v antiword >/dev/null || { echo "FATAL: antiword is missing (the litigation job and the negotiation watch read .doc with it)"; exit 1; }
}

# The chronology runner (routine 11, ss#2614), in its OWN root-owned venv from the
# connector SDK, the Smokeball connector and the runner, so the daemon and the
# driver child never share a dependency closure with the Hermes or broker venv.
# root:root a+rX: the medchron uid executes it and can write nothing in it.
medchron() {
  if [ -n "${PRICING_JSON:-}" ]; then
    mkdir -p "${OPT}/medchron"
    cp "$PRICING_JSON" "${OPT}/medchron/anthropic_pricing.json"
  fi
  uv venv "${OPT}/medchron/.venv"
  uv pip install --python "${OPT}/medchron/.venv/bin/python" --no-cache --no-config \
    --require-hashes -r "${SRC}/requirements/medchron.txt"
  uv pip install --python "${OPT}/medchron/.venv/bin/python" --no-cache --no-deps \
    "${SRC}/connectors/_sdk" "${SRC}/connectors/smokeball" "${SRC}/runners/medchron"
  [ -x "${OPT}/medchron/.venv/bin/medchron" ] || { echo "FATAL: the medchron runner produced no console-script"; exit 1; }
  import_check "${OPT}/medchron/.venv/bin/python"
  chown -R root:root "${OPT}/medchron"
  chmod -R a+rX "${OPT}/medchron"
}

case "${1:-}" in
  connectors) connectors ;;
  connector) [ $# -eq 2 ] || { echo "usage: jobs-runtime.sh connector <name>" >&2; exit 2; }; connector "$2" ;;
  medchron) medchron ;;
  import-check) [ $# -eq 2 ] || { echo "usage: jobs-runtime.sh import-check <python>" >&2; exit 2; }; import_check "$2" ;;
  *) echo "usage: jobs-runtime.sh connectors | connector <name> | medchron | import-check <python>" >&2; exit 2 ;;
esac

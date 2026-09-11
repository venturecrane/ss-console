#!/usr/bin/env bash
# medchron-vendor-icd.sh — vendor the CMS ICD code tables on the CONSOLE into
# <dest-dir>, for provision-customer.sh to stage into the seat's vault.
#
# WHY THIS EXISTS (2026-09-04, #2696 review): on a seat the chronology runner
# resolves its ICD tables under install_root/controls/icd/, and that tree is
# root-owned 0750 by the entrypoint so the medchron uid can read its
# falsifier and never rewrite it. The runner's own icd_tables stage therefore
# cannot fetch on a seat (PermissionError), and a vault without icd/ failed
# every job at that stage until someone uploaded the tables by hand. The
# seat tree is pre-seeded read-only by the vault; only a laptop install
# (install_root == data_root) fetches for itself. This script is the
# console-side half of that: the runner's own `icd_fetch.vendor()` — the same
# URLs, members and VERSION.json shape the laptop path writes — run from a
# scratch venv built exactly as CI and the seat image build it.
#
#   medchron-vendor-icd.sh /path/to/scratch/icd
#   -> /path/to/scratch/icd/{icd10cm_order.txt,CMS32_DESC_LONG_DX.txt,VERSION.json}
#
# Nothing here touches R2; the caller uploads. Fails loudly on any step.
set -euo pipefail

DEST="${1:?usage: medchron-vendor-icd.sh <dest-dir>}"
OPERATOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v uv >/dev/null 2>&1 || { echo "medchron-vendor-icd: uv is required on PATH" >&2; exit 1; }

SCRATCH="$(mktemp -d)"
trap 'rm -rf "${SCRATCH}"' EXIT
VENV="${SCRATCH}/venv"
uv venv --quiet "${VENV}"
# The load-bearing three-path install the tree already uses (CI and the seat
# image build): the runner's deps resolve through the connector's path source.
uv pip install --quiet --python "${VENV}/bin/python" \
  "${OPERATOR_DIR}/connectors/_sdk" "${OPERATOR_DIR}/connectors/smokeball" "${OPERATOR_DIR}/runners/medchron"

"${VENV}/bin/python" - "${DEST}" <<'PY'
import sys
from pathlib import Path

from medchron.stages.icd_fetch import vendor

v = vendor(Path(sys.argv[1]))
print(f"vendored {v['icd10cm']['label']} ({v['icd10cm']['sha256'][:12]}) and {v['icd9cm']['label']} ({v['icd9cm']['sha256'][:12]})")
PY
test -f "${DEST}/VERSION.json" || { echo "medchron-vendor-icd: ${DEST}/VERSION.json was not written" >&2; exit 1; }

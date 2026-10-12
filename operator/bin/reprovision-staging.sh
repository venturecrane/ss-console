#!/usr/bin/env bash
# reprovision-staging.sh — (re)provision the STAGING Operator: the permanent,
# faithful pre-production gate for Operator lifecycle work.
#
# WHY THIS EXISTS: a world-class deploy process never tests changes to the
# deploy/boot/voice/env machinery on the one live customer. This stands up
# hermes-smd-staging from the EXACT same tooling as production, so every cut is
# proven here before it touches a real customer.
#
# ISOLATION (the load-bearing part): the normal reprovision injects every
# Infisical /ss key as an env var, and provision-customer.sh stages the
# customer-owned ones (GOOGLE_*, CLIO_*, AGENTMAIL_*, SMOKEBALL_*, webhook
# secrets, the per-seat Anthropic key) onto the Machine. Those are REAL customer
# credentials. This wrapper isolates staging from ALL of them.
#
# FAIL-CLOSED (2026-07 rework, issue #1783). The previous version blanked a
# HAND-MAINTAINED DENYLIST (`CLIO_*` only) and so failed OPEN: any customer-owned
# secret it did not name (AgentMail, Smokeball, per-seat Anthropic, webhook
# secrets) leaked onto the staging box the moment a staging seat bound that
# connector. This version derives the isolate set from the custody classification
# (operator/bin/lib/secret_custody.py, sourced from
# operator/contracts/env-consumption.yaml + the connector tables), so a NEWLY
# added customer-owned secret is isolated automatically. The classification's
# completeness against provision-customer.sh is CI-enforced
# (operator/bin/tests/test_secret_custody.py::test_every_staged_secret_is_classified),
# which is what makes this allowlist trustworthy.
#
# Two isolation moves:
#   - BLANK every customer-owned secret (contract-driven) so the real value never
#     reaches the staging Machine.
#   - SUBSTITUTE GOOGLE_SERVICE_ACCOUNT_JSON with a freshly generated ISOLATED
#     service account (gen-staging-google-cred.py): Google is customer-by-EFFECT
#     (DWD impersonation), so the broker still needs *a* credential to boot
#     identically to prod — wired to nothing real.
# Result: staging borrows none of the live business's access.
#
# The slug is HARDCODED to smd-staging. This wrapper must never touch a real
# customer.
#
# Usage:  operator/bin/reprovision-staging.sh
set -euo pipefail

SLUG="smd-staging"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"

# Contract-driven isolate set, computed BEFORE infisical run (it needs no secret —
# only the classification + the slug). Newline-separated customer-owned source
# names, per-seat __<CID> expanded. Fails loud if the classification cannot load.
ISOLATE_NAMES="$(cd "${REPO_ROOT}" && uv run --quiet --with pyyaml python3 \
  operator/bin/lib/secret_custody.py isolate-names "${SLUG}")" \
  || { echo "reprovision-staging: could not compute the customer-owned isolate set" >&2; exit 1; }
[ -n "${ISOLATE_NAMES}" ] \
  || { echo "reprovision-staging: empty isolate set — refusing to run without isolation" >&2; exit 1; }

exec infisical run --env=prod --path=/ss --silent -- bash -c '
  set -euo pipefail
  HERE="$1"; SLUG="$2"; ISOLATE_NAMES="$3"

  # Blank every customer-owned secret so the real value never reaches the staging
  # Machine. Empty-string assignments, not secrets (gitleaks false positive). # gitleaks:allow
  while IFS= read -r _n; do
    [ -n "${_n}" ] || continue
    export "${_n}="
  done <<< "${ISOLATE_NAMES}"

  # SUBSTITUTE the isolated Google service account (throwaway RSA key, fictitious
  # project). Set AFTER the blank loop (which also blanks GOOGLE_SERVICE_ACCOUNT_JSON).
  # Value flows env -> fly secrets import; never printed.
  GEN_JSON="$(uv run --quiet --with cryptography python3 "${HERE}/gen-staging-google-cred.py")"
  export GOOGLE_SERVICE_ACCOUNT_JSON="$(printf "%s" "${GEN_JSON}" | base64 | tr -d "\n")"
  unset GEN_JSON

  # Non-interactive: Machine secrets persist; nothing to paste.
  # The exit status is provision-customer.sh, never `yes`: when provisioning
  # finishes, `yes` dies of SIGPIPE (141), and under pipefail that alone made a
  # fully successful reprovision (boot smoke 66/66, 2026-10-12) exit 1. A red
  # that cannot go green measures nothing.
  set +o pipefail
  yes s | "${HERE}/provision-customer.sh" "${SLUG}"
  _rc=${PIPESTATUS[1]}
  set -o pipefail
  exit "${_rc}"
' _ "${HERE}" "${SLUG}" "${ISOLATE_NAMES}"

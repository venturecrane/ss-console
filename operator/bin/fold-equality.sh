#!/usr/bin/env bash
# fold-equality.sh: the folded overlay (operator/hermes-smd-overlay) differs from the
# standalone repository's `main` by EXACTLY the files the fold PR declares.
#
# TEMPORARY. This exists for the window between the fold PR (ADR 0095, 2026-10-22)
# and the archive of venturecrane/hermes-smd-overlay: it re-probes, at merge-queue
# entry, the premise that nothing landed on overlay `main` after the tree was
# staged (the repository is locked, but a lock is a setting and this is a read).
# It is removed in the cutover PR that archives the repository; nothing else may
# depend on it.
#
# Fail-closed: a fetch failure is exit 2 and the job fails; a difference outside
# the declared set is exit 1 and names it.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
FOLDED="${REPO_ROOT}/operator/hermes-smd-overlay"
work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

# The files the fold deliberately changed or removed, relative to the overlay root.
# Everything else must be byte-identical to overlay main.
declare -a EXPECTED=(
  # standalone-repository files that have no meaning inside ss-console, and the
  # package metadata (authors, urls) that named the old repository
  ".gitattributes" ".github/workflows/ci.yml" ".gitignore" "AGENTS.md" "README.md" "pyproject.toml"
  # hand-copied fixtures, read in place from ss-console now
  "contracts/skill_capture_v1.json"
  "tests/contract/lane_proofs_cases.json" "tests/contract/policy_inputs.json"
  "tests/contract/seat_gate_binding_snapshot.json" "tests/contract/send-render.yaml"
  "tests/contract/validator_parity_fixtures.json"
  "tests/fixtures/body-canon-vectors.json" "tests/fixtures/roster-canon-cases.json"
  # the tests that read those fixtures (re-pointed) and one docstring
  "tests/contract/test_validator_parity_contract.py" "tests/test_dispatch_body_stamp.py"
  "tests/test_lane_cards.py" "tests/test_policy_cron_sends.py" "tests/test_policy_matrix.py"
  "tests/test_prerendered_dispatch.py" "tests/test_roster_canon_parity.py" "tests/test_send_as.py"
  "tests/test_voice_gate_binding_coverage.py" "plugins/hermes-smd-audit/skill_capture.py"
  # nosemgrep comments: new ones (the Security Summary check sees the tree as new)
  # and reasons added to two the standalone repo carried bare (scripts/nosemgrep-audit.mjs)
  "shared/heartbeat.py" "shared/oauth_callback.py" "shared/outbound_attachment.py"
  "skills_applier/__main__.py" "webhook_gate.py" "shared/group_audience.py"
  # new: pins the directory-name premise
  "tests/test_layout.py"
  # client identity out of fixtures (tests/client-identity-gate.test.ts and
  # tests/medchron-scrub.test.ts scan the whole public repo): a roster domain and
  # a display name in five files, denylisted tokens in three, replaced by synthetic values
  "shared/staged_send.py" "tests/test_record_store_tools.py" "tests/test_record_store_validation.py"
  "tests/test_sender_auth.py" "tests/test_voice_notes.py"
  "tests/test_identifier_filter.py" "tests/test_medchron_plugin.py" "tests/test_outcome_letter_passes_scan.py"
)

echo "fetching venturecrane/hermes-smd-overlay main"
curl -fsSL -o "${work}/main.tgz" "https://api.github.com/repos/venturecrane/hermes-smd-overlay/tarball/main" \
  || { echo "::error::could not fetch the overlay tarball (network/GitHub); refusing to call the fold equal"; exit 2; }
mkdir -p "${work}/main"
tar -xzf "${work}/main.tgz" -C "${work}/main" --strip-components=1

# A directory that exists on one side only is reported once by diff -rq; expand it
# to its files so the declared set stays a list of files (bash 3 compatible: no
# mapfile, the laptop's /usr/bin/env bash is 3.2). diff exits 1 when trees
# differ, which is the expected case, so the pipeline runs without pipefail.
set +o pipefail
# Local build and test residue is ignored (none of it exists in CI, all of it is
# gitignored; a laptop run after `pip install -e` or pytest must not read as drift).
diff -rq "${work}/main" "${FOLDED}" 2>/dev/null \
  | grep -v -E '__pycache__|\.ruff_cache|\.pytest_cache|/build$|/build/|\.egg-info|CUSTOMER_DB|\.db$|/\.venv' \
  | sed -E "s#^Files ${work}/main/(.*) and .* differ\$#\1#; s#^Only in ${work}/main/?([^:]*): (.*)\$#${work}/main/\1/\2#; s#^Only in ${FOLDED}/?([^:]*): (.*)\$#${FOLDED}/\1/\2#" \
  | while IFS= read -r entry; do
      case "${entry}" in
        /*) if [ -d "${entry}" ]; then find "${entry}" -type f; else echo "${entry}"; fi ;;
        *) echo "${entry}" ;;
      esac
    done \
  | sed -E "s#^${work}/main/##; s#^${FOLDED}/##; s#^/+##; s#//+#/#g" \
  | grep -v -E '^(build|\.ruff_cache|\.pytest_cache|\.venv)/|__pycache__|\.egg-info/|^CUSTOMER_DB$|\.db$' \
  | sort -u > "${work}/actual.txt"
set -o pipefail
printf '%s\n' "${EXPECTED[@]}" | sort -u > "${work}/expected.txt"
if ! diff -u "${work}/expected.txt" "${work}/actual.txt"; then
  echo "::error::the folded overlay differs from overlay main outside the declared set (above: - declared but identical, + differs but undeclared)"
  exit 1
fi
echo "fold equality holds: $(wc -l < "${work}/actual.txt" | tr -d ' ') declared differences, nothing else"

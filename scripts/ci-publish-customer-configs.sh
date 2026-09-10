#!/usr/bin/env bash
# CI auto-publish: customer.yaml (git source of truth) → the R2 object the
# running Machine actually reads (s3://${R2_BUCKET_CONFIG}/vaults/<slug>/customer.yaml).
#
# The sibling ci-sync-customer-configs.sh projects a merged config into D1, which
# is what the PORTAL reads. Nothing projected it to R2, which is what the SEAT
# reads. The only writer of that object was operator/bin/provision-customer.sh
# (step 2), run by hand, so merged config sat in git indefinitely while the live
# Machine kept serving whatever the last reprovision happened to upload. That is
# how origin/main carried A&P's authored persona register (#2077) while the seat's
# SOUL.md still said `plainspoken / warm-but-professional / concise` (ss #2082).
#
# Behavior per changed slug:
#   - Object exists in R2  → validate, upload, read the bytes back and prove they
#     match. The seat adopts it on its next boot (entrypoint.sh re-fetches every
#     boot); live-writable fields are picked up sooner by the ADR 0044 poller.
#   - No object (never provisioned) → WARN and skip. See "never create" below.
#
# Two guards, both load-bearing:
#
#   NEVER CREATE. A merge must not be able to conjure config for a seat nobody
#   provisioned. Provisioning binds a config to a Machine, a volume, and a secret
#   set; that binding is Captain-gated, exactly as the D1 sync leaves the FIRST
#   projection manual. A HEAD that fails for any reason OTHER than 404 (bad
#   credential, network, bucket typo) is a hard error, never a skip. "Cannot
#   tell" must not read as "no object here, move along".
#
#   ONE KEY SPACE. This publisher writes customer.yaml and nothing else. The
#   `output-classes.json` key beside it belongs to the portal (ADR 0083): two
#   writers, two key spaces, never the same object. That is structural here, not
#   a matter of restraint: the basename is a literal constant, the slug is
#   charset-constrained, and assert_config_key re-checks the assembled key
#   against a whole-string pattern before any write. There is no input to this
#   script that produces any other key. (#1898: R2 must never diverge from git
#   out of band.)
#
# Inputs (env): BEFORE_SHA, AFTER_SHA, the push range from the workflow.
# R2 auth: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT_URL if set
# (the operator-local path, e.g. `infisical run --env=prod --path=/ss -- ...`),
# otherwise derived from CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID, which CI
# already holds. R2's S3 API accepts a Cloudflare API token directly: the key id
# is the token's own id and the secret is the sha256 of the token value. Deriving
# is the venture's documented mechanism (derive, never mint) and keeps this job
# on the secrets the repo already has.
set -euo pipefail

BEFORE="${BEFORE_SHA:?BEFORE_SHA required}"
AFTER="${AFTER_SHA:?AFTER_SHA required}"

# The ONLY object name this publisher may write. Deliberately a constant: see
# "ONE KEY SPACE" above.
R2_CONFIG_BASENAME="customer.yaml"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"

# A force-push or branch-create event has a zero BEFORE sha; fall back to the
# single pushed commit so we never diff against nothing.
if [[ "$BEFORE" =~ ^0+$ ]]; then
  BEFORE="${AFTER}~1"
fi

# Only customer.yaml. The sibling routine-grid.yaml is a D1-only artifact. It is
# projected into the customer_configs row, never uploaded as its own R2 object,
# so a grid-only merge has nothing to publish here.
#
# Portable line-array read: unlike the D1 sync (CI-only), this script is
# exercised by tests/config-publish-guards.test.ts on an operator box, and macOS
# ships bash 3.2, which has neither mapfile nor associative arrays.
changed=()
while IFS= read -r _line; do
  [[ -n "$_line" ]] && changed+=("$_line")
done < <(git diff --name-only "$BEFORE" "$AFTER" -- \
  'operator/customers/*/customer.yaml' || true)

if [[ ${#changed[@]} -eq 0 ]]; then
  echo "No customer.yaml changes in ${BEFORE}..${AFTER}; nothing to publish."
  exit 0
fi

command -v aws >/dev/null 2>&1 || {
  echo "::error::aws CLI not found (required for the R2 upload)"
  exit 1
}

# ---------- R2 credentials ----------
# Never echoed, never logged, never passed on a command line: they reach the aws
# CLI as env vars on the invocation itself.
if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" ]]; then
  : "${CLOUDFLARE_API_TOKEN:?R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY unset and CLOUDFLARE_API_TOKEN not available to derive them}"
  # `|| true` so a curl/parse failure lands on the explicit message below rather
  # than a bare set -e abort with no explanation.
  token_id=$(curl -sS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    https://api.cloudflare.com/client/v4/user/tokens/verify 2>/dev/null |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s);process.stdout.write(r.success&&r.result&&r.result.id?r.result.id:'')}catch{process.stdout.write('')}})" || true)
  if [[ -z "$token_id" ]]; then
    echo "::error::could not derive the R2 key id from CLOUDFLARE_API_TOKEN (/user/tokens/verify did not return a token id)"
    exit 1
  fi
  # sha256sum on the runner, shasum on a macOS operator box.
  if command -v sha256sum >/dev/null 2>&1; then
    token_digest=$(printf %s "${CLOUDFLARE_API_TOKEN}" | sha256sum | awk '{print $1}')
  else
    token_digest=$(printf %s "${CLOUDFLARE_API_TOKEN}" | shasum -a 256 | awk '{print $1}')
  fi
  R2_ACCESS_KEY_ID="$token_id"
  R2_SECRET_ACCESS_KEY="$token_digest"
fi
if [[ -z "${R2_ENDPOINT_URL:-}" ]]; then
  : "${CLOUDFLARE_ACCOUNT_ID:?R2_ENDPOINT_URL unset and CLOUDFLARE_ACCOUNT_ID not available to build it}"
  R2_ENDPOINT_URL="https://${CLOUDFLARE_ACCOUNT_ID}.r2.cloudflarestorage.com"
fi

# ---------- helpers ----------

# The last gate before any write. Belt to the constant basename's braces: even if
# a future edit made the slug or the basename variable, a key outside this one
# shape aborts the run rather than writing to a neighbouring key space.
#
# The slug segment carries the canonical pattern (#2285), not a looser one — a
# key space this guard admits is a key space a seat must be able to boot from.
assert_config_key() {
  local key="$1"
  if [[ ! "$key" =~ ^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/customer\.yaml$ ]]; then
    echo "::error::refusing to write R2 key outside the customer.yaml key space: ${key}"
    exit 1
  fi
}

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

# Dedupe to unique slugs. A space-delimited seen-list rather than an associative
# array, for the bash 3.2 reason above.
seen_slugs=" "
slugs=()
for path in "${changed[@]}"; do
  slug=$(basename "$(dirname "$path")")
  case "${seen_slugs}" in *" ${slug} "*) continue ;; esac
  seen_slugs="${seen_slugs}${slug} "
  slugs+=("$slug")
done

work_dir=$(mktemp -d)
trap 'rm -rf "${work_dir}"' EXIT

fail=0
for slug in "${slugs[@]}"; do
  cfg="operator/customers/${slug}/customer.yaml"

  # Template/staging dirs are not live customers. They also carry deliberate
  # placeholder values that fail validation, so they must be skipped BEFORE the
  # validator runs, not after.
  if [[ "$slug" == _* ]]; then
    echo "Skipping template dir: $slug"
    continue
  fi
  # A decommissioned seat keeps its dir as `<slug>.decommissioned.<date>` with
  # its customer.yaml inside (operator/bin/lib/decommission.py, step 08). It is
  # a historical record, not a seat: never publish it, and never let its dotted
  # name reach the slug guard below as if someone had authored it.
  if [[ "$slug" == *.decommissioned.* ]]; then
    echo "Skipping decommissioned seat dir: $slug"
    continue
  fi

  # customer.yaml gone → the customer dir was retired. Retirement is a manual,
  # Captain-gated operation; a merge never deletes the object a live Machine
  # boots from.
  if [[ ! -f "$cfg" ]]; then
    echo "::warning::$cfg is absent; customer retirement is manual (no automatic R2 delete)."
    continue
  fi

  # The slug becomes part of the R2 key; constrain it hard. Canonical pattern
  # (#2285): lowercase alphanumerics + dashes, 2-40 chars, no leading/trailing
  # dash — the same shape operator/adapter/namespace_assertion.py demands at
  # seat boot. This guard writes to R2, so it must never be the loose one.
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]]; then
    echo "::error::Refusing to publish suspicious slug: $slug"
    fail=1
    continue
  fi

  key="vaults/${slug}/${R2_CONFIG_BASENAME}"
  assert_config_key "$key"

  echo "── Publishing $slug ──"

  # Validate against the canonical pre-merge gate (ADR 0019). An invalid config
  # reaching this object is a seat kill on the next restart: the boot fetch and
  # the ADR 0044 poller both refuse it, and a Machine on a fresh volume has
  # nothing to fall back to.
  if ! npx --quiet tsx scripts/validate-customer-yaml.ts "$cfg"; then
    echo "::error::$slug: customer.yaml failed validation; not publishing"
    fail=1
    continue
  fi

  # NEVER CREATE. Distinguish "no such object" from "could not tell": only a 404
  # is a skip.
  head_err="${work_dir}/${slug}.head.err"
  if r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "$key" \
       >/dev/null 2>"$head_err"; then
    :
  elif grep -q '(404)' "$head_err"; then
    echo "::warning::$slug has no object at s3://${R2_BUCKET_CONFIG}/${key}; first publish is Captain-gated; run operator/bin/provision-customer.sh."
    continue
  else
    echo "::error::$slug: could not read s3://${R2_BUCKET_CONFIG}/${key} (not a 404); refusing to guess whether the seat is provisioned"
    sed 's/^/    /' "$head_err"
    fail=1
    continue
  fi

  if ! r2 s3 cp "$cfg" "s3://${R2_BUCKET_CONFIG}/${key}" --only-show-errors; then
    echo "::error::$slug: R2 upload failed"
    fail=1
    continue
  fi

  # Prove the write landed by reading the object back and comparing bytes. The
  # D1 sync can verify with a stamped git_sha because it controls the row's
  # columns; the R2 object is the authored file verbatim (stamping it would make
  # R2 diverge from git, which is the thing #1898 forbids), so the proof is
  # byte identity.
  readback="${work_dir}/${slug}.readback.yaml"
  if ! r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${key}" "$readback" --only-show-errors; then
    echo "::error::$slug: published but could not read the object back to verify"
    fail=1
    continue
  fi
  if cmp -s "$cfg" "$readback"; then
    file_sha=$(git rev-list -1 "$AFTER" -- "$cfg" || true)
    echo "$slug published: s3://${R2_BUCKET_CONFIG}/${key} (git ${file_sha:-unknown})"
    echo "  the seat adopts this on its next restart; live-writable fields apply sooner via the ADR 0044 poller"
  else
    echo "::error::$slug: R2 object does not match the file that was uploaded"
    fail=1
  fi
done

exit "$fail"

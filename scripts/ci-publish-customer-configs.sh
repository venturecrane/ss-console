#!/usr/bin/env bash
# The one writer of a seat's config in R2 (ADR 0094, as amended by Wave 2 W4):
#   vaults/<slug>/customer.yaml           the object the seat boots from
#   fleet/specs/<slug>/<sha256>.json      the compiled seat spec, content-addressed
#   vaults/<slug>/spec-pointer.json       names that spec and signs its bytes
# Nothing else writes these keys. The nightly reconciler
# (ci-reconcile-r2-customer-configs.sh) only reads them and pages; provisioning
# writes customer.yaml through the same compiler, unsigned.
#
# WHY A SPEC. The seat used to learn its config through twelve routes, four of
# them writing this one customer.yaml, none signed. The spec
# (scripts/compile-seat-spec.ts) is one canonical description of what the seat is
# configured to run and what that requires of its image, signed with an Ed25519
# key held only in the protected `seat-spec` GitHub environment. The seat's root
# spec_verifier (overlay config_applier) checks the signature against the key
# baked into its image and the requirements against its own image, and reports
# the verdict on its heartbeat. SHADOW MODE: the seat still applies
# customer.yaml as before; the spec is verified and reported, not applied.
#
# WHICH SEATS, each push:
#   - every slug whose customer.yaml changed in BEFORE_SHA..AFTER_SHA, and
#   - every other seat at AFTER_SHA, converged: its customer.yaml republished if
#     R2's bytes differ from git, its spec republished if what this commit
#     compiles differs from the published spec in anything but compiled_from.
#     A range that was never processed is therefore healed by the next push,
#     which is what the nightly reconciler used to do by writing.
# A refusal for a CHANGED slug fails the job; for a converged one it is a
# warning (the nightly reconciler pages it), so one held seat cannot turn every
# merge red.
#
# GUARDS, all load-bearing:
#   NEVER CREATE. Only a 404 on vaults/<slug>/customer.yaml skips a seat (first
#     publish is provisioning's, Captain-gated); any other HEAD failure is an
#     error, never a skip.
#   KEY SPACE. The basename is a constant, the slug is charset-bounded, and every
#     key is re-checked against a whole-string pattern before a write; the
#     portal-owned output-classes.json beside it is unreachable (ADR 0083).
#   IMAGE. The compiler refuses a config naming a skill, pin, pre_run or
#     capability the seat's running image (plus its skills release) lacks: the
#     2026-10-13 outage guard, now one implementation (scripts/lib/seat-spec.ts).
#     A lane whose image validates firm inputs against another schema refuses
#     the spec only.
#   PROOF. Every write is read back and compared byte for byte.
#   SIGNING. SEAT_SPEC_SIGNING_KEY absent: customer.yaml publishes as before and
#     the spec is NOT published, with a loud warning, so main stays deployable
#     until the environment exists.
#
# Inputs (env): BEFORE_SHA, AFTER_SHA (the push range); SEAT_SPEC_SIGNING_KEY
# (optional, never echoed); FLEET_IMAGE_LOOKUP=1 to resolve each seat's image
# through D1. R2 auth: scripts/lib/r2-creds.sh (derive, never mint).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/seat-dir.sh
source "${SCRIPT_DIR}/lib/seat-dir.sh"
# shellcheck source=lib/r2-creds.sh
source "${SCRIPT_DIR}/lib/r2-creds.sh"
BEFORE="${BEFORE_SHA:?BEFORE_SHA required}"
AFTER="${AFTER_SHA:?AFTER_SHA required}"

# The ONLY customer.yaml object name this publisher may write.
R2_CONFIG_BASENAME="customer.yaml"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"

# A force-push or branch-create event has a zero BEFORE sha.
if [[ "$BEFORE" =~ ^0+$ ]]; then
  BEFORE="${AFTER}~1"
fi

# Portable line-array reads: macOS bash 3.2 has neither mapfile nor
# associative arrays, and tests/config-publish-guards.test.ts runs there too.
changed=()
while IFS= read -r _line; do
  _dir=$(seat_dir_of_path "$_line") && changed+=("$_dir")
done < <(git diff --name-only "$BEFORE" "$AFTER" -- 'operator/customers/*/customer.yaml' || true)
seats=()
while IFS= read -r _line; do
  case "$_line" in */customer.yaml) _dir=$(seat_dir_of_path "$_line") && seats+=("$_dir") ;; esac
done < <(git ls-tree -r --name-only "$AFTER" -- operator/customers || true)

if [[ ${#changed[@]} -eq 0 ]]; then
  echo "No customer.yaml changes in ${BEFORE}..${AFTER}; nothing to publish from the range."
fi
if [[ ${#changed[@]} -eq 0 && ${#seats[@]} -eq 0 ]]; then
  exit 0
fi

command -v aws >/dev/null 2>&1 || {
  echo "::error::aws CLI not found (required for R2)"
  exit 1
}
derive_r2_creds

assert_config_key() {
  local key="$1"
  if [[ ! "$key" =~ ^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/customer\.yaml$ ]]; then
    echo "::error::refusing to write R2 key outside the customer.yaml key space: ${key}"
    exit 1
  fi
}
assert_spec_key() {
  if [[ ! "$1" =~ ^(fleet/specs/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/[0-9a-f]{64}\.json|vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/spec-pointer\.json)$ ]]; then
    echo "::error::refusing to write R2 key outside the seat-spec key space: $1"
    exit 1
  fi
}

r2() {
  AWS_ACCESS_KEY_ID="${R2_ACCESS_KEY_ID}" \
  AWS_SECRET_ACCESS_KEY="${R2_SECRET_ACCESS_KEY}" \
    aws "$@" --endpoint-url "${R2_ENDPOINT_URL}"
}

# shellcheck source=lib/seat-spec-inputs.sh
source "${SCRIPT_DIR}/lib/seat-spec-inputs.sh"

work_dir=$(mktemp -d)
trap 'rm -rf "${work_dir}"' EXIT

# put_and_prove <file> <key>: upload, read back, compare bytes.
put_and_prove() {
  local src="$1" dest="$2" back
  back="${work_dir}/readback.$$"
  if ! r2 s3 cp "$src" "s3://${R2_BUCKET_CONFIG}/${dest}" --only-show-errors; then
    echo "::error::R2 upload of ${dest} failed"
    return 1
  fi
  if ! r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${dest}" "$back" --only-show-errors; then
    echo "::error::${dest} was uploaded but could not be read back to verify"
    return 1
  fi
  if ! cmp -s "$src" "$back"; then
    echo "::error::${dest} does not match the bytes that were uploaded"
    return 1
  fi
  rm -f "$back"
}

is_changed() {
  local s
  for s in ${changed[@]+"${changed[@]}"}; do [[ "$s" == "$1" ]] && return 0; done
  return 1
}

# refusal <slug> <message>: an error for a changed slug, a warning otherwise.
refusal() {
  if is_changed "$1"; then
    echo "::error::$1: $2"
    fail=1
  else
    echo "::warning::$1: $2 (not changed in this push; the nightly reconciler pages it)"
  fi
}

# Publish the spec the compiler wrote to <out>, pointer last: the pointer is the
# commit point, so a seat never sees a pointer to a spec that is not there.
publish_spec() {
  local slug="$1" out="$2" sha
  if [[ ! -f "${out}/pointer.json" ]]; then
    echo "::warning::${slug}: the seat spec was compiled but NOT published: SEAT_SPEC_SIGNING_KEY is not available to this job (the seat-spec environment). customer.yaml publishing is unaffected."
    return 0
  fi
  sha="$(_json_field "${out}/pointer.json" spec_sha256)"
  assert_spec_key "fleet/specs/${slug}/${sha}.json"
  assert_spec_key "vaults/${slug}/spec-pointer.json"
  put_and_prove "${out}/spec.json" "fleet/specs/${slug}/${sha}.json" || return 1
  put_and_prove "${out}/pointer.json" "vaults/${slug}/spec-pointer.json" || return 1
  echo "${slug} spec published: fleet/specs/${slug}/${sha}.json (signed, pointer written)"
}

publish_customer_yaml() {
  local slug="$1" cfg="$2" key="$3" published="$4"
  if [[ -f "$published" ]] && cmp -s "$cfg" "$published"; then
    echo "${slug}: s3://${R2_BUCKET_CONFIG}/${key} already holds these bytes"
    return 0
  fi
  put_and_prove "$cfg" "$key" || return 1
  echo "$slug published: s3://${R2_BUCKET_CONFIG}/${key} (git $(git rev-list -1 "$AFTER" -- "$cfg" || echo unknown))"
  echo "  the seat adopts this on its next restart; live-writable fields apply sooner via the ADR 0044 poller"
}

publish_seat() {
  local slug="$1" cfg skip head_err inputs out rc
  cfg="operator/customers/${slug}/customer.yaml"
  # Scaffolds and decommissioned tombstones are not seats (scripts/lib/seat-dir.sh),
  # and carry placeholder values that fail validation: skipped first.
  if skip=$(seat_dir_skip_reason "$slug"); then
    echo "Skipping ${skip} dir: $slug"
    return 0
  fi
  if [[ ! -f "$cfg" ]]; then
    echo "::warning::$cfg is absent; customer retirement is manual (no automatic R2 delete)."
    return 0
  fi
  # The canonical slug pattern (#2285); tests/customer-slug-pattern.test.ts pins it.
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]]; then
    echo "::error::Refusing to publish suspicious slug: $slug"
    fail=1
    return 0
  fi
  key="vaults/${slug}/${R2_CONFIG_BASENAME}"
  assert_config_key "$key"
  echo "── ${slug} ──"
  if ! npx --quiet tsx scripts/validate-customer-yaml.ts "$cfg"; then
    echo "::error::$slug: customer.yaml failed validation; not publishing"
    fail=1
    return 0
  fi
  head_err="${work_dir}/${slug}.head.err"
  if ! r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "$key" >/dev/null 2>"$head_err"; then
    if grep -q '(404)' "$head_err"; then
      echo "::warning::$slug has no object at s3://${R2_BUCKET_CONFIG}/${key}; first publish is Captain-gated; run operator/bin/provision-customer.sh."
      return 0
    fi
    echo "::error::$slug: could not read s3://${R2_BUCKET_CONFIG}/${key} (not a 404); refusing to guess whether the seat is provisioned"
    sed 's/^/    /' "$head_err"
    fail=1
    return 0
  fi
  inputs="${work_dir}/${slug}.in"
  out="${work_dir}/${slug}.out"
  if ! seat_spec_inputs "$slug" "$inputs"; then
    echo "::error::$slug: could not read what the seat runs now; not publishing"
    fail=1
    return 0
  fi
  rc=0
  seat_spec_compile "$slug" "$cfg" "$inputs" "$out" --sign || rc=$?
  # "Unchanged" is only true of a published spec that still verifies. A pointer
  # that does not (tampered, signed by a retired key) is re-signed, not kept.
  if [[ "$rc" -eq 10 ]] && ! _seat_tsx "$SEAT_SPEC_COMPILER" verify \
       --pointer "${inputs}/spec-pointer.json" --spec "${inputs}/current-spec.json" >/dev/null 2>&1; then
    echo "::warning::${slug}: the published seat spec does not verify against the committed key; re-signing it"
    rm -f "${inputs}/current-spec.json"
    rc=0
    seat_spec_compile "$slug" "$cfg" "$inputs" "$out" --sign || rc=$?
  fi
  case "$rc" in
    1)
      refusal "$slug" "not publishing; the seat's running image cannot run this config (above). Reprovision the seat to ship it."
      return 0
      ;;
    0 | 3 | 10) ;;
    *)
      echo "::error::$slug: the spec compiler failed (exit ${rc})"
      fail=1
      return 0
      ;;
  esac
  publish_customer_yaml "$slug" "$cfg" "$key" "${inputs}/published.yaml" || fail=1
  if [[ "$rc" -eq 3 ]]; then
    refusal "$slug" "the seat spec is refused (above); the published spec stays as it was"
  elif [[ "$rc" -eq 10 ]]; then
    echo "${slug}: the published seat spec is current"
  else
    publish_spec "$slug" "$out" || fail=1
  fi
}

fail=0
done_list=" "
for slug in ${changed[@]+"${changed[@]}"} ${seats[@]+"${seats[@]}"}; do
  case "$done_list" in *" ${slug} "*) continue ;; esac
  done_list="${done_list}${slug} "
  publish_seat "$slug"
done

exit "$fail"

#!/usr/bin/env bash
# Reconciler: make the R2 object every Machine BOOTS FROM converge on the
# customer.yaml main actually carries (ss #2305).
#
# WHY. This is the R2 twin of ci-reconcile-customer-configs.sh (#2292), and the
# gap is the same one: ci-publish-customer-configs.sh is a TRIGGER, not a
# reconciler. It diffs one push range (`github.event.before..github.sha`,
# deploy.yml:202-203) and publishes whatever changed inside it. A range that is
# never processed — a failed deploy job, a skipped run, a force-push, the ~07-31
# history rewrite — is never revisited, because no LATER push's range contains
# it. Nothing compared R2 against git on any schedule.
#
# The stakes are the mirror image of #2292 and worse. A stale D1 row means the
# PORTAL shows the wrong posture. A stale R2 object means the SEAT ENFORCES the
# wrong one: entrypoint.sh re-fetches this object every boot, and a Machine on a
# fresh volume has nothing else to boot from.
#
# WHY IT COULD NOT SHARE CODE WITH THE D1 RECONCILER. Different comparison
# primitive. The D1 row carries a stamped `git_sha`, so provenance is comparable.
# The R2 object is the authored file VERBATIM — stamping provenance into it
# would make R2 diverge from git, which is exactly what #1898 forbids — so the
# only available comparison is BYTE IDENTITY between the object and what git
# carries at HEAD. Byte comparison is the whole check; there is no stamp to
# orphan. (The seats are enumerated from HEAD's TREE and the bytes are read off
# the checkout, with a clean-tree guard below making those the same thing.)
#
# TWO SIGNALS:
#
#   DRIFT  — the object's bytes are not the blob's bytes. Re-published from the
#            blob, then proven by reading the object back (the publisher's own
#            proof). Reported even after self-healing, for the same reason
#            #2292 reports: how long a seat enforced a config main did not
#            author is the thing worth knowing, regardless of who fixed it.
#
#   REFUSED — the object drifted, but HEAD's config names a skill or pre_run
#            the seat's running image (plus its skills release) does not carry,
#            so it is NOT republished. This is the image guard the publisher
#            runs (ADR 0093, the 2026-10-13 A&P outage), from the same shared
#            implementation (scripts/lib/seat-image-guard.sh). Without it this
#            reconciler reopened the outage every night: the publisher refused
#            a config and left the old object in place, this job saw the byte
#            drift and wrote the refused config anyway, and the seat adopted it
#            on its next restart. A refusal does not heal on its own; it clears
#            when the seat is reprovisioned onto an image that carries what the
#            config names, so it is reported on every run until then. Checked
#            in a dry run too: the guard only reads.
#
# and one warn-only observation:
#
#   UNAUTHORED — an object under vaults/ whose customer.yaml is gone from git.
#            Warned about, never deleted. See "never deletes" below.
#
# WHAT IT WILL NOT DO:
#   - Never creates. A 404 on the object is a SKIP with a warning. The first
#     publish binds a config to a Machine, a volume and a secret set, and stays
#     Captain-gated (ci-publish-customer-configs.sh, "NEVER CREATE"). Any head-object
#     failure that is NOT a 404 is a hard error, never a skip: "could not tell"
#     must not read as "no object here".
#   - Never deletes. Retirement is manual. This is structural, not a rule to
#     remember: the republish loop's universe is the set of customer.yaml files
#     on HEAD, and there is no delete call anywhere in this script.
#   - Never writes any other key. assert_config_key re-checks the assembled key
#     against a whole-string pattern before every write, same constant basename
#     and same canonical slug pattern (#2285) the publisher uses.
#
# WHY THE SHALLOW REFUSAL IS LOAD-BEARING HERE. The byte comparison alone would
# survive a depth-1 clone (HEAD's tree and files are present). The REPORT would
# not: a finding names the commit and date that authored the current bytes, via
# `git log -1 -- <path>`, which in a shallow slice reports the newest commit IN
# THE SLICE. "This seat has been serving the wrong config since <today>" is a
# confident lie, and the age is the whole point of reporting a self-healed
# drift. Refuse rather than emit it.
#
# bash 3.2 compatible on purpose — like both siblings, this is exercised by a
# test suite that may run on a macOS operator box. No mapfile, no associative
# arrays, no bare `"${arr[@]}"` under `set -u`.
#
# Inputs (env):
#   R2_RECONCILE_DRY_RUN=1   detect and report; never write to R2.
#   R2_BUCKET_CONFIG         override the bucket (default smd-customer-config).
# R2 auth: R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY / R2_ENDPOINT_URL if set,
# otherwise derived from CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID exactly as
# the publisher derives them (derive, never mint).
#
# The reconcile ref is HEAD, deliberately not configurable: a ref that differed
# from the checkout would compare against one commit and publish another.
#
# Exit codes (the #2292 HOLDS-vs-FINDS contract, unchanged):
#   0  converged — every authored seat's object is byte-identical to its blob
#   1  cannot evaluate, or a republish failed to land (the control itself is
#      broken; a failed workflow run is the right noise for that)
#   2  findings — at least one object drifted, whether republished or REFUSED
#      by the image guard (the workflow opens an issue; REFUSED gets its own)
set -euo pipefail

DRY_RUN="${R2_RECONCILE_DRY_RUN:-}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The ONLY object name this reconciler may write, same constant as the
# publisher's. See "never writes any other key" above.
R2_CONFIG_BASENAME="customer.yaml"
R2_BUCKET_CONFIG="${R2_BUCKET_CONFIG:-smd-customer-config}"

if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo unknown)" != "false" ]]; then
  echo "::error::refusing to reconcile from a shallow clone (the checkout needs fetch-depth: 0)"
  exit 1
fi

head_sha=$(git rev-parse HEAD 2>/dev/null || true)
if [[ -z "$head_sha" ]]; then
  echo "::error::could not resolve HEAD; nothing to reconcile against"
  exit 1
fi

# The comparison bytes are the CHECKED-OUT file, because that is what the
# publisher publishes and what a `.gitattributes` filter, if one ever appears,
# would make differ from the raw blob. That is only honest while the checkout IS
# what HEAD authored, so require it — a modified or staged customer.yaml would
# make this control compare R2 against something main never carried, and
# silently "converge" R2 onto it.
if ! dirty=$(git status --porcelain -- operator/customers 2>/dev/null); then
  echo "::error::could not read the working-tree status of operator/customers"
  exit 1
fi
if [[ -n "$dirty" ]]; then
  echo "::error::operator/customers is not clean at HEAD; refusing to reconcile against a working tree main did not author"
  printf '%s\n' "$dirty" | sed 's/^/    /'
  exit 1
fi
echo "Reconciling s3://${R2_BUCKET_CONFIG}/vaults/*/${R2_CONFIG_BASENAME} against HEAD ${head_sha}"
if [[ -n "$DRY_RUN" ]]; then
  echo "DRY RUN: findings will be reported, nothing will be written."
fi

command -v aws >/dev/null 2>&1 || {
  echo "::error::aws CLI not found (required to read R2)"
  exit 1
}

# ---------- R2 credentials ----------
# Never echoed, never logged, never passed on a command line: they reach the aws
# CLI as env vars on the invocation itself. Lifted verbatim from the publisher so
# both jobs authenticate identically.
if [[ -z "${R2_ACCESS_KEY_ID:-}" || -z "${R2_SECRET_ACCESS_KEY:-}" ]]; then
  : "${CLOUDFLARE_API_TOKEN:?R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY unset and CLOUDFLARE_API_TOKEN not available to derive them}"
  token_id=$(curl -sS -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
    https://api.cloudflare.com/client/v4/user/tokens/verify 2>/dev/null |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s);process.stdout.write(r.success&&r.result&&r.result.id?r.result.id:'')}catch{process.stdout.write('')}})" || true)
  if [[ -z "$token_id" ]]; then
    echo "::error::could not derive the R2 key id from CLOUDFLARE_API_TOKEN (/user/tokens/verify did not return a token id)"
    exit 1
  fi
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

# The last gate before any write, identical in shape to the publisher's.
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

# The image guard every writer of this key runs (seat_image_guard). Sourced
# after r2() because it reads R2 through it.
# shellcheck source=lib/seat-image-guard.sh
source "${SCRIPT_DIR}/lib/seat-image-guard.sh"

# sha256sum on the runner, shasum on a macOS operator box.
digest() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

work_dir=$(mktemp -d)
trap 'rm -rf "${work_dir}"' EXIT

# ---------- enumerate the authored seats ----------
# From the git TREE at HEAD, not from a working-tree glob: the whole comparison
# is "does R2 hold what main authored", and an untracked or modified file in the
# checkout is not what main authored.
if ! tree_paths=$(git ls-tree -r --name-only HEAD -- operator/customers 2>/dev/null); then
  echo "::error::could not list operator/customers at HEAD"
  exit 1
fi

paths=()
while IFS= read -r _line; do
  case "$_line" in */customer.yaml) paths+=("$_line") ;; esac
done <<<"$tree_paths"

if [[ ${#paths[@]} -eq 0 ]]; then
  echo "::error::HEAD carries no operator/customers/*/customer.yaml; that is not a converged state, it is an unreadable one"
  exit 1
fi

# ---------- reconcile ----------
hard_fail=0
checked=0
n_drift=0
n_missing=0
n_unauthored=0
n_refused=0
findings=""
authored_slugs=" "

for cfg in "${paths[@]}"; do
  slug=$(basename "$(dirname "$cfg")")

  # Template dirs are not live customers and are never published; skipped before
  # the charset guard so a leading underscore reads as "skip this", not as
  # "someone put a hostile string in the tree".
  if [[ "$slug" == _* ]]; then
    echo "Skipping template dir: ${slug}"
    continue
  fi
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]]; then
    echo "::error::refusing to reconcile suspicious slug: ${slug}"
    hard_fail=1
    continue
  fi

  authored_slugs="${authored_slugs}${slug} "
  checked=$((checked + 1))
  key="vaults/${slug}/${R2_CONFIG_BASENAME}"
  assert_config_key "$key"

  # `git ls-tree` named it, so this is HEAD's path, and the clean-tree guard
  # above makes the file on disk HEAD's bytes.
  if [[ ! -f "$cfg" ]]; then
    echo "::error::${slug}: ${cfg} is in HEAD's tree but not on disk"
    hard_fail=1
    continue
  fi

  # NEVER CREATE. Only a 404 is a skip.
  head_err="${work_dir}/${slug}.head.err"
  if r2 s3api head-object --bucket "${R2_BUCKET_CONFIG}" --key "$key" \
       >/dev/null 2>"$head_err"; then
    :
  elif grep -q '(404)' "$head_err"; then
    echo "::warning::${slug} has no object at s3://${R2_BUCKET_CONFIG}/${key}; first publish is Captain-gated; run operator/bin/provision-customer.sh."
    n_missing=$((n_missing + 1))
    continue
  else
    echo "::error::${slug}: could not read s3://${R2_BUCKET_CONFIG}/${key} (not a 404); refusing to guess whether the seat is provisioned"
    sed 's/^/    /' "$head_err"
    hard_fail=1
    continue
  fi

  # A download that fails is a HOLD, never a clean verdict: an object we could
  # not read is an object we cannot say anything about.
  live="${work_dir}/${slug}.live.yaml"
  if ! r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${key}" "$live" --only-show-errors; then
    echo "::error::${slug}: object exists but could not be downloaded; cannot compare"
    hard_fail=1
    continue
  fi

  want_sha=$(digest "$cfg")
  live_sha=$(digest "$live")

  if [[ "$live_sha" == "$want_sha" ]]; then
    echo "${slug}: in sync (sha256 ${want_sha})"
    continue
  fi

  # The age of the divergence — the thing the shallow refusal above protects.
  authored=$(git log -1 --format='%h %ad' --date=short -- "$cfg" 2>/dev/null || true)
  echo "── ${slug} DRIFTED: R2 has ${live_sha}, HEAD carries ${want_sha} ──"
  echo "  ${cfg} was last authored at ${authored:-unknown}; the seat has been booting the other bytes since some point after that."

  # IMAGE GUARD, before anything decides to write, and in a dry run too. The
  # live object is the "what the seat boots from now" the checker compares
  # against when it cannot read the image manifest; we already hold it.
  guard_err="${work_dir}/${slug}.guard.err"
  if ! seat_image_guard "$slug" "$cfg" "$work_dir" "$live" >/dev/null 2>"$guard_err"; then
    echo "::error::${slug}: NOT republishing; the seat's running image cannot run HEAD's config. The object stays as it is until the seat is reprovisioned onto an image that carries it."
    sed 's/^/    /' "$guard_err"
    n_refused=$((n_refused + 1))
    findings="${findings}  REFUSED  ${slug}: R2 ${live_sha} != git ${want_sha} (authored ${authored:-unknown}); the image guard refused:
$(sed 's/^/             /' "$guard_err")
"
    continue
  fi

  n_drift=$((n_drift + 1))
  findings="${findings}  DRIFT  ${slug}: R2 ${live_sha} != git ${want_sha} (authored ${authored:-unknown})
"

  if [[ -n "$DRY_RUN" ]]; then
    echo "  dry run: would republish ${slug}"
    continue
  fi

  # Validate before writing, same gate the publisher applies: an invalid config
  # on this object is a seat kill on the next restart. Validated at its REAL
  # path, not a temp copy — the validator resolves customer-local skill bodies
  # relative to the yaml's own directory (validate-customer-yaml.ts:63-68), so a
  # copy elsewhere would fail a seat that binds one.
  if ! npx --quiet tsx scripts/validate-customer-yaml.ts "$cfg"; then
    echo "::error::${slug}: HEAD's customer.yaml failed validation; not republishing"
    hard_fail=1
    continue
  fi

  if ! r2 s3 cp "$cfg" "s3://${R2_BUCKET_CONFIG}/${key}" --only-show-errors; then
    echo "::error::${slug}: R2 upload failed"
    hard_fail=1
    continue
  fi

  # Landback proof: read the object back and prove byte identity against what we
  # just uploaded. Same proof the publisher uses, and the only one available —
  # there is no stamp to read.
  readback="${work_dir}/${slug}.readback.yaml"
  if ! r2 s3 cp "s3://${R2_BUCKET_CONFIG}/${key}" "$readback" --only-show-errors; then
    echo "::error::${slug}: republished but could not read the object back to verify"
    hard_fail=1
    continue
  fi
  got_sha=$(digest "$readback")
  if [[ "$got_sha" == "$want_sha" ]]; then
    echo "  ${slug} republished: s3://${R2_BUCKET_CONFIG}/${key} (sha256 ${got_sha})"
    echo "  the seat adopts this on its next restart; live-writable fields apply sooner via the ADR 0044 poller"
  else
    echo "::error::${slug}: republish did not land (want ${want_sha}, object now has ${got_sha})"
    hard_fail=1
  fi
done

# ---------- objects with no authored file ----------
# The mirror of the D1 reconciler's NO-YAML signal. Warn-only and never fatal on
# its own, deliberately: retirement is manual, and a seat that was retired from
# git but whose object was left in place is a state a human decides about.
#
# A FAILED LIST IS NOT A HARD FAIL, and that asymmetry is intentional. Every
# other read here feeds the verdict, so "could not read" must fail the run. This
# one feeds a warning that never alerts, and failing the whole control on it
# would take the drift check — the part that does alert — down with it, e.g. the
# day the token loses ListBucket. The log says the sub-check could not run,
# which is the honest report.
listing="${work_dir}/listing.txt"
if r2 s3api list-objects-v2 --bucket "${R2_BUCKET_CONFIG}" --prefix "vaults/" \
     --query 'Contents[].Key' --output text >"$listing" 2>"${work_dir}/list.err"; then
  for k in $(tr '\t' '\n' <"$listing"); do
    case "$k" in
      vaults/*/"${R2_CONFIG_BASENAME}") ;;
      *) continue ;;
    esac
    obj_slug=${k#vaults/}
    obj_slug=${obj_slug%/${R2_CONFIG_BASENAME}}
    case "${authored_slugs}" in *" ${obj_slug} "*) continue ;; esac
    echo "::warning::${obj_slug} has an object at s3://${R2_BUCKET_CONFIG}/${k} but no customer.yaml on HEAD; seat retirement is manual (no automatic R2 delete)."
    n_unauthored=$((n_unauthored + 1))
    findings="${findings}  UNAUTHORED  ${obj_slug}
"
  done
else
  echo "::warning::could not list s3://${R2_BUCKET_CONFIG}/vaults/; the unauthored-object check did not run (the drift check above did)"
  sed 's/^/    /' "${work_dir}/list.err"
fi

# ---------- report ----------
echo
echo "── R2 reconcile summary ──"
echo "seats checked:   ${checked}"
echo "drifted:         ${n_drift}"
echo "unprovisioned:   ${n_missing}"
echo "unauthored:      ${n_unauthored}"
echo "refused (image): ${n_refused}"
if [[ -n "$findings" ]]; then
  printf '%s' "$findings"
fi

if [[ "$hard_fail" -ne 0 ]]; then
  echo "R2 reconcile FAILED: the control could not complete."
  exit 1
fi
if [[ "$n_drift" -gt 0 ]]; then
  if [[ -n "$DRY_RUN" ]]; then
    echo "R2 reconcile found drift (dry run — nothing written)."
  else
    echo "R2 reconcile republished the objects above; reporting because the silent divergence is itself the defect."
  fi
fi
if [[ "$n_refused" -gt 0 ]]; then
  echo "R2 reconcile left ${n_refused} drifted object(s) in place: the seat's running image cannot run HEAD's config (REFUSED above). Reprovision the seat to ship it."
fi
if [[ "$n_drift" -gt 0 || "$n_refused" -gt 0 ]]; then
  exit 2
fi
echo "R2 reconcile clean: every authored seat's object is byte-identical to what HEAD carries."
exit 0

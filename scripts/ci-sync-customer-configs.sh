#!/usr/bin/env bash
# CI auto-sync: customer.yaml + routine-grid.yaml (git source of truth) →
# customer_configs D1 projection (#1308 auto-sync extension, ADR 0012 §5,
# ADR 0075).
#
# Closes the stale-projection window: before this job, a merged customer.yaml
# change never reached the live portal until someone remembered to re-project
# by hand — the mechanism behind the "removed in git but still shows live"
# failure class (see feedback: the persona-name saga, 2026-07-08/13). The
# routine-grid.yaml (ADR 0075) is a sibling artifact projected into the same
# row, so a grid-only merge must sync too.
#
# Behavior per changed slug (deduped across both watched files):
#   - Row exists in customer_configs  → re-project via the canonical script
#     (validates BOTH files, provenance-guarded, idempotent SQL) and apply
#     --remote. The upsert deliberately never updates entity_id, so a routine
#     CI sync can never repoint a config to a different client (cross-tenant
#     guard, see customer-config-projection.ts).
#   - No row (never seeded)           → WARN and skip. The FIRST projection
#     of a new customer stays a Captain-gated manual step (it binds the
#     config to an owning entity).
#
# Inputs (env): BEFORE_SHA, AFTER_SHA — the push range from the workflow.
# Wrangler auth: CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID env vars.
set -euo pipefail

# The one rule for which operator/customers/ dirs are seats (scaffolds and
# decommissioned tombstones are not): scripts/lib/seat-dir.sh.
# shellcheck source=lib/seat-dir.sh
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/seat-dir.sh"

DB=ss-console-db
BEFORE="${BEFORE_SHA:?BEFORE_SHA required}"
AFTER="${AFTER_SHA:?AFTER_SHA required}"

# A force-push or branch-create event has a zero BEFORE sha; fall back to the
# single pushed commit so we never diff against nothing.
if [[ "$BEFORE" =~ ^0+$ ]]; then
  BEFORE="${AFTER}~1"
fi

# Watch BOTH the config and its sibling routine grid; a change to either
# re-projects the slug. Dedupe to unique slugs so a commit that touched both
# files for one seat projects exactly once.
mapfile -t changed < <(git diff --name-only "$BEFORE" "$AFTER" -- \
  'operator/customers/*/customer.yaml' 'operator/customers/*/routine-grid.yaml' || true)

# Rows never projected with schedule_changed_at (migration 0130) re-project
# too, whatever the push range touched: until they do, work-health grades their
# schedule against synced_at, which every unrelated publish moves. Self-healing,
# so a row seeded by hand without the column converges on the next deploy.
mapfile -t unstamped < <(npx wrangler d1 execute "$DB" --remote --json \
  --command "SELECT customer_slug FROM customer_configs WHERE schedule_changed_at IS NULL" |
  node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const r=JSON.parse(s)[0].results;process.stdout.write(r.map(x=>x.customer_slug).join('\n'))})")
for slug in "${unstamped[@]}"; do
  [[ -n "$slug" ]] && changed+=("operator/customers/${slug}/customer.yaml")
done

if [[ ${#changed[@]} -eq 0 ]]; then
  echo "No customer.yaml / routine-grid.yaml changes in ${BEFORE}..${AFTER} — nothing to sync."
  exit 0
fi

declare -A seen_slug=()
slugs=()
for path in "${changed[@]}"; do
  slug=$(seat_dir_of_path "$path") || continue
  [[ -n "${seen_slug[$slug]:-}" ]] && continue
  seen_slug[$slug]=1
  slugs+=("$slug")
done

fail=0
for slug in "${slugs[@]}"; do
  cfg="operator/customers/${slug}/customer.yaml"
  # customer.yaml gone → the customer dir was retired (or a grid-only path for a
  # dir with no config). Retirement is a manual, Captain-gated operation, never
  # an automatic row drop.
  if [[ ! -f "$cfg" ]]; then
    echo "::warning::$cfg is absent; customer retirement is manual (no automatic projection drop)."
    continue
  fi

  # Scaffolds and decommissioned tombstones are not live customers. Checked
  # BEFORE the slug guard below, so a tombstone's dotted name reads as "skip
  # this", not as a suspicious slug that fails the job.
  if skip=$(seat_dir_skip_reason "$slug"); then
    echo "Skipping ${skip} dir: $slug"
    continue
  fi
  # The slug becomes part of a SQL literal below; constrain it hard. Canonical
  # pattern (#2285): lowercase alphanumerics + dashes, 2-40 chars, no
  # leading/trailing dash — the same shape
  # operator/adapter/namespace_assertion.py demands at seat boot. This guard
  # projects into D1, so it must never be the loose one.
  if [[ ! "$slug" =~ ^[a-z0-9][a-z0-9-]{0,38}[a-z0-9]$ ]]; then
    echo "::error::Refusing to sync suspicious slug: $slug"
    fail=1
    continue
  fi

  echo "── Syncing $slug ──"
  entity_id=$(npx wrangler d1 execute "$DB" --remote --json \
    --command "SELECT entity_id FROM customer_configs WHERE customer_slug = '$slug'" |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const r=JSON.parse(s)[0].results;process.stdout.write(r.length?r[0].entity_id:'')})")

  if [[ -z "$entity_id" ]]; then
    echo "::warning::$slug has no customer_configs row — first projection is Captain-gated; run scripts/project-customer-config.ts manually."
    continue
  fi

  out="scripts/.generated/ci-project-${slug}.sql"
  npx tsx scripts/project-customer-config.ts "$slug" "$entity_id" \
    --actor="system:deploy.yml/sync-customer-configs" --synced-by=ci --out="$out"
  npx wrangler d1 execute "$DB" --remote --file="$out"

  # Prove the write landed: the live row's git_sha must equal the SHA the
  # projection STAMPED — always customer.yaml's SHA, even for a grid-only change
  # (see resolveGitSha in project-customer-config.ts). Read it back out of the
  # generated SQL header rather than recomputing per-file, so this stays correct
  # regardless of which of the two files triggered the sync.
  want=$(grep -oE 'git_sha [0-9a-f]{40}' "$out" | head -1 | awk '{print $2}')
  if [[ -z "$want" ]]; then
    echo "::error::$slug: could not read stamped git_sha from $out"
    fail=1
    continue
  fi
  got=$(npx wrangler d1 execute "$DB" --remote --json \
    --command "SELECT git_sha FROM customer_configs WHERE customer_slug = '$slug'" |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const r=JSON.parse(s)[0].results;process.stdout.write(r.length?r[0].git_sha:'')})")
  if [[ "$got" == "$want" ]]; then
    echo "$slug projected: git_sha=$got"
  else
    echo "::error::$slug projection did not land (want $want, live row has $got)"
    fail=1
  fi
done

exit "$fail"

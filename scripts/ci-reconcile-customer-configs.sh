#!/usr/bin/env bash
# Reconciler: make every `customer_configs` D1 row converge on the customer.yaml
# that main actually carries (ss #2292).
#
# WHY. The sibling ci-sync-customer-configs.sh is a TRIGGER, not a reconciler: it
# diffs one push range (`github.event.before..github.sha`) and syncs whatever
# changed inside it. A range that is never processed — a failed deploy job, a
# skipped run, a force-push, a rewritten history — is never revisited, because no
# LATER push's range contains it. That is not hypothetical: on 2026-08-11 the
# smd-staging row still carried `format_spec: none` while main (008a5731, 07-31)
# and the live R2 seat config both said `expected`. Eleven days, no alarm, and
# the portal showed a posture the seat did not enforce — precisely the "a broken
# sync read as a deliberate choice" failure the #2080 commit message warned of.
#
# So this job asks a question the trigger cannot: for EVERY row that exists, is
# its stamped provenance the commit main names for that file right now? Same
# shape as the overlay#185 profile-home reconciler — converge on authored state,
# rather than sweep once and hope the trigger holds.
#
# Note `updated_at` is NOT a freshness signal here: the sync never touches it
# (the smd row shows 06-10 against a 07-30 git_sha). Provenance is the only
# honest comparison, and it is compared against git, never against a clock.
#
# TWO SIGNALS, deliberately distinguished:
#
#   DRIFT     — row.git_sha != `git log -1 -- <slug>/customer.yaml`. The row is
#               behind (or ahead of) authored main. Re-projected, then verified by
#               reading the stamped sha back. Reported even after self-healing: a
#               projection silently wrong for eleven days is worth a human's
#               attention regardless of who eventually fixed it.
#   ORPHANED  — row.git_sha is not an ancestor of HEAD, or is not an object in
#               this clone at all. Main's history was rewritten out from under the
#               provenance stamp. This is the signal nobody saw for eleven days,
#               so it ALERTS rather than logs: the workflow turns the non-zero
#               exit into an issue.
#
# WHAT IT WILL NOT DO. It never creates a row and never drops one:
#   - Never creates. The slug list comes FROM D1, so an unseeded customer is not
#     in the loop at all. The FIRST projection binds a config to an owning entity
#     and stays Captain-gated (ci-sync-customer-configs.sh:85-88).
#   - Never drops. A row whose customer.yaml is gone is WARNED about, never
#     deleted. Retirement is manual (ci-sync-customer-configs.sh:63-66).
#   - Never repoints. entity_id is read off the row being reconciled, and the
#     upsert excludes it from its update set (customer-config-projection.ts:202).
#
# KNOWN BLIND SPOT (follow-on). The comparison is provenance, not content, and
# the row's git_sha is ALWAYS customer.yaml's sha even when only the sibling
# routine-grid.yaml changed (resolveGitSha, project-customer-config.ts:105-124).
# A grid-only change that failed to sync therefore leaves a matching git_sha and
# is invisible here. Closing it needs a column-level content comparison, not a
# tighter sha rule: a pure-git grid check would fire on every SUCCESSFUL
# grid-only sync, and a control that cries wolf gets muted inside a week. Neither
# live grid is in that state today (both last changed before their customer.yaml).
#
# bash 3.2 compatible on purpose — like ci-publish-customer-configs.sh, this is
# exercised by a test suite that may run on a macOS operator box. No mapfile, no
# associative arrays, and no bare `"${arr[@]}"` under `set -u`; findings are
# accumulated as newline-delimited strings instead.
#
# Inputs (env):
#   RECONCILE_DRY_RUN=1   detect and report; never write to D1.
#   DB_NAME               override the D1 database name (default ss-console-db).
# Wrangler auth: CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID.
#
# The reconcile ref is HEAD, deliberately not configurable: project-customer-config.ts
# resolves the sha from the working tree, so a ref that differed from the checkout
# would compare against one commit and project another.
#
# Exit codes:
#   0  converged — nothing drifted, nothing orphaned
#   1  cannot evaluate, or a re-projection failed to land (the control itself is
#      broken; a failed workflow run is the right noise for that)
#   2  findings — drift and/or orphaned provenance (the workflow opens an issue)
set -euo pipefail

DB="${DB_NAME:-ss-console-db}"
DRY_RUN="${RECONCILE_DRY_RUN:-}"

# A shallow clone cannot answer either question: `git log -1 -- <path>` reports
# the newest commit IN THE SHALLOW SLICE, and `merge-base --is-ancestor` would
# call every older-but-valid sha an orphan. Refuse rather than emit confident
# garbage — "could not evaluate" must not read as "found nothing".
if [[ "$(git rev-parse --is-shallow-repository 2>/dev/null || echo unknown)" != "false" ]]; then
  echo "::error::refusing to reconcile from a shallow clone (the checkout needs fetch-depth: 0)"
  exit 1
fi

head_sha=$(git rev-parse HEAD 2>/dev/null || true)
if [[ -z "$head_sha" ]]; then
  echo "::error::could not resolve HEAD; nothing to reconcile against"
  exit 1
fi
echo "Reconciling customer_configs against HEAD ${head_sha}"
if [[ -n "$DRY_RUN" ]]; then
  echo "DRY RUN: findings will be reported, nothing will be written."
fi

# ---------- read every row ----------
# Rows, not files: the loop's universe is what D1 already holds, which is what
# makes "never creates a row" structural rather than a rule to remember.
if ! rows_json=$(npx wrangler d1 execute "$DB" --remote --json \
  --command "SELECT customer_slug, entity_id, git_sha FROM customer_configs ORDER BY customer_slug" 2>/dev/null); then
  echo "::error::could not read customer_configs from ${DB} (wrangler failed)"
  exit 1
fi

if ! rows=$(printf '%s' "$rows_json" | node -e "
let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{
  let r;
  try { r = JSON.parse(s)[0].results } catch { process.exit(7) }
  if (!Array.isArray(r)) process.exit(7)
  process.stdout.write(r.map(x=>[x.customer_slug,x.entity_id,x.git_sha].join('\t')).join('\n'))
})"); then
  echo "::error::could not parse the customer_configs read from ${DB}"
  exit 1
fi

if [[ -z "$rows" ]]; then
  echo "::error::customer_configs came back empty; that is not a converged state, it is an unreadable one"
  exit 1
fi

# ---------- reconcile ----------
hard_fail=0
checked=0
n_drift=0
n_orphan=0
n_noyaml=0
findings=""

while IFS=$'\t' read -r slug entity_id row_sha; do
  [[ -z "$slug" ]] && continue

  # A template dir is not a live customer and must never be projected. Checked
  # BEFORE the charset guard: the leading underscore is not in the slug charset,
  # so the order decides whether `_template` reads as "skip this" or as "someone
  # put a hostile string in the database".
  if [[ "$slug" == _* ]]; then
    echo "::warning::${slug} looks like a template dir but holds a customer_configs row; skipping."
    continue
  fi
  if [[ "$slug" == *.decommissioned.* ]]; then
    echo "::warning::${slug} is a decommissioned seat dir but holds a customer_configs row; skipping."
    continue
  fi
  # The slug arrives from the database and becomes both a filesystem path and a
  # SQL literal downstream. Constrain it here, on the way in.
  if [[ ! "$slug" =~ ^[a-z0-9-]+$ ]]; then
    echo "::error::refusing to reconcile suspicious slug from customer_configs: ${slug}"
    hard_fail=1
    continue
  fi

  checked=$((checked + 1))
  cfg="operator/customers/${slug}/customer.yaml"

  if [[ ! -f "$cfg" ]]; then
    echo "::warning::${slug} has a row but no ${cfg}; customer retirement is manual (no automatic row drop)."
    n_noyaml=$((n_noyaml + 1))
    findings="${findings}  NO-YAML  ${slug}
"
    continue
  fi

  want=$(git log -1 --format=%H -- "$cfg" 2>/dev/null | tr -d '\n' || true)
  if [[ ! "$want" =~ ^[0-9a-f]{40}$ ]]; then
    echo "::error::${slug}: could not resolve a commit for ${cfg} on HEAD"
    hard_fail=1
    continue
  fi

  # ORPHAN CHECK, before the sha comparison and independent of it: a rewritten
  # history breaks provenance even when the content happens to be current, and
  # that broken link is exactly what made this class of staleness invisible. A
  # sha that is not an object in this clone counts as orphaned too — the three
  # known cases (249d7a93, 812873d6, 895dad9f) resolve only via the GitHub API.
  is_orphan=0
  if [[ ! "$row_sha" =~ ^[0-9a-f]{40}$ ]]; then
    is_orphan=1
  elif ! git merge-base --is-ancestor "$row_sha" HEAD 2>/dev/null; then
    is_orphan=1
  fi
  if [[ "$is_orphan" -eq 1 ]]; then
    echo "::error::${slug}: git_sha ${row_sha:-<empty>} is not an ancestor of HEAD — main no longer contains the commit this row claims."
    n_orphan=$((n_orphan + 1))
    findings="${findings}  ORPHAN   ${slug} (${row_sha:-<empty>})
"
  fi

  if [[ "$row_sha" == "$want" ]]; then
    echo "${slug}: in sync (git_sha ${row_sha})"
    continue
  fi

  echo "── ${slug} DRIFTED: row has ${row_sha:-<empty>}, HEAD names ${want} for ${cfg} ──"
  n_drift=$((n_drift + 1))
  findings="${findings}  DRIFT    ${slug}: ${row_sha:-<empty>} -> ${want}
"

  if [[ -n "$DRY_RUN" ]]; then
    echo "  dry run: would re-project ${slug}"
    continue
  fi

  out="scripts/.generated/reconcile-project-${slug}.sql"
  if ! npx tsx scripts/project-customer-config.ts "$slug" "$entity_id" \
    --actor="system:reconcile-customer-configs" --synced-by=ci --out="$out"; then
    echo "::error::${slug}: projection failed; row left as-is"
    hard_fail=1
    continue
  fi

  # Same landback proof the push-range sync uses: read the sha the projection
  # STAMPED out of the generated SQL rather than recomputing it, so this stays
  # correct however resolveGitSha chooses the anchor.
  stamped=$(grep -oE 'git_sha [0-9a-f]{40}' "$out" | head -1 | awk '{print $2}' || true)
  if [[ -z "$stamped" ]]; then
    echo "::error::${slug}: could not read the stamped git_sha from ${out}"
    hard_fail=1
    continue
  fi

  if ! npx wrangler d1 execute "$DB" --remote --file="$out"; then
    echo "::error::${slug}: applying the re-projection failed"
    hard_fail=1
    continue
  fi

  got=$(npx wrangler d1 execute "$DB" --remote --json \
    --command "SELECT git_sha FROM customer_configs WHERE customer_slug = '$slug'" 2>/dev/null |
    node -e "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{try{const r=JSON.parse(s)[0].results;process.stdout.write(r.length?r[0].git_sha:'')}catch{process.stdout.write('')}})" || true)
  if [[ "$got" == "$stamped" ]]; then
    echo "  ${slug} re-projected: git_sha=${got}"
  else
    echo "::error::${slug}: re-projection did not land (want ${stamped}, live row has ${got:-<empty>})"
    hard_fail=1
  fi
done <<<"$rows"

# ---------- report ----------
echo
echo "── reconcile summary ──"
echo "rows checked: ${checked}"
echo "drifted:      ${n_drift}"
echo "orphaned:     ${n_orphan}"
echo "unauthored:   ${n_noyaml}"
if [[ -n "$findings" ]]; then
  printf '%s' "$findings"
fi

if [[ "$hard_fail" -ne 0 ]]; then
  echo "reconcile FAILED: the control could not complete."
  exit 1
fi
if [[ "$n_drift" -gt 0 || "$n_orphan" -gt 0 ]]; then
  if [[ -n "$DRY_RUN" ]]; then
    echo "reconcile found drift (dry run — nothing written)."
  else
    echo "reconcile converged the rows above; reporting because the silent divergence is itself the defect."
  fi
  exit 2
fi
echo "reconcile clean: every row's provenance is the commit HEAD names."
exit 0

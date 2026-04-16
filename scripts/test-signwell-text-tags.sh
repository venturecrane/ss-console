#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# End-to-end test harness for the SignWell text-tag redesign (PR #394).
#
# Seeds a fresh DRAFT quote on the Cactus Creative Studio sample entity with
# authored schedule + deliverables (so the send-gating from #377 does not
# block the flow), then prints the admin URL and signer for the operator to
# click Generate PDF -> Send for Signature.
#
# When Send fires, SignWell emails a signing link to Sofia Chen at her
# address on record, and we can visually verify:
#   1. The unsigned PDF has no visible {{s:1}} / {{d:1}} tag residue
#   2. SignWell places the signature + date fields over the tag locations
#   3. The signed PDF has the signature/date rendered on top of the tags,
#      with no visible drift or duplicate "Date: _______________" artifact.
#
# Usage:
#   scripts/test-signwell-text-tags.sh
#
# Requires:
#   - npx + wrangler (already in devDependencies)
#   - Cloudflare credentials in the local env (for --remote D1 access)
#
# Safe to re-run: each invocation creates a NEW draft quote. The existing
# accepted quote on Cactus is untouched.
# ---------------------------------------------------------------------------

set -euo pipefail

# Fixed identifiers from production D1 (checked 2026-04-15):
ORG_ID="01JQFK0000SMDSERVICES000"
ENTITY_ID="fe4fbf18-ab3a-43d9-ae11-1d7c8c6d54ae"        # Cactus Creative Studio
ENTITY_NAME="Cactus Creative Studio"
ASSESSMENT_ID="5d84a836-0a81-4b85-9941-2c44339184b6"    # Reuse Cactus assessment
CONTACT_ID="c3-cactus-creative-test"                    # Sofia Chen
CONTACT_NAME="Sofia Chen"
CONTACT_EMAIL="smdurgan@icloud.com"

# Generate a fresh quote ID so re-runs don't collide.
QUOTE_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Authored client-facing content (#377 compliance — no fabricated defaults).
# ---------------------------------------------------------------------------

LINE_ITEMS='[{"problem":"Process Design","description":"Map current workflows, document SOPs, remove owner bottleneck","estimated_hours":20},{"problem":"Tool Systems","description":"Evaluate tools, select and configure project management and CRM","estimated_hours":15},{"problem":"Data Visibility","description":"Build financial dashboard, set up revenue and profitability reporting","estimated_hours":10}]'

SCHEDULE='[{"label":"Week 1 — Discovery","body":"We shadow your current operations, interview the team, and agree on the priority workflows to redesign."},{"label":"Weeks 2-3 — Build","body":"We document SOPs, configure the selected tools, and build the financial dashboard alongside your team."},{"label":"Week 4 — Handoff","body":"Hands-on training session, written handoff document, and a two-week stabilization window for questions and adjustments."}]'

DELIVERABLES='[{"title":"SOP Library","body":"Written standard operating procedures for the three priority workflows identified during discovery."},{"title":"Configured Toolset","body":"Project management and CRM configured with your data, integrated where applicable, with team access provisioned."},{"title":"Financial Dashboard","body":"Revenue and profitability views wired to your existing financial data, with agreed-upon weekly check-in metrics."},{"title":"Handoff Document","body":"Single written artifact capturing what we built, how to operate it, and how to extend it after the engagement ends."}]'

ENGAGEMENT_OVERVIEW="Operations design and implementation engagement scoped during the assessment conversation. We focus on removing the owner-bottleneck pattern in Cactus Creative Studio's delivery operations by documenting the three highest-leverage workflows, configuring a project management and CRM system that fits your team's actual working rhythm, and standing up financial reporting that makes profitability visible per project."

# ---------------------------------------------------------------------------
# Seed the draft quote.
# ---------------------------------------------------------------------------

# Build the SQL file. We write it to disk instead of passing via --command so
# that the embedded JSON strings don't trigger shell-quoting gymnastics.
SQL_FILE="$(mktemp -t seed-signwell-test.XXXXXX.sql)"
trap 'rm -f "$SQL_FILE"' EXIT

# Escape single quotes inside the authored content by doubling them (SQLite
# string-literal escaping), so the SQL remains valid regardless of content.
escape_sql() { printf %s "$1" | sed "s/'/''/g"; }

LINE_ITEMS_SQL="$(escape_sql "$LINE_ITEMS")"
SCHEDULE_SQL="$(escape_sql "$SCHEDULE")"
DELIVERABLES_SQL="$(escape_sql "$DELIVERABLES")"
OVERVIEW_SQL="$(escape_sql "$ENGAGEMENT_OVERVIEW")"

cat >"$SQL_FILE" <<EOF
INSERT INTO quotes (
  id, org_id, entity_id, assessment_id, version, parent_quote_id,
  line_items, total_hours, rate, total_price, deposit_pct, deposit_amount,
  status, schedule, deliverables, engagement_overview, milestone_label,
  created_at, updated_at
) VALUES (
  '${QUOTE_ID}',
  '${ORG_ID}',
  '${ENTITY_ID}',
  '${ASSESSMENT_ID}',
  1,
  NULL,
  '${LINE_ITEMS_SQL}',
  45,
  175,
  7875,
  0.5,
  3937.5,
  'draft',
  '${SCHEDULE_SQL}',
  '${DELIVERABLES_SQL}',
  '${OVERVIEW_SQL}',
  NULL,
  '${NOW}',
  '${NOW}'
);
EOF

echo "Seeding draft quote ${QUOTE_ID} for ${ENTITY_NAME} (${ENTITY_ID})..."
npx wrangler d1 execute ss-console-db --remote --file "$SQL_FILE" >/dev/null

cat <<EOF

Seed complete.

  Quote ID:    ${QUOTE_ID}
  Entity:      ${ENTITY_NAME} (${ENTITY_ID})
  Status:      draft
  Signer:      ${CONTACT_NAME} <${CONTACT_EMAIL}>  (contact ${CONTACT_ID})

Next steps (admin UI):

  1. Open: https://admin.smd.services/admin/entities/${ENTITY_ID}/quotes/${QUOTE_ID}
  2. Click Generate SOW PDF
  3. Click Send for Signature
  4. Confirm the signer is Sofia Chen
  5. Watch ${CONTACT_EMAIL} for the SignWell review email

What to verify:

  - Unsigned PDF rendered by SignWell shows NO visible {{s:1}} / {{d:1}} text tags
  - Signature and date fields are placed over the tag locations (left-aligned
    inside the CLIENT ACCEPTANCE block)
  - After signing, the final PDF shows the signature and date stamps cleanly,
    with no "Owner" label overlapping the date and no duplicate
    "Date: _______________" placeholder (the drift bug from the screenshot
    in PR #394)

EOF

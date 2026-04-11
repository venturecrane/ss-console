## Lifecycle UI Smoke Test — Browser Agent

You are testing the SMD Services admin console at **https://smd.services** to verify the Phase 2a lifecycle pages render correctly and basic interactions work. Use the Chrome extension MCP tools (`mcp__claude-in-chrome__*`) for all browser interaction.

### Prerequisites
- Navigate to `https://smd.services/admin` and log in if prompted
- Take a screenshot after each major step to verify results
- Report every failure with: what you expected, what you saw, and a screenshot

### Test 1: Operator Dashboard (`/admin`)

1. Navigate to `/admin`
2. Verify the page has 5 distinct cards:
   - **Today's Work** — signals count, assessments today, overdue follow-ups
   - **Pipeline** — funnel bar + stage counts (Signals, Prospects, Assessing, Proposing, Engaged, Delivered)
   - **Revenue** — Invoiced, Paid, Outstanding amounts
   - **Follow-up Health** — on-time rate, sent on time, sent late, missed
   - **System Health** — Google Calendar, Stripe, Follow-up processor, Lead-gen Workers status dots
3. Verify the nav bar has links to Entities, Follow-ups, Analytics
4. Click "Entities" link — confirm it navigates to `/admin/entities`
5. Navigate back to `/admin`

### Test 2: Entity List → Assessment Detail

1. From `/admin/entities`, find any entity in the `assessing` stage (or any entity with a scheduled assessment)
2. If no assessing entities exist, note this and skip to Test 3
3. Click into the entity detail page
4. Look for an assessment link or navigate directly to `/admin/assessments/{id}` for any assessment
5. Verify the assessment page has:
   - Entity info header (name, stage, tier, vertical)
   - Schedule sidecar (time, timezone, meeting link area)
   - Context timeline section
   - Live notes textarea
   - Collapsible "Complete Assessment" form with:
     - 6 problem checkboxes (Owner bottleneck, Lead leakage, etc.)
     - Disqualifier checkbox + reason
     - Duration input
     - Notes textarea
     - "Complete & Draft Proposal" button

### Test 3: Quote Builder

1. Navigate to an entity detail page that has quotes (or navigate to `/admin/entities` and find one in `proposing` stage)
2. Click into a quote to reach the quote builder page
3. Verify the quote builder has:
   - Header with status badge, dates, rate
   - Line item table (problem, description, estimated hours)
   - Add/remove line item controls
   - Auto-calculated totals (total hours, project price)
   - Payment structure section (deposit %)
   - Action buttons: Save Draft, Generate SOW PDF, Send via SignWell
4. If a draft quote exists, try adding a line item and verify the totals recalculate
5. Take a screenshot of the complete quote builder

### Test 4: Engagement Detail

1. Find an entity in `engaged` stage, or navigate directly to `/admin/engagements/{id}` if you can find an engagement
2. If no engagements exist, note this and skip
3. Verify the engagement page has:
   - Header with entity name, status badge, dates
   - Quote summary (hours, rate, price)
   - Milestone list with status badges and action buttons (Start, Mark Complete)
   - Payment trigger toggles
   - Deliverables section with upload area
   - Context timeline
   - Status transition buttons (Mark Handoff, Cancel)

### Test 5: Navigation Integrity

1. From the dashboard, verify each nav link works:
   - `/admin/entities` — entity list loads
   - `/admin/follow-ups` — follow-ups page loads
   - `/admin/analytics` — analytics page loads
2. Verify the sign-out button is present
3. Check that entity detail pages link to their assessments, quotes, and engagements

### Test 6: Empty State Handling

1. If any of the above pages had no data (no assessments, no quotes, no engagements), verify they show reasonable empty states rather than errors or blank pages
2. The dashboard should show zeros gracefully, not crash

### Reporting

After all tests, provide a summary table:

| Test | Status | Notes |
|------|--------|-------|
| Dashboard cards | PASS/FAIL | ... |
| Assessment detail | PASS/FAIL/SKIP | ... |
| Quote builder | PASS/FAIL/SKIP | ... |
| Engagement detail | PASS/FAIL/SKIP | ... |
| Navigation | PASS/FAIL | ... |
| Empty states | PASS/FAIL | ... |

Flag any visual issues (broken layouts, missing styles, overlapping elements) even if the functionality works.

# Test cases - status-report-assembler synthetic fixtures

Ten synthetic client scenarios covering the variation space. Fixtures live at `operator/fixtures/marketing-agency/status-reports/<client-slug>/` and include PM-tool data, analytics data, paid-media data, and prior-thread email samples for voice-matching.

The agent should be run against these as a regression check. Each test specifies the inputs + the expected draft characteristics. Captain or a reviewer grades each draft as autonomous / draft / fails per the rubric.

---

## #1 - Standard paid-media client, healthy week

```yaml
client: "Halo Marketing"
service_lines: [paid_media, analytics]
report_window: 7 days
inputs:
  pm_shipped: 4 items (paid creative + analytics dashboard)
  analytics: sessions +18% WoW, conversions +51% MoM, CTR steady
  paid: $4,200 spend, $14 CPL (down from $19 prior week), top ad: Save-15 creative
  blockers: 1 (Q3 budget pending since 5/8)
  next_week_plan: 3 confirmed items
prior_thread_voice: "business-casual, bulleted, first-name salutation"
```

Expected draft characteristics:

- Voice: business-casual, bulleted, "Hi Jane,"
- Shipped: 4 bullets grouped by service line
- Results: 4 metrics with WoW deltas + sources
- Blocker: 1 entry naming Q3 budget + since-date + next step
- Next week: 3 items, all (confirmed)
- One thing: confirm Q3 budget by Friday

Expected verdict: **autonomous** (no calibration concerns)

---

## #2 - Result drop with explanation (holiday)

```yaml
client: 'India Networks'
report_window: 7 days
inputs:
  analytics: sessions -22% WoW (week included Memorial Day Monday)
  pm_shipped: 3 items
  blockers: 0
  next_week_plan: 4 items, all confirmed
prior_thread_voice: "formal, paragraph density, 'Dear' salutation"
```

Expected draft characteristics:

- Voice: formal, paragraph density, "Dear Mark,"
- Results section includes context line: "Sessions: 8,400 (-22% WoW per GA4) - week included Memorial Day, holiday-normalized drop ~5%."
- Surfaces the drop honestly with explanation; doesn't hide it

Expected verdict: **autonomous**

---

## #3 - Anomaly: result drop without explanation

```yaml
client: 'Juliet Studios'
report_window: 7 days
inputs:
  analytics: conversions -41% WoW (no obvious cause)
  pm_shipped: 2 items
  blockers: 0
  next_week_plan: 3 items
```

Expected:

- Surfaces the drop in Results
- Flags anomaly in Slack alert: "Juliet - conversions -41% WoW, no obvious cause"
- Draft includes `> NOTE:` line above Results: "Investigate conversion drop before sending - check pixel + funnel"
- Verdict: **draft_for_review** (anomaly requires owner judgment before send)

---

## #4 - Engagement paused per SOW

```yaml
client: 'Kilo Corp'
sow_status: paused
inputs:
  pm_shipped: 0 items (nothing to ship while paused)
```

Expected:

- NO draft produced
- Slack alert one-liner: "Kilo Corp engagement paused per SOW; no draft."

---

## #5 - New client, no prior thread history

```yaml
client: 'Lima Group'
report_window: 7 days
client_signed_date: 5 days ago
inputs:
  pm_shipped: 1 item (onboarding kickoff)
  analytics: limited (just started tracking)
  blockers: 0
  next_week_plan: 3 onboarding items
prior_thread_voice: NULL (no prior reports)
```

Expected:

- Voice: agency-default (per customer.yaml)
- Draft notes "First report - voice calibration pending. Edit freely; agent will learn from this version."
- Slack alert: "Lima - first report, voice match flagged for owner review"
- Verdict: **draft_for_review** (always for first-report clients)

---

## #6 - Multiple-stakeholder client

```yaml
client: 'Mike Industries'
primary_contact: 'Sarah Tan (CMO)'
cc_list: ['Bob Lee (CFO)', 'Alice Chen (CEO)']
inputs: standard healthy week, 3 shipped, 4 metrics, 1 blocker, 3 next-week
```

Expected:

- Draft addressed to Sarah Tan only
- Slack alert lists the CC list for the owner to add when sending
- Verdict: **autonomous** (assuming healthy week)

---

## #7 - Long blocker (≥ 7 business days)

```yaml
client: "November Co"
inputs:
  blocker_a: "Brand guidelines pending since 4/30" (currently 5/19 - 14 business days)
  pm_shipped: 2 items
  analytics: steady
```

Expected:

- Blocker surfaced as anomaly in Slack alert: "November - brand guidelines pending 14 business days, owner should consider escalation outside the report"
- Draft blocker section includes this item normally
- Verdict: **draft_for_review** (long blocker may need owner-side handling)

---

## #8 - Shipped count = 0 (anomaly)

```yaml
client: 'Oscar Studio'
sow_hours_monthly: 40
inputs:
  pm_shipped: 0 items (nothing closed in window)
  analytics: steady
  blockers: 0
  next_week_plan: present (work IS planned)
```

Expected:

- Slack alert flag: "Oscar - 0 items shipped this week despite active retainer; capacity or scope issue?"
- Draft shipped section is empty; owner decides whether to send (likely no - they need to address the underlying issue first)
- Verdict: **draft_for_review**

---

## #9 - Heavy engagement, 600+ word draft

```yaml
client: 'Papa Holdings'
sow_hours_monthly: 80 # large retainer
service_lines: [paid_media, organic, content, analytics, CRO]
inputs:
  pm_shipped: 12 items across 4 service lines
  analytics: 8 metrics with WoW deltas
  paid: $14,000 spend, multi-platform
  blockers: 2 client-side
  next_week_plan: 7 items
prior_thread_voice: 'business-casual, dense paragraphs'
```

Expected:

- Draft length 500-700 words (matches engagement size)
- Shipped section grouped by service line, not flat list
- Results section gets all 8 metrics
- Voice matches prior thread density
- Verdict: **autonomous**

---

## #10 - Mid-week stakeholder change

```yaml
client: "Quebec Corp"
inputs:
  primary_contact_changed_during_week: true
  former_contact: "John Park"
  new_contact: "Maria Singh" (from intro email mid-week)
  draft_addressed_to: ? (which contact?)
```

Expected:

- Surfaces the contact change in Slack alert: "Quebec - primary contact changed mid-week (John → Maria); draft addressed to Maria but verify"
- Draft uses Maria as primary contact, with a brief opening line: "Maria - welcome aboard! Here's the week-of recap..."
- Verdict: **draft_for_review** (relationship transition needs human eye)

---

## How to use this file

Run the agent against the synthetic fixtures in `operator/fixtures/marketing-agency/status-reports/` (10 client scenarios mirroring the tests above). Diff agent output against expected characteristics. Mismatches in:

- Section structure → bug in output-format.md or prompt
- Voice → bug in voice.md or prior-thread retrieval
- Inclusion/exclusion → bug in categorization-rubric.md
- Anomaly surfacing → bug in the anomaly thresholds

The fixtures are the contract. Iterate the prompt + references until 8/10 pass at the expected verdict.

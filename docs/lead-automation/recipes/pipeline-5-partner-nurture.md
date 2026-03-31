# Make.com Recipe: Pipeline 5 — Referral Partner Nurture

**Purpose:** Step-by-step guide to build a Make.com scenario that drafts personalized check-in emails for bookkeeper/CPA referral partners, creates Gmail drafts for human review, and tracks check-in cadence in Google Sheets.

**Prerequisites:**

- Google Sheets: "SMD Lead Generation" spreadsheet with "Referral Partners" tab populated (see google-sheets-schema.md, Sheet 5)
- Gmail connection in Make.com (for creating drafts)
- Anthropic API key

**No external API accounts needed.** This pipeline uses only Gmail, Google Sheets, and Anthropic — all of which are already configured for Pipelines 1-4.

---

## Scenario: Weekly Partner Check-ins

### Scenario Overview

```
Schedule (every Friday 8am)
  -> Google Sheets: search rows in "Referral Partners" tab
    where Next Check-in Date <= today
    AND Relationship Stage != "Dormant"
  -> Filter: email is not empty
  -> Iterator: each partner due for check-in
    -> Anthropic: draft check-in email with Claude (partner-nurture-prompt)
    -> Parse JSON
    -> Gmail: create draft (to: partner email, subject + body from Claude output)
    -> Google Sheets: update row (Last Contact Date, Next Check-in Date)
  -> Gmail: send summary email to self ("{count} partner check-in drafts ready")
```

**Total modules per partner:** ~5
**Expected weekly volume:** 2-8 partners due for check-in per week (depends on list size and check-in cadence)
**Operations per run:** ~30-80

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P5 Partner Nurture`
3. Set the schedule: **Every Friday at 8:00 AM** (MST/Arizona time — Arizona does not observe DST)

**Why Friday:** Drafts are created Friday morning. The human reviews them over the weekend or first thing Monday. Emails are sent Tuesday-Thursday (optimal B2B send days per the prompt). This gives time for review without making partners wait.

#### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once weekly on Friday at 8:00 AM

#### Step 3: Module 1 — Google Sheets (Search Partners Due for Check-in)

**Module: Google Sheets -> Search Rows**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Referral Partners"
- Filter:

| Condition | Column                | Operator                 | Value                               |
| --------- | --------------------- | ------------------------ | ----------------------------------- |
| 1         | K: Next Check-in Date | is less than or equal to | `{{formatDate(now; "YYYY-MM-DD")}}` |
| 2         | I: Relationship Stage | does not equal           | `Dormant`                           |

This returns all partners whose Next Check-in Date has arrived (or passed) and who are not in the Dormant stage.

**Note:** If the Google Sheets Search Rows module does not support complex filters natively, use the **Get All Rows** variant and add Make.com filters after the module to apply both conditions.

#### Step 4: Filter — Email Exists

**Add a filter after the Google Sheets module:**

- Condition: `{{E: Email}}` is not empty
- Label: "Has email address"

Partners without email addresses cannot receive check-in emails. Skip them. These partners may need manual outreach (phone call, LinkedIn message) — a separate notification could flag these if desired.

#### Step 5: Module 2 — Iterator (Each Partner)

**Module: Flow Control -> Iterator**

- Source array: The filtered rows from Google Sheets
- Each bundle represents one partner due for a check-in

#### Step 6: Module 3 — Anthropic (Claude Email Draft)

**Module: Anthropic (Claude) -> Create a Message**

| Setting       | Value                                                                                           |
| ------------- | ----------------------------------------------------------------------------------------------- |
| Model         | `claude-sonnet-4-6` (needs good tone and personalization)                                       |
| Max tokens    | `512` (short emails — 3-5 sentences)                                                            |
| System prompt | Paste the full content of `SYSTEM_PROMPT` from `src/lead-gen/prompts/partner-nurture-prompt.ts` |

**User message:**

```
Draft a personalized check-in email for this referral partner.

Firm: {{iterator.A: Firm Name}}
Contact: {{iterator.B: Contact Name}}
Area: {{iterator.C: Area}}
Phone: {{iterator.D: Phone}}
Email: {{iterator.E: Email}}
Website: {{iterator.F: Website}}
Tier: {{iterator.G: Tier}}
Focus Areas: {{iterator.H: Focus Areas}}
Relationship Stage: {{iterator.I: Relationship Stage}}
Last Contact: {{iterator.J: Last Contact Date}}
Referrals Received (them -> us): {{iterator.L: Referrals Received}}
Referrals Sent (us -> them): {{iterator.M: Referrals Sent}}
Notes: {{iterator.N: Notes}}

Produce a single JSON object matching the PartnerEmailDraft schema.
```

**Why claude-sonnet-4-6:** Partner emails require good tone, personalization, and adherence to the strict voice rules (no "I", no dollar amounts, no fixed timeframes). Claude Sonnet handles these nuances better than Haiku and the volume is low enough (2-8 per week) that the cost difference is negligible.

**Error handling:** Add an error route -> Resume (if Claude returns invalid JSON, skip this partner and continue)

#### Step 7: Module 4 — Parse JSON

**Module: JSON -> Parse JSON**

- JSON string: `{{anthropic.content[1].text}}` (Claude's response text)
- This converts the string into a structured object with `subject`, `body`, `tone`, `suggested_send_day`, and `notes` fields

#### Step 8: Module 5 — Gmail (Create Draft)

**Module: Gmail -> Create a Draft**

| Setting      | Value                                                 |
| ------------ | ----------------------------------------------------- |
| To           | `{{iterator.E: Email}}` (the partner's email address) |
| Subject      | `{{json.subject}}` (from Claude's output)             |
| Content      | `{{json.body}}` (from Claude's output)                |
| Content type | Plain text                                            |

**Important:** This creates a DRAFT, not a sent email. The draft appears in Gmail's Drafts folder for human review. The human reads the draft, makes any edits, and sends manually. This is by design — AI drafts should always be reviewed before sending.

**From address:** The draft will be created from the Gmail account connected to Make.com. Ensure this is the business email (e.g., the smd.services domain) and not a personal account.

#### Step 9: Module 6 — Google Sheets (Update Partner Row)

**Module: Google Sheets -> Update a Row**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Referral Partners"
- Row number: `{{iterator.__ROW_NUMBER__}}` (the row number from the Search Rows result — the exact field name depends on the Make.com Google Sheets module version)

**Column updates:**

| Column                | Value                               | Notes                         |
| --------------------- | ----------------------------------- | ----------------------------- |
| J: Last Contact Date  | `{{formatDate(now; "YYYY-MM-DD")}}` | Today's date                  |
| K: Next Check-in Date | See calculation below               | Depends on relationship stage |

**Next Check-in Date calculation:**

The check-in cadence depends on the relationship stage:

| Relationship Stage | Check-in Interval | Make.com Expression                              |
| ------------------ | ----------------- | ------------------------------------------------ |
| Prospect           | Every 14 days     | `{{formatDate(addDays(now; 14); "YYYY-MM-DD")}}` |
| Intro Sent         | Every 14 days     | `{{formatDate(addDays(now; 14); "YYYY-MM-DD")}}` |
| Intro Call Done    | Every 14 days     | `{{formatDate(addDays(now; 14); "YYYY-MM-DD")}}` |
| Active Partner     | Every 21 days     | `{{formatDate(addDays(now; 21); "YYYY-MM-DD")}}` |

In Make.com, use an `if` expression to set the interval based on the relationship stage:

```
{{if(iterator.I: Relationship Stage = "Active Partner"; formatDate(addDays(now; 21); "YYYY-MM-DD"); formatDate(addDays(now; 14); "YYYY-MM-DD"))}}
```

For the initial build, a uniform 14-day cadence for all stages is simpler and reasonable. Adjust as the partner list matures.

#### Step 10: Module 7 — Numeric Aggregator (Count Drafts)

**Module: Tools -> Numeric aggregator**

- Source module: Gmail (Create a Draft)
- Aggregate function: Count
- This counts how many drafts were created during this run

#### Step 11: Module 8 — Gmail (Summary Email to Self)

**Module: Gmail -> Send an Email**

| Setting      | Value                                                        |
| ------------ | ------------------------------------------------------------ |
| To           | Your business email address                                  |
| Subject      | `Partner Nurture — {{numericAggregator.count}} drafts ready` |
| Content      | See below                                                    |
| Content type | Plain text                                                   |

**Email body:**

```
Partner Check-in Drafts Ready

{{numericAggregator.count}} partner check-in email draft(s) created in Gmail.
Review and send by {{formatDate(addDays(now; 4); "dddd")}} ({{formatDate(addDays(now; 4); "YYYY-MM-DD")}}).
```

This sends a single summary email after the entire loop completes — not one email per partner. The suggested send date is 4 days from Friday (Tuesday), which aligns with the optimal B2B send window.

---

## Buttondown Integration (Future)

When the partner list grows to 10+ active partners, add a monthly broadcast scenario for broader content distribution. This is NOT built in the initial version.

### Future Scenario: Monthly Partner Insights Email

```
Schedule (1st of month)
  -> Anthropic: draft monthly insights email
  -> HTTP: POST to Buttondown API /v1/emails (status: "draft")
  -> Gmail: send email to self ("Monthly partner email draft ready in Buttondown")
```

**Module 1: Anthropic**

- Draft a 2-3 paragraph email sharing an anonymized client insight, a small business operations trend, or a relevant observation. Same voice rules apply.

**Module 2: HTTP (Buttondown API)**

| Setting        | Value                                                                                                     |
| -------------- | --------------------------------------------------------------------------------------------------------- |
| URL            | `https://api.buttondown.email/v1/emails`                                                                  |
| Method         | POST                                                                                                      |
| Headers        | `Authorization: Token {{BUTTONDOWN_API_KEY}}`, `Content-Type: application/json`                           |
| Body           | `{"subject": "{{claude_subject}}", "body": "{{claude_body}}", "status": "draft", "email_type": "public"}` |
| Parse response | Yes                                                                                                       |

The email appears in Buttondown's dashboard as a draft. The human reviews and publishes. Buttondown handles delivery, unsubscribe links, and open/click tracking.

**Subscriber sync:** Before this scenario works, import the partner list into Buttondown. See `buttondown-integration.md` for the subscriber sync process (one-time CSV import or API-based sync).

**Do not build this until:**

1. The weekly check-in scenario (above) has been running for 4+ weeks
2. At least 10 partners are in the "Active Partner" or "Intro Call Done" stage
3. You have enough anonymized client insights to share meaningful content

---

## Testing Checklist

- [ ] Run scenario manually (right-click -> Run once)
- [ ] Verify Google Sheets Search returns partners due for check-in (check module output)
- [ ] If no partners are returned, temporarily set a partner's Next Check-in Date to today and re-run
- [ ] Verify the email filter skips partners without email addresses
- [ ] Verify Claude produces valid JSON (check Anthropic module output — valid PartnerEmailDraft schema)
- [ ] Verify the draft email uses "we" voice, no "I" or "my" (read the body field)
- [ ] Verify the draft email contains no dollar amounts or fixed timeframes
- [ ] Verify the Gmail draft appears in the Drafts folder with correct To, Subject, and Body
- [ ] Verify the Google Sheet row is updated: Last Contact Date = today, Next Check-in Date = today + 14 (or 21)
- [ ] Verify the Gmail summary email shows the correct count of drafts created
- [ ] Run the scenario twice in a row — the second run should find no partners due for check-in (dates were pushed forward)
- [ ] Check operations count — should be within estimated 30-80 per run
- [ ] Let scenario run automatically for 3+ weeks, then review draft quality and cadence

---

## Tuning

After 2-3 weeks of running:

1. **Draft quality too generic:** Add more detail to partner Notes (column N) in the Google Sheet. The more context Claude has — intro call takeaways, industries they serve, referrals exchanged — the more personalized the email. The prompt is designed to use all available context.

2. **Too many partners due at once:** If all partners were added on the same day, they all come due on the same day. Stagger the initial Next Check-in Dates across the first 2-3 weeks when populating the sheet.

3. **Wrong cadence:** If 14 days feels too frequent for prospects, extend to 21 days. If active partners need more frequent check-ins, shorten to 10 days. Adjust the interval expression in Step 9.

4. **Voice violations in drafts:** If Claude occasionally uses "I" or "my", review the system prompt. The examples in `partner-nurture-prompt.ts` should catch most cases, but edge cases may require adding more negative examples to the prompt.

5. **Partner progressed to a new stage:** When a partner moves from "Prospect" to "Intro Call Done" (or any stage change), update the Relationship Stage in the Google Sheet manually. Claude uses this field to select the appropriate email variant. The scenario does not auto-advance stages.

6. **Want to track email opens:** Switch from Gmail drafts to Buttondown for 1:1 emails once the volume justifies it. Buttondown provides open/click tracking that Gmail does not. See `buttondown-integration.md` for the trade-offs (tag-based targeting vs. Gmail simplicity).

> **Note:** Notifications are handled by the Daily Digest scenario (see pipeline-2-job-monitor.md, Daily Digest Scenario section), not per-lead.

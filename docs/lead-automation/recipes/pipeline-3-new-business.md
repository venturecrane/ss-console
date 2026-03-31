# Make.com Recipe: Pipeline 3 — New Business Detection

**Purpose:** Step-by-step guide to build two Make.com scenarios that detect new and expanding businesses from city permit feeds and state filing data, then qualify them as potential prospects using Claude.

**Prerequisites:**

- Google Sheets: "SMD Lead Generation" spreadsheet with "New Business Leads" tab (see google-sheets-schema.md)
- Anthropic API key
- Make.com Data Store: `seen_permits` created (see make-data-store-schema.md)
- Gmail connection (for Scenario B — ACC/ADOR file intake)
- SODA API dataset IDs identified for Phoenix, Scottsdale, and Chandler (see soda-api-queries.md, Setup Steps section)

**No external API accounts needed for SODA endpoints** — city open data portals are free and require no authentication.

---

## Scenario A: City Permits (Daily)

Queries three city open data portals for new commercial permits issued in the last 24 hours, qualifies each with Claude, and writes qualified leads to Google Sheets.

### Scenario Overview

```
Schedule (daily 7am)
  -> Router: 3 paths (one per city)
    Path 1: HTTP -> Phoenix SODA API (commercial permits, last 24 hours)
    Path 2: HTTP -> Scottsdale SODA API
    Path 3: HTTP -> Chandler SODA API
  -> (each path continues:)
    -> Iterator: each permit result
      -> Data Store: check seen_permits (dedup by permit number)
      -> Filter: only new permits
      -> Anthropic: qualify with Claude (new-business-prompt)
      -> Parse JSON
      -> Filter: outreach_timing != "not_recommended"
      -> Google Sheets: append to "New Business Leads"
      -> Data Store: mark as seen
```

**Total modules per qualified permit:** ~7
**Expected daily volume:** 0-10 new commercial permits per day across all 3 cities. Most days will be low volume.
**Operations per run:** ~40-130 (low volume source)

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P3a City Permits`
3. Set the schedule: **Every day at 7:00 AM** (MST/Arizona time — Arizona does not observe DST)

#### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once daily at 7:00 AM

#### Step 3: Module 1 — Router (3 City Paths)

**Module: Flow Control -> Router**

Add 3 routes from the router. Each route queries a different city's SODA API. The Router runs all 3 paths in parallel — each city is independent.

Label each route:

- Route 1: "Phoenix Permits"
- Route 2: "Scottsdale Permits"
- Route 3: "Chandler Permits"

#### Step 4: Module 2a — HTTP (Phoenix SODA API)

**Module: HTTP -> Make a request** (on Route 1)

| Setting          | Value                                                        |
| ---------------- | ------------------------------------------------------------ |
| URL              | `https://www.phoenixopendata.com/resource/{DATASET_ID}.json` |
| Method           | GET                                                          |
| Headers          | None (no auth required)                                      |
| Query parameters | See below                                                    |
| Parse response   | Yes                                                          |

**Query parameters (as URL query string):**

| Key      | Value                                                                                                    |
| -------- | -------------------------------------------------------------------------------------------------------- |
| `$where` | `issue_date > '{{formatDate(addDays(now; -1); "YYYY-MM-DD")}}' AND permit_type_desc LIKE '%COMMERCIAL%'` |
| `$limit` | `100`                                                                                                    |
| `$order` | `issue_date DESC`                                                                                        |

**Important:** Replace `{DATASET_ID}` with the actual 4-character Socrata resource ID. To find it:

1. Visit phoenixopendata.com
2. Search for "building permits"
3. Note the resource ID from the URL (format: `xxxx-xxxx`)
4. Click "API" to see the exact column names
5. Adjust `$where` clause column names to match the actual schema

The `$where` clause column names (`issue_date`, `permit_type_desc`) are templates — the actual column names vary by dataset. See soda-api-queries.md for alternative filter approaches if column names differ.

**Date filter:** The expression `{{formatDate(addDays(now; -1); "YYYY-MM-DD")}}` produces yesterday's date in ISO format, e.g., `2026-03-29`.

**Error handling:** Add an error route -> Resume (continue if this city's API is down)

#### Step 5: Module 2b — HTTP (Scottsdale SODA API)

**Module: HTTP -> Make a request** (on Route 2)

| Setting          | Value                                                                      |
| ---------------- | -------------------------------------------------------------------------- |
| URL              | `https://data.scottsdaleaz.gov/resource/{DATASET_ID}.json`                 |
| Method           | GET                                                                        |
| Query parameters | Same structure as Phoenix, but adjust column names per Scottsdale's schema |
| Parse response   | Yes                                                                        |

**Query parameters:**

| Key      | Value                                                                                           |
| -------- | ----------------------------------------------------------------------------------------------- |
| `$where` | `issued_date > '{{formatDate(addDays(now; -1); "YYYY-MM-DD")}}' AND permit_type = 'Commercial'` |
| `$limit` | `100`                                                                                           |
| `$order` | `issued_date DESC`                                                                              |

Same setup steps: find the dataset ID, confirm column names, test in a browser.

#### Step 6: Module 2c — HTTP (Chandler SODA API)

**Module: HTTP -> Make a request** (on Route 3)

| Setting          | Value                                                    |
| ---------------- | -------------------------------------------------------- |
| URL              | `https://data.chandleraz.gov/resource/{DATASET_ID}.json` |
| Method           | GET                                                      |
| Query parameters | Same structure, adjusted for Chandler's column names     |
| Parse response   | Yes                                                      |

**Query parameters:**

| Key      | Value                                                                                         |
| -------- | --------------------------------------------------------------------------------------------- |
| `$where` | `issue_date > '{{formatDate(addDays(now; -1); "YYYY-MM-DD")}}' AND work_class = 'Commercial'` |
| `$limit` | `100`                                                                                         |
| `$order` | `issue_date DESC`                                                                             |

#### Step 7: Module 3 — Iterator (Permit Results)

**Module: Flow Control -> Iterator** (add one per route, after each HTTP module)

- Source array: `{{HTTP.body}}` (SODA APIs return a JSON array of permit records)
- If the API returns no results (empty array), the iterator produces 0 items -> scenario moves to the next route

**Field normalization note:** Each city uses different column names. Before passing data downstream, use a **Set Variable** module to normalize fields to a standard format:

| Standard Field  | Phoenix (example)            | Scottsdale (example)            | Chandler (example)           |
| --------------- | ---------------------------- | ------------------------------- | ---------------------------- |
| `permit_number` | `{{iterator.permit_number}}` | `{{iterator.permit_no}}`        | `{{iterator.permit_number}}` |
| `business_name` | `{{iterator.owner_name}}`    | `{{iterator.contractor_name}}`  | `{{iterator.owner_name}}`    |
| `address`       | `{{iterator.address}}`       | `{{iterator.site_address}}`     | `{{iterator.address}}`       |
| `permit_date`   | `{{iterator.issue_date}}`    | `{{iterator.issued_date}}`      | `{{iterator.issue_date}}`    |
| `description`   | `{{iterator.description}}`   | `{{iterator.work_description}}` | `{{iterator.description}}`   |
| `source`        | `phoenix_permit`             | `scottsdale_permit`             | `chandler_permit`            |

Fill in the actual column names during setup by examining the API responses from each city (see soda-api-queries.md, Normalization section).

#### Step 8: Module 4 — Data Store (Dedup Check)

**Module: Data Store -> Get a record**

- Data store: `seen_permits`
- Key: `{{permit_number}}` (the normalized permit number from Step 7)

The permit number is a natural unique identifier for each record.

#### Step 9: Filter — Only New Permits

**Add a filter after the Data Store module:**

- Condition: Data Store module **did NOT return a record** (the record does not exist)
- Label: "New permit only"

#### Step 10: Module 5 — Anthropic (Claude Qualification)

**Module: Anthropic (Claude) -> Create a Message**

| Setting       | Value                                                                                        |
| ------------- | -------------------------------------------------------------------------------------------- |
| Model         | `claude-haiku-4-5` (sufficient for this simpler classification task)                         |
| Max tokens    | `1024`                                                                                       |
| System prompt | Paste the full content of `SYSTEM_PROMPT` from `src/lead-gen/prompts/new-business-prompt.ts` |

**User message:**

```
Analyze this new business filing and determine if it's a potential operations consulting prospect.

Business name: {{business_name}}
Entity type: Commercial Permit
Address: {{address}}
Filing date: {{permit_date}}
Source: {{source}}
Permit type: {{iterator.permit_type}} (or work_class, depending on city)
Additional data: {{description}}

Produce a single JSON object matching the NewBusinessQualification schema.
```

**Why claude-haiku-4-5:** This is a simpler classification task than review scoring. The prompt provides clear heuristics for vertical detection and disqualification criteria. Haiku handles this well at significantly lower cost per call.

**Error handling:** Add an error route -> Resume (if Claude returns invalid JSON, skip this permit and continue)

#### Step 11: Module 6 — Parse JSON

**Module: JSON -> Parse JSON**

- JSON string: `{{anthropic.content[1].text}}` (Claude's response text)
- This converts the string into a structured object for downstream modules

#### Step 12: Filter — Only Qualified

**Add a filter after the JSON parse:**

- Condition: `{{json.outreach_timing}}` does NOT equal `not_recommended`
- Label: "Qualified only"

This passes permits where Claude recommends "immediate", "wait_30_days", or "wait_60_days" — all indicating a potential prospect worth tracking.

#### Step 13: Module 7 — Google Sheets (Append Row)

**Module: Google Sheets -> Add a Row**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "New Business Leads"
- Column mapping:

| Column                | Value                                                                              |
| --------------------- | ---------------------------------------------------------------------------------- |
| A: Business Name      | `{{json.business_name}}`                                                           |
| B: Entity Type        | `{{json.entity_type}}`                                                             |
| C: Address            | `{{json.address}}`                                                                 |
| D: Area               | `{{json.area}}`                                                                    |
| E: Source             | `{{json.source}}` (e.g., "phoenix_permit", "scottsdale_permit", "chandler_permit") |
| F: Filing/Permit Date | `{{permit_date}}`                                                                  |
| G: Vertical Match     | `{{json.vertical_match}}`                                                          |
| H: Size Estimate      | `{{json.size_estimate}}`                                                           |
| I: Outreach Timing    | `{{json.outreach_timing}}`                                                         |
| J: Date Found         | `{{formatDate(now; "YYYY-MM-DD")}}`                                                |
| K: Status             | `New`                                                                              |

#### Step 14: Module 8 — Data Store (Mark as Seen)

**Module: Data Store -> Add/replace a record**

- Data store: `seen_permits`
- Key: `{{permit_number}}`
- Fields:

| Field            | Value                               |
| ---------------- | ----------------------------------- |
| `record_id`      | `{{permit_number}}`                 |
| `business_name`  | `{{json.business_name}}`            |
| `source`         | `{{source}}`                        |
| `date_processed` | `{{formatDate(now; "YYYY-MM-DD")}}` |

**Important:** This module should run for ALL permits (including disqualified ones) to prevent reprocessing. Place this module BEFORE the qualification filter, or add it to both the qualified and disqualified paths using a Router after the JSON parse.

---

## Scenario B: ACC/ADOR File Intake (On-demand)

Triggered by email — when the Arizona Corporation Commission or ADOR responds to public records requests with data files. Not scheduled; fires when a matching email arrives.

### Scenario Overview

```
Gmail: Watch for new emails (with label "ACC-ADOR-Data")
  -> Google Drive: download attachment
  -> CSV/Excel: parse file
  -> Iterator: each row
    -> Data Store: check seen_permits (dedup)
    -> Filter: only new entries
    -> Anthropic: qualify with Claude
    -> Parse JSON
    -> Filter: qualified
    -> Google Sheets: append to "New Business Leads"
    -> Data Store: mark as seen
```

**Expected volume per file:** 10-100 new entities per ACC weekly extract. 50-200 per ADOR monthly extract.
**Operations per run:** ~200-1,000 (depends on file size)

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P3b ACC/ADOR Intake`
3. Set the schedule: **Immediately** (event-driven, not scheduled)

#### Step 2: Gmail Setup — Create the Label

Before building the scenario, set up Gmail filtering:

1. In Gmail, create a label: `ACC-ADOR-Data`
2. Create a Gmail filter for emails from ACC (`records@azcc.gov`) and ADOR (verify current email on azdor.gov) that have attachments
3. Apply the `ACC-ADOR-Data` label automatically
4. This separates ACC/ADOR data emails from regular inbox traffic

#### Step 3: Module 1 — Gmail (Watch for Emails)

**Module: Gmail -> Watch Emails**

- Label: `ACC-ADOR-Data`
- Criteria: Has attachment
- Maximum number of results: 1 (process one email at a time)

This module polls Gmail for new emails with the `ACC-ADOR-Data` label. When a matching email arrives, it triggers the scenario.

#### Step 4: Module 2 — Gmail (Download Attachment)

**Module: Gmail -> Download Attachment** (or use an Iterator on the email's attachments)

- Email ID: `{{gmail.id}}` (from the Watch module)
- Attachment index: 1 (the first attachment — ACC/ADOR typically sends a single file)

#### Step 5: Module 3 — CSV/Excel Parse

**Module: CSV -> Parse CSV** (for ACC data) or **Microsoft Excel -> Parse Excel** (if ADOR sends .xlsx)

For CSV:

- Source file: `{{gmail.attachments[1].data}}` (the downloaded attachment)
- Delimiter: `,` (standard CSV)
- First row is header: Yes

For Excel:

- Source file: `{{gmail.attachments[1].data}}`
- Sheet: 1 (first sheet)
- First row is header: Yes

**Handling both formats:** Use a **Router** after the attachment download. Route 1 handles `.csv` files (filter on filename ending in `.csv`). Route 2 handles `.xlsx` files. Both routes feed into the same downstream processing chain.

#### Step 6: Module 4 — Iterator (Each Row)

**Module: Flow Control -> Iterator**

- Source array: The parsed rows from the CSV/Excel module

Each row represents one business filing or TPT license.

#### Step 7: Module 5 — Data Store (Dedup Check)

**Module: Data Store -> Get a record**

- Data store: `seen_permits`
- Key: Use the filing number or entity number from the row. The exact column depends on the ACC/ADOR file format:
  - ACC: Entity number or file number (e.g., `{{iterator.entity_number}}`)
  - ADOR: License number (e.g., `{{iterator.license_number}}`)

#### Step 8: Filter — Only New Entries

**Add a filter:**

- Condition: Data Store module **did NOT return a record**
- Label: "New entity only"

#### Step 9: Module 6 — Anthropic (Claude Qualification)

**Module: Anthropic (Claude) -> Create a Message**

| Setting       | Value                                                                                         |
| ------------- | --------------------------------------------------------------------------------------------- |
| Model         | `claude-haiku-4-5`                                                                            |
| Max tokens    | `1024`                                                                                        |
| System prompt | Same as Scenario A — paste `SYSTEM_PROMPT` from `src/lead-gen/prompts/new-business-prompt.ts` |

**User message:**

```
Analyze this new business filing and determine if it's a potential operations consulting prospect.

Business name: {{iterator.business_name}}
Entity type: {{iterator.entity_type}}
Address: {{iterator.address}}
Filing date: {{iterator.filing_date}}
Source: acc_filing (or ador_tpt — set based on which label/sender triggered the email)
Additional data: {{iterator.description}} (or any other relevant columns)

Produce a single JSON object matching the NewBusinessQualification schema.
```

**Source determination:** Use a **Set Variable** module before the Claude call to set the `source` field based on the email sender. If the email is from `records@azcc.gov`, set source to `acc_filing`. If from ADOR, set to `ador_tpt`.

#### Step 10: Modules 7-9 — Parse, Filter, Sheet, Data Store

These modules are identical to Scenario A Steps 11-15:

1. **Parse JSON** — Parse Claude's response
2. **Filter** — `outreach_timing` != `not_recommended`
3. **Google Sheets** — Append to "New Business Leads" (same column mapping as Scenario A, with `source` reflecting `acc_filing` or `ador_tpt`)
4. **Data Store** — Mark as seen in `seen_permits`

No per-lead notification for this scenario — the volume is higher per run and the filings are less time-sensitive than permits. New leads appear in the Daily Digest email (see pipeline-2-job-monitor.md). Review the Google Sheet periodically for additional detail.

---

## Testing Checklist

### Scenario A (City Permits)

- [ ] Run scenario manually (right-click -> Run once)
- [ ] Verify each SODA API returns data (check HTTP module output for all 3 cities)
- [ ] If a city returns no data, verify the dataset ID and column names are correct by testing the URL in a browser: `https://{portal}/resource/{ID}.json?$limit=5`
- [ ] Verify dedup works: run twice, second run should skip all previously seen permits
- [ ] Verify Claude produces valid JSON (check Anthropic module output)
- [ ] Verify qualified permits appear in the Google Sheet with correct source values
- [ ] Check operations count — should be within estimated 40-130 per run
- [ ] Let scenario run automatically for 5+ days, then review all qualified leads for accuracy

### Scenario B (ACC/ADOR Intake)

- [ ] Send a test email to yourself with the `ACC-ADOR-Data` label and a sample CSV attachment
- [ ] Verify the Gmail Watch module picks up the email
- [ ] Verify the CSV is parsed correctly (check column names and data types)
- [ ] Verify dedup works: process the same file twice, second run should skip all entries
- [ ] Verify Claude produces valid JSON for each row
- [ ] Verify qualified entities appear in the Google Sheet

---

## Tuning

After 1-2 weeks of running:

1. **Dataset IDs not found:** The most common setup issue. Visit each city's open data portal, search for building permits, and note the exact resource ID and column names. Test each URL in a browser before entering it in Make.com.

2. **Too many irrelevant permits:** The SODA `$where` filter may be too broad. Tighten the filter to look specifically for tenant improvement permits: `description LIKE '%tenant improvement%'` or `permit_subtype = 'Tenant Improvement'` (adjust column names to match the actual schema).

3. **Too few results:** Broaden the filter. Remove the `COMMERCIAL` restriction and let Claude handle classification — it will disqualify non-commercial permits via the prompt.

4. **Claude over-qualifying:** If too many holding companies, franchises, or non-operational entities are passing qualification, add more disqualification examples to the system prompt. The existing prompt handles common cases but may need tuning for local patterns.

5. **ACC/ADOR file format changes:** When ACC or ADOR changes their file format, the CSV/Excel parse module will break. Update the column mappings and iterator references to match the new format. Keep a sample of each file format for reference.

> **Note:** Notifications are handled by the Daily Digest scenario (see pipeline-2-job-monitor.md, Daily Digest Scenario section), not per-lead.

# Make.com Recipe: Pipeline 2 — Job Posting Monitor

**Purpose:** Step-by-step guide to build the Make.com scenario that monitors Phoenix job postings for operational pain signals at small businesses.

**Prerequisites:**

- SerpAPI account with API key (Developer plan, $50/mo)
- Anthropic API key
- Google Sheets: "SMD Lead Generation" spreadsheet with "Job Signal Leads" tab (see google-sheets-schema.md)
- Make.com Data Store: `seen_jobs` created (see make-data-store-schema.md)

---

## Scenario Overview

```
Schedule (daily 6am)
  → HTTP: SerpAPI query 1
  → Iterator: each job result
    → Data Store: check seen_jobs (dedup)
    → Filter: only new jobs
    → Anthropic: qualify with Claude
    → Filter: only qualified = true
    → Google Sheets: append row
    → Data Store: mark as seen
```

**Total modules per job processed:** ~7
**Expected daily volume:** 5-15 new jobs across 8 queries
**Operations per run:** ~90-180 (8 queries × ~10 results × ~7 modules for new ones, minus filtered dupes)

---

## Step-by-Step Build

### Step 1: Create the Scenario

1. In Make.com → Scenarios → Create a new scenario
2. Name: `Lead Gen: P2 Job Monitor`
3. Set the schedule: **Every day at 6:00 AM** (MST/Arizona time — Arizona does not observe DST)

### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once daily at 6:00 AM
3. This fires the first module in the chain

### Step 3: Module 1 — HTTP (SerpAPI Query)

We need to run 8 queries. The simplest approach: use a **Set Variable** module to define the query list, then iterate.

**Module: Tools → Set multiple variables**

- Variable 1: `queries` (array)
- Value:

```json
[
  "office manager",
  "operations manager",
  "dispatcher",
  "scheduling coordinator",
  "customer service coordinator",
  "office administrator",
  "front desk manager",
  "service coordinator"
]
```

### Step 4: Module 2 — Iterator (Query List)

**Module: Flow Control → Iterator**

- Source array: `{{queries}}` (from the Set Variable module)
- This processes each query term one at a time

### Step 5: Module 3 — HTTP (SerpAPI Request)

**Module: HTTP → Make a request**

| Setting          | Value                        |
| ---------------- | ---------------------------- |
| URL              | `https://serpapi.com/search` |
| Method           | GET                          |
| Query parameters | See below                    |
| Parse response   | Yes                          |

**Query parameters:**
| Key | Value |
|-----|-------|
| `engine` | `google_jobs` |
| `q` | `{{iterator.value}}` (the current query term) |
| `location` | `Phoenix, Arizona, United States` |
| `chips` | `date_posted:3days` |
| `api_key` | `{{SERPAPI_API_KEY}}` (from connection or scenario variable) |

**Error handling:** Add an error route → Resume (continue to next query if one fails)

### Step 6: Module 4 — Iterator (Job Results)

**Module: Flow Control → Iterator**

- Source array: `{{HTTP.body.jobs_results}}` (the jobs array from SerpAPI response)
- If `jobs_results` is empty or undefined, the iterator produces 0 items → scenario continues to next query

### Step 7: Module 5 — Data Store (Dedup Check)

**Module: Data Store → Get a record**

- Data store: `seen_jobs`
- Key: Use a hash of company + title + location. In Make.com, construct the key:
  ```
  {{sha256(iterator2.company_name + "|" + iterator2.title + "|" + iterator2.location)}}
  ```
  (If `sha256` is not available natively, use `md5` or concatenate the fields as the key directly — just ensure uniqueness)

**Alternative simpler key:** `{{iterator2.company_name}}_{{iterator2.title}}` (less collision-proof but functional)

### Step 8: Filter — Only New Jobs

**Add a filter between the Data Store module and the next module:**

- Condition: Data Store module **did NOT return a record** (the record does not exist)
- In Make.com: the filter checks if the Data Store output bundle is empty
- Label: "New job only"

### Step 9: Module 6 — Anthropic (Claude Qualification)

**Module: Anthropic (Claude) → Create a Message**

| Setting       | Value                                                                                                               |
| ------------- | ------------------------------------------------------------------------------------------------------------------- |
| Model         | `claude-sonnet-4-6` (cost-effective for classification tasks)                                                       |
| Max tokens    | `1024`                                                                                                              |
| System prompt | Paste the full content of `JOB_QUALIFICATION_SYSTEM_PROMPT` from `src/lead-gen/prompts/job-qualification-prompt.ts` |

**User message:**

```
Analyze this job posting and determine if it signals operational pain at a small business.

Job title: {{iterator2.title}}
Company: {{iterator2.company_name}}
Location: {{iterator2.location}}
Source: google_jobs
URL: {{iterator2.apply_options[1].link}}

Description:
{{iterator2.description}}

Produce a single JSON object matching the JobQualification schema.
```

### Step 10: Module 7 — Parse JSON

**Module: JSON → Parse JSON**

- JSON string: `{{anthropic.content[1].text}}` (Claude's response text)
- This converts the string into a structured object you can reference in downstream modules

### Step 11: Filter — Only Qualified

**Add a filter after the JSON parse:**

- Condition: `{{json.qualified}}` equals `true`
- Label: "Qualified only"

### Step 12: Module 8 — Google Sheets (Append Row)

**Module: Google Sheets → Add a Row**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Job Signal Leads"
- Column mapping:

| Column                   | Value                                    |
| ------------------------ | ---------------------------------------- |
| A: Company Name          | `{{json.company}}`                       |
| B: Job Title Posted      | `{{iterator2.title}}`                    |
| C: Location              | `{{iterator2.location}}`                 |
| D: Source                | `Google Jobs`                            |
| E: Company Size Estimate | `{{json.company_size_estimate}}`         |
| F: Qualified             | `Yes`                                    |
| G: Confidence            | `{{json.confidence}}`                    |
| H: Problems Signaled     | `{{join(json.problems_signaled; ", ")}}` |
| I: Evidence              | `{{json.evidence}}`                      |
| J: Outreach Angle        | `{{json.outreach_angle}}`                |
| K: Job Posting URL       | `{{iterator2.apply_options[1].link}}`    |
| L: Date Found            | `{{formatDate(now; "YYYY-MM-DD")}}`      |
| M: Status                | `New`                                    |

### Step 13: Module 9 — Data Store (Mark as Seen)

**Module: Data Store → Add/replace a record**

- Data store: `seen_jobs`
- Key: Same hash as Step 7
- Fields:
  - `company_name`: `{{iterator2.company_name}}`
  - `job_title`: `{{iterator2.title}}`
  - `first_seen`: `{{formatDate(now; "YYYY-MM-DD")}}`
  - `qualified`: `{{json.qualified}}`

---

## Craigslist RSS Addition

Add a second path to the scenario for Craigslist:

### Module: RSS → Watch RSS Feed Items

- URL: `https://phoenix.craigslist.org/search/jjj?query=office+manager&format=rss`
- Max items: 10
- Schedule: Every 6 hours (separate from the main daily SerpAPI run, or triggered by the same schedule)

Connect the RSS output to the same Anthropic → Filter → Sheets → Data Store chain.

**Differences for Craigslist:**

- `source` = `craigslist`
- `company` may be extracted from the title or set to `"(Craigslist - see posting)"`
- RSS provides `title`, `link`, `description` (truncated), and `pubDate`
- Pass the `description` as the job description (it's shorter, Claude will work with what it has)

To monitor multiple Craigslist queries: use a Router module after the schedule trigger, with separate RSS modules for each query term on parallel paths.

---

## Testing Checklist

- [ ] Run scenario manually (right-click → Run once)
- [ ] Verify SerpAPI returns results (check HTTP module output)
- [ ] Verify dedup works: run twice, second run should skip all previously seen jobs
- [ ] Verify Claude produces valid JSON (check Anthropic module output)
- [ ] Verify qualified jobs appear in the Google Sheet
- [ ] Check operations count — should be within estimated 90-180 per run
- [ ] Let it run for 3 days automatically, then review all qualified leads for accuracy

---

## Tuning

After a week of running:

1. **False positives:** If too many large companies are being qualified, tighten the system prompt examples or add more disqualification criteria.
2. **False negatives:** If small businesses are being incorrectly disqualified, review the disqualified results (they're still in the Data Store with `qualified: false`) and adjust the prompt.
3. **Volume too low:** Add the expansion queries (see serpapi-queries.md) or increase `chips` from `3days` to `week`.
4. **Volume too high:** Add a filter for `confidence` — only pass `high` and `medium` to Sheets.

> **Note:** Notifications are handled by the Daily Digest scenario (see below), not per-lead.

---

## Daily Digest Scenario

A separate Make.com scenario that sends one morning email summarizing all new leads across all pipelines.

### Scenario Overview

```
Schedule (daily 6:30am, runs AFTER all pipeline scenarios)
  → Google Sheets: search "Job Signal Leads" for rows where Date Found = today
  → Google Sheets: search "Review Signal Leads" for rows where Date Found = today
  → Google Sheets: search "New Business Leads" for rows where Date Found = today
  → Text Aggregator: compile all new leads into a digest
  → Gmail: send email to self
```

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com → Scenarios → Create a new scenario
2. Name: `Lead Gen: Daily Digest`
3. Set the schedule: **Every day at 6:30 AM** (MST/Arizona time — Arizona does not observe DST)

This runs 30 minutes after the earliest pipeline (P2 Job Monitor at 6:00 AM) and after P3 City Permits (7:00 AM). All pipelines will have completed by 6:30 AM on most days. If P3 runs later, adjust to 7:30 AM.

#### Step 2: Module 1 — Google Sheets (Search Job Signal Leads)

**Module: Google Sheets → Search Rows**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Job Signal Leads"
- Filter: `L: Date Found` equals `{{formatDate(now; "YYYY-MM-DD")}}`

#### Step 3: Module 2 — Google Sheets (Search Review Signal Leads)

**Module: Google Sheets → Search Rows**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "Review Signal Leads"
- Filter: `L: Date Found` equals `{{formatDate(now; "YYYY-MM-DD")}}`

#### Step 4: Module 3 — Google Sheets (Search New Business Leads)

**Module: Google Sheets → Search Rows**

- Spreadsheet: "SMD Lead Generation"
- Sheet: "New Business Leads"
- Filter: `J: Date Found` equals `{{formatDate(now; "YYYY-MM-DD")}}`

#### Step 5: Module 4 — Text Aggregator (Compile Digest)

**Module: Tools → Set multiple variables**

Build the digest body from the three sheet search results. Use `ifempty` to handle days with no results for a given pipeline.

- Variable: `digest_body`
- Value:

```
REVIEW SIGNALS ({{length(sheetsModule2.results)}})
{{#each sheetsModule2.results}}
- {{this.A: Business Name}} | Pain: {{this.H: Pain Score}}/10 | {{this.I: Top Problems}} | {{this.K: Outreach Angle}}
{{/each}}

JOB SIGNALS ({{length(sheetsModule1.results)}})
{{#each sheetsModule1.results}}
- {{this.A: Company Name}} hiring {{this.B: Job Title Posted}} | {{this.G: Confidence}} | {{this.H: Problems Signaled}} | {{this.J: Outreach Angle}}
{{/each}}

NEW BUSINESSES ({{length(sheetsModule3.results)}})
{{#each sheetsModule3.results}}
- {{this.A: Business Name}} | {{this.E: Source}} | {{this.G: Vertical Match}} | {{this.I: Outreach Timing}}
{{/each}}

Full details in the Lead Generation spreadsheet.
```

**Note on iteration:** Make.com does not support `{{#each}}` natively. Use Text Aggregator modules after each Google Sheets search to concatenate the rows into formatted text blocks, then combine them in a Set Variable module. Alternatively, use Iterator → Text Aggregator chains for each sheet result set.

- Variable: `total_count`
- Value: `{{length(sheetsModule1.results) + length(sheetsModule2.results) + length(sheetsModule3.results)}}`

#### Step 6: Filter — Only Send if Leads Exist

**Add a filter before the Gmail module:**

- Condition: `{{total_count}}` is greater than `0`
- Label: "Has new leads"

This prevents sending an empty digest on quiet days.

#### Step 7: Module 5 — Gmail (Send Digest Email)

**Module: Gmail → Send an Email**

| Setting      | Value                                                                             |
| ------------ | --------------------------------------------------------------------------------- |
| To           | Your business email address                                                       |
| Subject      | `Lead Gen Digest — {{formatDate(now; "YYYY-MM-DD")}} — {{total_count}} new leads` |
| Content      | `{{digest_body}}`                                                                 |
| Content type | Plain text                                                                        |

**Email format:**

```
Subject: Lead Gen Digest — {date} — {total_count} new leads

REVIEW SIGNALS ({count})
- {business_name} | Pain: {score}/10 | {top_problems} | {outreach_angle}

JOB SIGNALS ({count})
- {company} hiring {title} | {confidence} | {problems} | {outreach_angle}

NEW BUSINESSES ({count})
- {business_name} | {source} | {vertical} | {outreach_timing}

SOCIAL ({count})
- {platform}: {title} — {url}

Full details in the Lead Generation spreadsheet.
```

### Operations Estimate

~50 ops/day (3 sheet searches + aggregation + email)

### Note

This scenario runs once daily after all other scenarios complete. All pipelines write their results to Google Sheets; this scenario reads from those sheets and compiles a single morning summary.

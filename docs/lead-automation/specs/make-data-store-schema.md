# Make.com Data Store Schema

## What Are Data Stores?

Make.com Data Stores are simple key-value tables that live inside your Make.com account. They act as lightweight databases that scenarios can read from and write to during execution. We use them for **deduplication** — ensuring that each lead generation pipeline doesn't reprocess records it has already seen.

Each Data Store has a defined structure (columns) and a primary key. Scenarios check the Data Store before processing a record: if the key exists, skip it; if not, process it and write the key.

## Creation Instructions

1. Go to **Settings → Data Stores** in your Make.com dashboard (left sidebar, gear icon).
2. Click **Add data store**.
3. Enter the name exactly as specified below.
4. Define the data structure (columns) as documented for each table.
5. Set the primary key as indicated.

Repeat for all 4 Data Stores.

## Plan Limits

Data Store capacity depends on your Make.com plan:

- **Free plan:** 1 Data Store, 1 MB storage — not sufficient for our pipelines.
- **Core plan:** Limited Data Stores, 1 MB each.
- **Pro plan ($16/mo):** Unlimited Data Stores, 1 MB each by default (expandable). Ample for our volume — each record is a few hundred bytes, so 1 MB holds thousands of entries per table.

The Pro plan is required for running these pipelines.

## Dedup Check Pattern

Every pipeline follows the same dedup pattern:

1. **Get a record** — Use the "Data Stores → Get a record" module with the record's key (e.g., `place_id`, `job_hash`).
2. **Filter** — Add a filter after the Get module. Set the condition to: "The record does NOT exist" (the Get module returns empty).
3. **Process** — If the filter passes (record not found), continue with the pipeline's processing logic.
4. **Add/replace a record** — After processing, use "Data Stores → Add/replace a record" to write the key and its fields. This prevents reprocessing on future runs.

If the record already exists, the filter stops execution for that item and the pipeline moves to the next.

---

## Table 1: `seen_businesses`

**Pipeline:** Pipeline 1 — Review Mining

**Purpose:** Prevents rescoring businesses whose reviews haven't changed since the last scan.

| Field              | Type   | Description                                          |
| ------------------ | ------ | ---------------------------------------------------- |
| `place_id` (key)   | String | Google Places API `place_id`. Primary key.           |
| `business_name`    | String | Name of the business.                                |
| `last_scanned`     | Date   | ISO 8601 date of the most recent scan.               |
| `last_review_date` | Date   | Date of the most recent review at the time of scan.  |
| `last_pain_score`  | Number | Previous AI-assigned pain score, for tracking drift. |

**Dedup logic:** Before scoring a business, check if `place_id` exists. If it does, compare `last_review_date` to the current most recent review. If unchanged, skip. If a newer review exists, reprocess and update the record.

**Cleanup rule:** Delete entries where `last_scanned` is older than 90 days. The business may have closed, moved, or become irrelevant. Use a scheduled maintenance scenario or manual cleanup monthly.

---

## Table 2: `seen_jobs`

**Pipeline:** Pipeline 2 — Job Monitor

**Purpose:** Prevents reprocessing the same job posting across runs.

| Field            | Type    | Description                                                              |
| ---------------- | ------- | ------------------------------------------------------------------------ |
| `job_hash` (key) | String  | SHA-256 hash of `company_name + job_title + location`. Primary key.      |
| `company_name`   | String  | Name of the hiring company.                                              |
| `job_title`      | String  | Title of the job posting.                                                |
| `first_seen`     | Date    | ISO 8601 date the posting was first detected.                            |
| `qualified`      | Boolean | Result of AI qualification (`true` = qualified, `false` = disqualified). |

**Dedup logic:** Before processing a job posting, compute its hash and check if `job_hash` exists. If it does, skip entirely. If not, run qualification and write the result.

**Cleanup rule:** Delete entries where `first_seen` is older than 60 days. Job postings expire and the hash space should stay clean.

---

## Table 3: `seen_permits`

**Pipeline:** Pipeline 3 — New Business Detection

**Purpose:** Prevents reprocessing the same permit or business filing.

| Field             | Type   | Description                                                                                                           |
| ----------------- | ------ | --------------------------------------------------------------------------------------------------------------------- |
| `record_id` (key) | String | Permit number or ACC filing number. Primary key.                                                                      |
| `business_name`   | String | Name of the business on the permit or filing.                                                                         |
| `source`          | String | Origin of the record: `phoenix_permit`, `scottsdale_permit`, `chandler_permit`, `acc_filing`, `ador_tpt`, `sba_loan`. |
| `date_processed`  | Date   | ISO 8601 date the record was processed by the pipeline.                                                               |

**Dedup logic:** Before processing a permit or filing, check if `record_id` exists. If it does, skip. If not, process and write the record.

**Cleanup rule:** Delete entries where `date_processed` is older than 180 days. Permits and filings are one-time events; keeping them longer than 6 months wastes storage.

---

## Table 4: `seen_social`

**Pipeline:** Pipeline 4 — Social Listening

**Purpose:** Prevents surfacing the same social post or alert twice.

| Field           | Type   | Description                                                            |
| --------------- | ------ | ---------------------------------------------------------------------- |
| `post_id` (key) | String | Reddit post ID, or SHA-256 of Google Alert subject + URL. Primary key. |
| `platform`      | String | Source platform: `reddit`, `google_alerts`, `craigslist`.              |
| `date_found`    | Date   | ISO 8601 date the post was first detected.                             |

**Dedup logic:** Before processing a social post, check if `post_id` exists. If it does, skip. If not, process and write the record.

**Cleanup rule:** Delete entries where `date_found` is older than 30 days. Social content is ephemeral — old posts are no longer actionable and the table should stay lean.

---

## Maintenance

Run a cleanup scenario monthly (or set a scheduled scenario) that iterates each Data Store and deletes records past their retention window. Alternatively, perform manual cleanup via **Settings → Data Stores → [table name] → Browse records** and sort by date.

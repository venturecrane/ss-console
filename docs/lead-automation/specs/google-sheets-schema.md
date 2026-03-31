# Google Sheets Schema — Lead Generation Pipelines

This document defines the column structure for 6 Google Sheets used as output destinations for automated lead generation pipelines built in Make.com. Each sheet corresponds to a specific pipeline source.

**Creation order:** Create Sheets 1-5 first, then Sheet 6 (Master Pipeline). The Master Pipeline references data from the other five sheets, so those must exist before consolidation begins.

**Naming convention:** Name each sheet tab exactly as shown (e.g., "Review Signal Leads") so Make.com scenario references remain consistent.

---

## Sheet 1: Review Signal Leads (Pipeline 1)

Source: Google Maps review scraping and AI scoring.

| Column | Header         | Type     | Notes                                                                         |
| ------ | -------------- | -------- | ----------------------------------------------------------------------------- |
| A      | Business Name  | text     |                                                                               |
| B      | Phone          | text     |                                                                               |
| C      | Website        | URL      | Auto-detect hyperlink                                                         |
| D      | Category       | text     | e.g., "plumber", "HVAC", "dentist"                                            |
| E      | Area           | text     | Phoenix sub-area (e.g., "Scottsdale", "Tempe", "Gilbert")                     |
| F      | Google Rating  | number   | 1-5                                                                           |
| G      | Review Count   | number   |                                                                               |
| H      | Pain Score     | number   | 1-10. Conditional format: green (1-3), yellow (4-6), orange (7-8), red (9-10) |
| I      | Top Problems   | text     | Comma-separated problem IDs (e.g., "1,3,5")                                   |
| J      | Evidence       | text     | Key review quotes supporting the pain score                                   |
| K      | Outreach Angle | text     | Suggested opening for outreach                                                |
| L      | Date Found     | date     | Format: YYYY-MM-DD                                                            |
| M      | Status         | dropdown | Values: New / Contacted / Responded / Meeting Booked / Dead                   |
| N      | Notes          | text     | Free-form                                                                     |

---

## Sheet 2: Job Signal Leads (Pipeline 2)

Source: Job posting scraping from Google Jobs, Craigslist, and local job boards.

| Column | Header                | Type     | Notes                                                       |
| ------ | --------------------- | -------- | ----------------------------------------------------------- |
| A      | Company Name          | text     |                                                             |
| B      | Job Title Posted      | text     |                                                             |
| C      | Location              | text     |                                                             |
| D      | Source                | text     | e.g., "Google Jobs", "Craigslist"                           |
| E      | Company Size Estimate | text     | e.g., "10-25", "unknown"                                    |
| F      | Qualified             | text     | "Yes", "No", "Maybe"                                        |
| G      | Confidence            | text     | "high", "medium", "low"                                     |
| H      | Problems Signaled     | text     | Comma-separated problem IDs                                 |
| I      | Evidence              | text     | Key job description excerpts                                |
| J      | Outreach Angle        | text     |                                                             |
| K      | Job Posting URL       | URL      | Auto-detect hyperlink                                       |
| L      | Date Found            | date     | Format: YYYY-MM-DD                                          |
| M      | Status                | dropdown | Values: New / Contacted / Responded / Meeting Booked / Dead |
| N      | Notes                 | text     |                                                             |

---

## Sheet 3: New Business Leads (Pipeline 3)

Source: Public filings — Arizona Corporation Commission, ADOR TPT registrations, city permit feeds, SBA loan data.

| Column | Header             | Type     | Notes                                                                                          |
| ------ | ------------------ | -------- | ---------------------------------------------------------------------------------------------- |
| A      | Business Name      | text     |                                                                                                |
| B      | Entity Type        | text     | LLC, Corp, etc.                                                                                |
| C      | Address            | text     |                                                                                                |
| D      | Area               | text     | Phoenix sub-area                                                                               |
| E      | Source             | text     | "ACC Filing", "ADOR TPT", "Phoenix Permit", "Scottsdale Permit", "Chandler Permit", "SBA Loan" |
| F      | Filing/Permit Date | date     | Format: YYYY-MM-DD                                                                             |
| G      | Vertical Match     | text     | Vertical ID or "unknown"                                                                       |
| H      | Size Estimate      | text     |                                                                                                |
| I      | Outreach Timing    | text     | "immediate", "wait 30 days", "wait 60 days"                                                    |
| J      | Date Found         | date     | Format: YYYY-MM-DD                                                                             |
| K      | Status             | dropdown | Values: New / Contacted / Responded / Meeting Booked / Dead                                    |
| L      | Notes              | text     |                                                                                                |

---

## Sheet 4: Social Opportunities (Pipeline 4)

Source: Reddit, Facebook groups, Nextdoor, Google Alerts — keyword-monitored posts where a business owner is asking for help or venting about operational pain.

| Column | Header               | Type     | Notes                                            |
| ------ | -------------------- | -------- | ------------------------------------------------ |
| A      | Platform             | text     | "Reddit", "Facebook", "Nextdoor", "Google Alert" |
| B      | Post Title / Subject | text     |                                                  |
| C      | Author               | text     |                                                  |
| D      | URL                  | URL      | Auto-detect hyperlink                            |
| E      | Relevance            | text     | "high", "medium", "low"                          |
| F      | Topic                | text     | Brief categorization                             |
| G      | Date Found           | date     | Format: YYYY-MM-DD                               |
| H      | Responded            | checkbox |                                                  |
| I      | Response Notes       | text     |                                                  |

---

## Sheet 5: Referral Partners (Pipeline 5)

Source: Bookkeeper/accountant/insurance agent research. Enhances the existing bookkeeper-prospect-list.md structure with relationship tracking.

| Column | Header             | Type     | Notes                                                                      |
| ------ | ------------------ | -------- | -------------------------------------------------------------------------- |
| A      | Firm Name          | text     |                                                                            |
| B      | Contact Name       | text     |                                                                            |
| C      | Area               | text     |                                                                            |
| D      | Phone              | text     |                                                                            |
| E      | Email              | text     |                                                                            |
| F      | Website            | URL      | Auto-detect hyperlink                                                      |
| G      | Tier               | number   | 1, 2, or 3                                                                 |
| H      | Focus Areas        | text     | What verticals/services they specialize in                                 |
| I      | Relationship Stage | dropdown | Values: Prospect / Intro Sent / Intro Call Done / Active Partner / Dormant |
| J      | Last Contact Date  | date     | Format: YYYY-MM-DD                                                         |
| K      | Next Check-in Date | date     | Format: YYYY-MM-DD                                                         |
| L      | Referrals Received | number   | Count of referrals from them to us                                         |
| M      | Referrals Sent     | number   | Count of referrals from us to them                                         |
| N      | Notes              | text     |                                                                            |

---

## Sheet 6: Master Pipeline

Consolidated view across all sources. This is the working pipeline for outreach prioritization and tracking.

| Column | Header          | Type     | Notes                                                       |
| ------ | --------------- | -------- | ----------------------------------------------------------- |
| A      | Business Name   | text     |                                                             |
| B      | Source Pipeline | text     | "Reviews", "Jobs", "New Biz", "Social", "Referral"          |
| C      | Category        | text     |                                                             |
| D      | Area            | text     |                                                             |
| E      | Pain Score      | number   | From AI scoring, or blank for non-scored sources            |
| F      | Top Problems    | text     |                                                             |
| G      | Outreach Angle  | text     |                                                             |
| H      | Date Found      | date     | Format: YYYY-MM-DD                                          |
| I      | Status          | dropdown | Values: New / Contacted / Responded / Meeting Booked / Dead |
| J      | Notes           | text     |                                                             |

**Population method:** Manual consolidation. Only promote leads worth pursuing from individual sheets (1-5) into the Master Pipeline. IMPORTRANGE formulas are an option for later automation, but manual curation is recommended initially to maintain signal quality.

---

## Formatting Rules (All Sheets)

Apply these formatting standards when creating each sheet:

1. **Header row (Row 1):** Frozen, bold text, light gray background (`#f3f3f3`).
2. **Pain Score columns:** Conditional formatting rules:
   - 1-3: Green background (`#d9ead3`)
   - 4-6: Yellow background (`#fff2cc`)
   - 7-8: Orange background (`#fce5cd`)
   - 9-10: Red background (`#f4cccc`)
3. **Status columns:** Data validation dropdown. Allowed values listed per sheet above.
4. **Date columns:** Number format set to `yyyy-mm-dd`.
5. **URL columns:** Auto-detect hyperlinks enabled.
6. **New rows from Make.com:** Always append to the bottom of the sheet (never insert).
7. **New row highlight:** Conditional formatting rule on the Date Found column — highlight the row if Date Found equals today. Use light blue background (`#cfe2f3`) so new rows are immediately visible.

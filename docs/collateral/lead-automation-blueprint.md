# Lead Generation Automation Blueprint

**Purpose:** Architecture for automated prospect discovery and qualification. Turns manual networking into a system that surfaces the right 15-20 people each week -- before they know they need help.

**The advantage:** Every other local operations consultant is at chamber mixers and BNI meetings hoping to bump into the right person. Those channels matter and we use them. But we also have five automated pipelines running in the background, finding businesses whose reviews, job postings, and public filings are already telling us exactly what's broken. We show up to the conversation knowing the problem. That's the edge.

---

## The Five Pipelines

| #   | Pipeline                 | Signal                                        | Why It Works                                               | Frequency |
| --- | ------------------------ | --------------------------------------------- | ---------------------------------------------------------- | --------- |
| 1   | Review Mining            | Operational complaints in Google/Yelp reviews | You know their exact pain before you call                  | Weekly    |
| 2   | Job Posting Monitor      | Small companies hiring ops roles              | They're trying to hire their way out of a problem we solve | Daily     |
| 3   | New Business Detection   | ACC filings, city permits, TPT licenses       | Catch them before the chaos sets in                        | Weekly    |
| 4   | Social Listening         | Reddit, Facebook groups, Craigslist           | Be the answer, not the pitch                               | Daily     |
| 5   | Referral Partner Nurture | Automated check-ins with bookkeeper partners  | Keep the warmest channel warm without manual effort        | Weekly    |

---

## Pipeline 1: Review Mining

**The idea:** Scan Google and Yelp reviews for Phoenix-area businesses in our target verticals. Use AI to flag reviews that describe operational problems — missed calls, scheduling chaos, lost paperwork, slow follow-up. These businesses have prospects publicly documenting their pain.

### Data Flow

```
Google Places API          Outscraper (review text)         Claude API
(discover businesses) -->  (pull recent reviews)      -->  (score for ops pain)
       |                          |                              |
       v                          v                              v
  Business list             Review corpus              Pain signals + score
  (name, phone,             (full text,                (which of the 5 categories,
   address, category,        date-filtered,             severity 1-10,
   rating, place_id)         per-business)              specific evidence)
                                                              |
                                                              v
                                                     Filter: score >= 7
                                                              |
                                                    +---------+---------+
                                                    |                   |
                                                    v                   v
                                            Google Sheet
                                          "Qualified Leads"
```

### How It Works

**Step 1 — Discover businesses.** Use Google Places API Text Search to find businesses by vertical + geography. Query examples:

- "plumber Phoenix AZ"
- "HVAC contractor Scottsdale"
- "accounting firm Chandler"
- "dental office Mesa"
- "auto repair shop Tempe"

The API returns up to 60 results per query (paginated). Run 10-15 queries per vertical to build a master list. This only needs to run monthly — business listings don't change that fast. Store place_ids in the Make.com Data Store for deduplication.

Google's $200/mo free credit covers ~6,250 Text Searches — more than enough.

**Step 2 — Pull recent reviews.** Use Outscraper's Google Maps Reviews API with date filtering. For each business in the master list, request reviews from the last 7 days.

Outscraper is the best fit here because:

- Returns ALL reviews, not the 5-review cap of the official API
- Supports date range filtering (only pull new reviews since last scan)
- Cost: ~$3 per 1,000 review tasks
- At 500 businesses/week: ~$6/month

Alternative: DataForSEO at ~$2 per 1,000 tasks (cheaper but no date filter — you'd pull all and diff locally).

**Step 3 — AI analysis.** Pass each batch of reviews through Claude (via Make.com's native Anthropic module). The prompt:

```
You are analyzing Google reviews for a {business_type} called "{business_name}"
in {city}, AZ. Your job is to identify operational problems that signal a business
could benefit from solutions consulting -- NOT service quality or product issues.

Look for signals matching these 5 solution categories:
1. Process design — "only the owner can help," "had to wait for the boss," "nobody knew the process," "no system for handling X"
2. Tools & systems — "their software is terrible," "had to use three different apps," "nothing talks to each other," "still using paper forms"
3. Data & visibility — "surprise charges," "couldn't give me a quote," "billing errors," "didn't know my history," "no record of my last visit"
4. Customer pipeline — "never called back," "left a message, no response," "ghosted," "no follow-up"
5. Team operations — "different person every time," "new guy didn't know what to do," "double-booked," "showed up on wrong day"

For each review, extract:
- The operational signal (which of the 5 solution categories, if any)
- The exact quote that shows it
- Severity (1-10)

Then give the business an overall operational pain score (1-10) based on the
pattern across all reviews. A single complaint is noise. Repeated patterns are signal.

Return JSON:
{
  "business_name": "...",
  "pain_score": 8,
  "top_problems": ["customer_pipeline", "tool_systems"],
  "evidence": [
    {"problem": "customer_pipeline", "quote": "Left two messages, never heard back", "severity": 8},
    ...
  ],
  "outreach_angle": "Their customers love their work but keep complaining about
                      communication gaps. Lead with: 'Your 5-star reviews mention
                      how hard it is to reach you -- we fix that.'"
}
```

**Step 4 — Route and record.** Filter for pain_score >= 7. Push qualified leads to a Google Sheet with columns: Business Name, Phone, Website, Category, Area, Pain Score, Top Problems, Evidence Quotes, Suggested Outreach Angle, Date Found. New leads appear in the daily digest email (see Daily Digest Scenario).

### Cost Estimate

| Component                                                  | Monthly Cost   |
| ---------------------------------------------------------- | -------------- |
| Google Places API (discovery, within free credit)          | $0             |
| Outscraper (500 businesses/week, recent reviews)           | $6-12          |
| Claude API (scoring ~100 review batches/week)              | $5-10          |
| Make.com (shared plan, ~2,000 ops/month for this pipeline) | Shared         |
| **Pipeline total**                                         | **~$11-22/mo** |

---

## Pipeline 2: Job Posting Monitor

**The idea:** Monitor job postings in Phoenix for roles that signal operational pain at small businesses. A 15-person plumbing company posting for an "office manager" is telling you they've outgrown their current operations. A contractor hiring a "dispatcher" has scheduling chaos. These are warm leads — they're actively trying to solve the problem.

### Data Flow

```
SerpAPI (Google Jobs)        Claude API                 Google Sheet
(daily job search)    -->   (analyze job desc)   -->   "Job Signal Leads"
       |                          |                          |
  Job listings              Qualification:               Google Sheet
  (title, company,          - Is this a small co?        "Job Signal Leads"
   description,             - Is the pain operational?
   location, date)          - Which of the 5 categories?
                            - Draft outreach angle
```

### How It Works

**Step 1 — Query job boards.** Use SerpAPI's Google Jobs endpoint. Google Jobs aggregates from Indeed, LinkedIn, ZipRecruiter, Glassdoor, and direct company postings — one API covers all major sources.

Monitored search terms (each run as a separate query):

- "office manager" in Phoenix, AZ
- "operations manager" in Phoenix, AZ
- "operations director" in Phoenix, AZ
- "dispatcher" in Phoenix, AZ
- "scheduling coordinator" in Phoenix, AZ
- "customer service coordinator" in Phoenix, AZ
- "office administrator" in Phoenix, AZ
- "front desk manager" in Phoenix, AZ
- "service coordinator" in Phoenix, AZ
- "IT manager" in Phoenix, AZ
- "systems administrator" in Phoenix, AZ
- "project coordinator" in Phoenix, AZ

Filter: `date_posted:3days` to catch recent postings. Run daily.

**Step 2 — Dedup and filter.** Check each posting against the Make.com Data Store (by job_id hash or title+company). Only process new postings.

**Step 3 — AI qualification.** Pass the job description to Claude:

```
You are analyzing a job posting to determine if it signals operational pain at
a growing business ($750k-$5M revenue range) that could benefit from solutions
consulting.

Job title: {title}
Company: {company}
Location: {location}
Description: {description}

Evaluate:
1. Company size/revenue signal — Does anything in the posting suggest this is a
   growing business in the $750k-$5M range? Look for: "small team," "growing
   company," single-location indicators, the job combining multiple responsibilities,
   signals of 10-75 employees.

2. Operational pain signal — Is this role being created because the business has
   outgrown its current operations? Look for: "we need someone to organize,"
   "create processes," "implement systems," "the owner currently handles,"
   "no existing structure," "manage our technology," "integrate our tools."

3. Which of the 5 solution categories does this map to?
   - process_design: Missing or undocumented processes
   - tool_systems: Tools don't connect, manual workarounds, technology gaps
   - data_visibility: No reporting, can't track metrics, financial blindness
   - customer_pipeline: Leads falling through, no CRM, no follow-up system
   - team_operations: Scheduling chaos, onboarding gaps, retention issues

4. Disqualify if: large company (200+ employees), franchise corporate office,
   staffing agency, government, or the role is a standard replacement hire
   with no operational pain signals.

Return JSON:
{
  "company": "...",
  "qualified": true/false,
  "confidence": "high/medium/low",
  "company_size_estimate": "20-50",
  "problems_signaled": ["process_design", "tool_systems"],
  "evidence": "Job description mentions 'owner currently handles all scheduling'
               and 'no existing processes documented'",
  "outreach_angle": "They're hiring to solve a problem that's really about
                      missing processes. Reach out before they hire: 'Before you
                      add payroll, let us build the solution that makes this role
                      half as big.'"
}
```

**Step 4 — Route qualified leads.** Push to the "Job Signal Leads" sheet. Appears in the daily digest email.

**The outreach timing advantage:** The company just posted the job. They're actively feeling the pain. Your outreach arrives while the wound is fresh — not 6 months later when they've already hired someone and forgotten how bad it was.

### Supplementary: Craigslist RSS

Free. Set up Make.com's RSS module to watch:

- `https://phoenix.craigslist.org/search/jjj?query=office+manager&format=rss`
- Same for other target job titles

Lower signal quality (anonymous postings, less company info) but catches the smallest businesses that only post on Craigslist. Run through the same AI qualification step.

### Cost Estimate

| Component                                                              | Monthly Cost                            |
| ---------------------------------------------------------------------- | --------------------------------------- |
| SerpAPI Google Jobs (~12 queries x 1 run/day x 30 days = 360 searches) | $50/mo (well within 5,000/mo base plan) |
| Craigslist RSS                                                         | $0                                      |
| Claude API (qualifying ~20-30 postings/day)                            | $10-15                                  |
| Make.com (shared plan, ~3,000 ops/month for this pipeline)             | Shared                                  |
| **Pipeline total**                                                     | **~$60-65/mo**                          |

---

## Pipeline 3: New Business Detection

**The idea:** Catch new and growing businesses from public records: business filings, commercial permits, tax license registrations. These are businesses in formation or expansion.

**Note:** This pipeline is deprioritized. New businesses filing paperwork are unlikely to be in the $750k-$5M revenue range yet. The signal is weaker for our current ICP. Keep the architecture documented but build this last, if at all. The pipeline is most useful for catching businesses expanding into new locations (commercial TI permits), which does signal growth.

### Data Flow

```
SODA API (city permits)         ACC/ADOR data              Claude API
(automated daily query)  +   (public records req)   -->  (qualify + enrich)
        |                          |                           |
  Commercial TI permits      New LLC/corp filings         Qualification:
  (business name, address,   New TPT licenses             - Is this our target?
   permit type, date)        (business name, address,     - What vertical?
                              entity type)                 - Outreach timing
                                                               |
                                                               v
                                                        Google Sheet
                                                      "New Business Leads"
```

### Automated Sources (no human needed)

**City Open Data Portals (SODA API):**

Phoenix, Scottsdale, and Chandler publish permit data via Socrata's SODA REST API. A commercial tenant improvement (TI) permit means a business is moving into or expanding a space — direct growth signal.

Make.com's HTTP module queries these directly. Example:

```
GET https://www.phoenixopendata.com/resource/{dataset-id}.json
  ?$where=issue_date > '2026-03-23'
  &$where=permit_type = 'Commercial TI'
  &$limit=100
```

Run daily for each city. Free. Fully automated.

**SBA Loan Data:**

Quarterly bulk CSV from data.sba.gov. Filter for Maricopa County zip codes. A business receiving an SBA loan is investing in growth. Download, parse, cross-reference with existing prospect list. Batch process quarterly.

### Semi-Automated Sources (human initiates, system processes)

**Arizona Corporation Commission (ACC):**

No API, but you can email a weekly public records request for new entity filings in Maricopa County. The ACC responds with a data extract (required under ARS 39-121). Once received, upload to a processing pipeline:

- Make.com watches a Gmail inbox or Google Drive folder for the weekly ACC file
- Parses the CSV/Excel
- Runs each new entity through AI qualification (is this a growing business in the $750k-$5M range? what vertical?)
- Pushes qualified entities to the prospect sheet

**Arizona Department of Revenue (ADOR) — TPT Licenses:**

Highest signal source — a new TPT license means the business is actually operating, not just filed paperwork. Same public records request approach as ACC, monthly cadence.

### Cost Estimate

| Component                                        | Monthly Cost             |
| ------------------------------------------------ | ------------------------ |
| SODA API queries (Phoenix, Scottsdale, Chandler) | $0                       |
| SBA data processing (quarterly)                  | $0                       |
| ACC/ADOR public records processing               | $0 (free public records) |
| Claude API (qualifying ~50 new entities/week)    | $3-5                     |
| Make.com (shared plan, ~1,500 ops/month)         | Shared                   |
| **Pipeline total**                               | **~$3-5/mo**             |

---

## Pipeline 4: Social Listening

**The idea:** Monitor local online communities for people asking questions that signal operational pain. "Does anyone know a good CRM?" "How do you handle scheduling for a team of 12?" "I'm drowning in paperwork." These are people raising their hand. Don't pitch — answer the question. The DMs come to you.

### Channels to Monitor

**Reddit:**

- r/phoenix, r/smallbusiness, r/entrepreneur
- Reddit has a free API (rate-limited but sufficient)
- Monitor for keywords: "small business Phoenix," "scheduling software," "CRM recommendation," "overwhelmed business owner," "can't take a day off"

**Facebook Groups:**

- Phoenix-area small business groups
- Industry-specific groups (Phoenix HVAC contractors, Arizona contractors, etc.)
- No API — manual monitoring or use a tool like PhantomBuster
- Highest signal but lowest automation potential

**Google Alerts:**

- Set up alerts for: "small business Phoenix operations," "Phoenix [vertical] hiring office manager"
- Delivers to Gmail → Make.com watches the inbox → parses the alert → routes interesting results

**Craigslist (services section):**

- Businesses posting in "services offered" → they exist and are marketing
- RSS feed: `https://phoenix.craigslist.org/search/bbs?format=rss`

### Automation Approach

This pipeline is the least automatable but the most authentic. The play is:

1. **Automated discovery:** Make.com surfaces the posts/threads worth looking at
2. **Human response:** You (the human) write a genuinely helpful answer
3. **No pitch in public:** The value is being visibly competent, not selling
4. **Track engagement:** Log which responses generate DMs or follows

Set up a Make.com scenario that:

- Queries Reddit API for keyword matches in target subreddits
- Watches a Google Alerts Gmail label for new alerts
- Deduplicates against Data Store
- Sends a daily Gmail digest: "Here are today's conversations worth joining"

You spend 15 minutes in the morning reading the digest and responding where you can add value.

### Cost Estimate

| Component                              | Monthly Cost |
| -------------------------------------- | ------------ |
| Reddit API                             | $0           |
| Google Alerts                          | $0           |
| Make.com (shared plan, ~500 ops/month) | Shared       |
| **Pipeline total**                     | **~$0**      |

---

## Pipeline 5: Referral Partner Nurture

**The idea:** Referral partner relationships are the highest-converting source. This includes bookkeepers/CPAs (the original 22 prospects), plus Vistage chairs, EO members, fractional CFOs, and commercial insurance agents. Relationships decay without maintenance. Automate the stay-in-touch cadence so the relationship stays warm even when you're heads-down on a client engagement.

### Automation Approach

This is the simplest pipeline — it's a CRM drip, not a data mining operation.

**Tracking:** Google Sheet with the referral partner list (started with bookkeepers, expanding to Vistage chairs, fractional CFOs, EO members). Add columns:

- Last Contact Date
- Next Check-in Date
- Relationship Stage (prospect / intro call done / active partner / dormant)
- Referrals Received (count)
- Referrals Sent (count)

**Make.com scenario (weekly, Friday morning):**

1. Read the Google Sheet
2. Filter for partners where Next Check-in Date <= today
3. For each partner due for check-in:
   - If relationship is "active partner": Draft a check-in email with something useful (a relevant article, a client win story, a question about their business)
   - If relationship is "intro call done" but no referrals yet: Draft a gentle touch
   - If relationship is "prospect" with no intro call: Draft a follow-up to the original outreach
4. Push drafts to Gmail draft folder for human review and send
5. Send a summary email to self: "3 partner check-ins ready in your drafts"

**The AI layer:** Claude drafts the check-in emails, personalized per partner based on their firm's focus areas and your relationship history. You review and send — never fully automated outreach.

**Email delivery:** Gmail for 1:1 partner nurture (truly personalized). Buttondown (existing subscription) reserved for future broadcast content — a monthly "operations insight" to the full partner list when the network reaches 10+ active relationships. See `docs/lead-automation/specs/buttondown-integration.md` for details.

### Cost Estimate

| Component                               | Monthly Cost |
| --------------------------------------- | ------------ |
| Google Sheets                           | $0           |
| Claude API (drafting ~5-10 emails/week) | $2-3         |
| Make.com (shared plan, ~200 ops/month)  | Shared       |
| **Pipeline total**                      | **~$2-3/mo** |

---

## The Make.com Architecture

### Scenario Map

| Scenario         | Trigger     | Schedule                                    | Est. Ops/Month |
| ---------------- | ----------- | ------------------------------------------- | -------------- |
| Review Discovery | Scheduled   | Monthly (rebuild business list)             | 500            |
| Review Scan      | Scheduled   | Weekly (pull new reviews, score)            | 1,500          |
| Job Monitor      | Scheduled   | Daily (query SerpAPI, qualify)              | 2,500          |
| City Permits     | Scheduled   | Daily (SODA API query)                      | 800            |
| ACC/ADOR Intake  | Gmail watch | On new email (process filings)              | 500            |
| Social Digest    | Scheduled   | Daily (Reddit + Google Alerts → email)      | 500            |
| Partner Nurture  | Scheduled   | Weekly (check-ins → Gmail drafts)           | 200            |
| Daily Digest     | Scheduled   | Daily 6:30am (morning email, all pipelines) | 1,500          |
| **Total**        |             |                                             | **~8,000/mo**  |

The Make.com Pro plan ($16/mo, 10,000 ops) covers this. If volume grows, additional ops are ~$9/10K.

### Shared Infrastructure

**Make.com Data Store:** Single data store with tables for:

- `seen_businesses` (place_id → prevents reprocessing)
- `seen_jobs` (job_id_hash → deduplication)
- `seen_permits` (permit_number → deduplication)
- `seen_social` (post_id → deduplication)

**Google Sheets (output hub):**

- "Review Signal Leads" — businesses with operational pain in their reviews
- "Job Signal Leads" — companies hiring ops roles
- "New Business Leads" — new filings and permits
- "Social Opportunities" — threads worth responding to
- "Referral Partners" — bookkeeper/CPA, Vistage chairs, fractional CFOs, EO members (already exists)
- "Master Pipeline" — consolidated view of all qualified leads across sources

**Daily Digest Email:** A single morning email via Gmail summarizing all new leads across all pipelines from the past 24 hours. Google Sheets are the dashboard — the digest is a daily nudge to check them.

---

## Total Cost Model

| Component                                            | Monthly Cost     |
| ---------------------------------------------------- | ---------------- |
| Make.com Pro                                         | $16              |
| SerpAPI (Google Jobs, 5K searches/mo)                | $50              |
| Outscraper (review extraction)                       | $20-50           |
| Claude API (all pipelines)                           | $20-33           |
| Google Places API (within $200 free credit)          | $0               |
| Reddit API, Craigslist RSS, Google Alerts, SODA APIs | $0               |
| **Total**                                            | **~$106-149/mo** |

For context: one closed engagement at our average price point covers a full year of this entire system. The ROI math is almost absurd: the system needs to surface ONE client per year to pay for itself many times over.

---

## Implementation Roadmap

### Phase 1: Highest Signal, Lowest Effort (Week 1-2)

Build Pipeline 2 (Job Posting Monitor) first. Why:

- SerpAPI is a single API with excellent docs
- The Make.com scenario is straightforward (HTTP → Iterator → Claude → Filter → Sheets)
- Job postings are the strongest intent signal — these companies are actively trying to solve the problem
- Daily cadence means you see results immediately

Also set up Pipeline 4 (Social Listening) — Google Alerts and Reddit monitoring are near-zero effort and provide immediate daily content.

**Deliverables:**

- [ ] SerpAPI account, Google Jobs endpoint tested
- [ ] Make.com scenario: Job Monitor (8 queries → dedup → Claude qualify → Sheets)
- [ ] Google Alerts configured for target keywords
- [ ] Make.com scenario: Social Digest (Reddit API + Gmail alerts → daily email digest)
- [ ] Make.com scenario: Daily Digest (morning email summarizing all new leads)
- [ ] Google Sheets created: "Job Signal Leads," "Social Opportunities"

### Phase 2: The Review Engine (Week 3-4)

Build Pipeline 1 (Review Mining). This is the most complex pipeline but the most defensible advantage.

**Deliverables:**

- [ ] Outscraper account, review extraction tested
- [ ] Google Places API: business discovery queries built for each target vertical
- [ ] Make.com scenario: Review Discovery (monthly business list refresh)
- [ ] Make.com scenario: Review Scan (weekly review pull → Claude scoring → Sheets)
- [ ] Google Sheet: "Review Signal Leads"
- [ ] Claude prompt tuned on real review data (test against known businesses)

### Phase 3: Public Records (Week 5-6)

Build Pipeline 3 (New Business Detection). Start with the automated sources, add semi-automated later.

**Deliverables:**

- [ ] SODA API endpoints identified for Phoenix, Scottsdale, Chandler commercial permits
- [ ] Make.com scenario: City Permits (daily SODA query → filter commercial TIs → Sheets)
- [ ] First ACC public records request submitted (test the process)
- [ ] First ADOR public records request submitted
- [ ] Make.com scenario: ACC/ADOR Intake (Gmail watch → parse → qualify → Sheets)
- [ ] Google Sheet: "New Business Leads"

### Phase 4: Nurture System (Week 7-8)

Build Pipeline 5 (Referral Partner Nurture). By now you have active bookkeeper relationships from manual outreach — automate the maintenance.

**Deliverables:**

- [ ] Bookkeeper prospect sheet enhanced with check-in tracking columns
- [ ] Make.com scenario: Partner Nurture (weekly → identify due check-ins → Claude draft → Gmail drafts → summary email)

### Phase 5: Optimization (Ongoing)

- Tune Claude prompts based on real qualification results (are the scores accurate?)
- Adjust query terms based on what's actually surfacing
- Build the "Master Pipeline" consolidated view
- Add company enrichment (Apollo.io or manual) for size/revenue filtering
- Track source attribution: which pipeline produces the leads that actually close?

---

## The Competitive Moat

This system compounds. Every week:

- The business discovery list grows (more businesses being monitored for reviews)
- The dedup store grows (less wasted processing)
- The Claude prompts improve (tuned on real data)
- The referral partner network deepens (automated nurture)
- You learn which signals actually predict closed deals (feedback loop)

In 6 months, you have a lead generation machine that no other local operations consultant can replicate — not because the technology is hard, but because the system knowledge and prompt tuning took hundreds of iterations to get right.

The manual channels (BNI, chamber, Vistage/EO, LinkedIn) are still essential. They're how you build trust and close deals. But the automated pipelines ensure you're never short on conversations to have. The 15-20 touches/week target stops being a grind and starts being a selection problem: which of these qualified leads do I reach out to first?

---

## Technical Notes

### Make.com Operations Budget

Operations are the main constraint. Each module execution = 1 operation. A scenario with 10 modules processing 20 items = 200 operations. The Pro plan (10K ops/month) covers the baseline architecture. Monitor usage and purchase additional op packs ($9/10K) as volume scales.

**Optimization tips:**

- Use filters (free, don't count as operations) aggressively before expensive modules (AI calls)
- Batch review text into single Claude calls where possible (score 5-10 businesses per prompt)
- Use the Data Store "exists" check before API calls to avoid reprocessing

### API Key Management

- SerpAPI key → Make.com connection
- Outscraper API key → Make.com connection
- Google Places API key → Make.com HTTP module (restrict to Places API in Google Cloud Console)
- Anthropic API key → Make.com Anthropic module
- Reddit API credentials (OAuth2 app) → Make.com HTTP module

Store all keys in Make.com's connection manager. Never hardcode in scenario configurations.

### Data Retention

Google Sheets is the initial output layer. If volume grows or you want structured querying, migrate to D1 (already in your stack) with a Cloudflare Worker API that Make.com calls via HTTP. But Sheets is fine for the first 6 months — don't over-engineer the storage layer before you've validated the pipelines.

---

## Implementation Files

The blueprint is the architecture document. The implementation details live in:

### Prompts & Schemas (`src/lead-gen/`)

TypeScript prompt library following the `src/portal/assessments/extraction-prompt.ts` pattern. Each prompt exports a system prompt, user prompt builder, manual prompt builder, and validation function.

- `schemas/lead-scoring-schema.ts` — Shared types (PipelineId, ScoringResult, LeadRecord)
- `schemas/review-signal.ts` — Pipeline 1 output types
- `schemas/job-signal.ts` — Pipeline 2 output types
- `schemas/new-business-signal.ts` — Pipeline 3 output types
- `schemas/partner-email-draft.ts` — Pipeline 5 output types
- `prompts/review-scoring-prompt.ts` — Pipeline 1 AI scoring
- `prompts/job-qualification-prompt.ts` — Pipeline 2 AI qualification
- `prompts/new-business-prompt.ts` — Pipeline 3 AI qualification
- `prompts/partner-nurture-prompt.ts` — Pipeline 5 email drafting

### Specifications (`docs/lead-automation/specs/`)

- `google-sheets-schema.md` — Column specs for all 6 output sheets
- `make-data-store-schema.md` — Deduplication table definitions
- `serpapi-queries.md` — Pipeline 2 API query specs
- `outscraper-queries.md` — Pipeline 1 API query specs
- `soda-api-queries.md` — Pipeline 3 city permit API specs
- `buttondown-integration.md` — Pipeline 5 email delivery spec
- `api-accounts-checklist.md` — All external account signups

### Make.com Recipes (`docs/lead-automation/recipes/`)

Step-by-step build guides for each Make.com scenario:

- `pipeline-1-review-mining.md` — Two scenarios (discovery + scan)
- `pipeline-2-job-monitor.md` — Reference pattern for all pipelines
- `pipeline-3-new-business.md` — Two scenarios (permits + ACC/ADOR intake)
- `pipeline-4-social-listening.md` — Daily digest scenario
- `pipeline-5-partner-nurture.md` — Weekly check-in scenario

### GitHub Issues

- #105 — Prompt library + shared schemas
- #106 — Pipeline 2: Job Posting Monitor
- #107 — Pipeline 1: Review Mining
- #108 — Pipeline 3: New Business Detection
- #109 — Pipeline 4: Social Listening
- #110 — Pipeline 5: Partner Nurture + Buttondown
- #111 — API accounts, Make.com setup, Gmail digest

# API Accounts & Configuration Checklist

External accounts, API keys, and configurations required to run all 5 lead generation pipelines. Ordered by priority: Pipeline 2 (Job Monitor) first, since it has the fewest dependencies and is the fastest to validate end-to-end.

**Estimated time:** 30-45 minutes for all account setup and configuration.

---

## Order of Operations

1. **Verify accounts you already have** (5-10 min)
2. **Sign up for new services** (5-10 min)
3. **Configure Google Cloud** if needed (5-10 min)
4. **Create Make.com connections** (5-10 min)
5. **Set up Google Sheets** (5 min)
6. **Create Make.com Data Stores** (5 min)

Pipeline 2 (Job Monitor) requires only: SerpAPI + Anthropic + Google Sheets + Gmail. Complete those first and validate the pipeline before configuring the remaining accounts.

---

## 1. Verify Existing Accounts

These accounts should already exist. Verify access and confirm API keys are available.

- [ ] **Make.com** — Pro plan required ($16/mo, 10K operations).
  - Log in and check **Settings → Subscription** for current plan level.
  - If on Free or Core, upgrade to Pro.
  - Signup: [make.com](https://make.com)

- [ ] **Anthropic API** — Claude API access for AI qualification and scoring.
  - Log in to your Anthropic account and verify an API key exists.
  - Check usage limits and billing status.
  - Console: [console.anthropic.com](https://console.anthropic.com)

- [ ] **Google Cloud** — For Places API (Pipeline 1).
  - Check if a project already exists at [console.cloud.google.com](https://console.cloud.google.com).
  - If a project exists, verify the Places API is enabled (see Google Cloud setup below).
  - If no project exists, you'll create one in step 3.

- [ ] **Buttondown** — For Pipeline 5 (Nurture Sequences).
  - Log in and go to **Settings → API** to verify your API key.
  - Signup: [buttondown.com](https://buttondown.com)

---

## 2. New Account Signups

These are new services that need to be created.

- [ ] **SerpAPI** — Google Jobs search results API. Used by Pipeline 2 (Job Monitor).
  - Sign up at [serpapi.com](https://serpapi.com).
  - Select the Developer plan ($50/mo, 5,000 searches).
  - Copy your API key from the dashboard after signup.

- [ ] **Outscraper** — Google Maps/Reviews scraping API. Used by Pipeline 1 (Review Mining).
  - Sign up at [outscraper.com](https://outscraper.com).
  - Select Medium plan ($49/mo) or pay-as-you-go (~$20-50/mo depending on volume).
  - Copy your API key from the dashboard after signup.

---

## 3. Google Cloud Setup

Skip this section if the Places API is already enabled on an existing project.

- [ ] **Create a Google Cloud project** (or select an existing one).
  - Go to [console.cloud.google.com](https://console.cloud.google.com).
  - Click the project dropdown → **New Project**.
  - Name it something identifiable (e.g., "SMD Lead Gen").

- [ ] **Enable the Places API (New)**.
  - In the project, go to **APIs & Services → Library**.
  - Search for "Places API (New)" and click **Enable**.

- [ ] **Create an API key**.
  - Go to **APIs & Services → Credentials**.
  - Click **Create Credentials → API Key**.
  - Restrict the key to the Places API only (under **API restrictions**).

- [ ] **Verify free credit**.
  - Google Cloud provides $200/mo in free credit for the Maps Platform.
  - Confirm this is active under **Billing → Overview**.

---

## 4. Make.com Connections

Configure these connections inside Make.com at **Settings → Connections → Add**.

### Priority (needed for Pipeline 2):

- [ ] **Anthropic connection**
  - Settings → Connections → Add → search "Anthropic" (or use HTTP module).
  - Paste your Anthropic API key.
  - If no native Anthropic module exists, use an HTTP module with the API key in the `x-api-key` header.

- [ ] **Google Sheets connection**
  - Settings → Connections → Add → search "Google Sheets".
  - Authenticate via OAuth with the Google account that owns the lead gen spreadsheet.

### After Pipeline 2 is validated:

- [ ] **SerpAPI connection**
  - No native Make.com module. Use an **HTTP → Make a request** module.
  - Pass the API key as a query parameter: `api_key={your_key}`.

- [ ] **Outscraper connection**
  - No native Make.com module. Use an **HTTP → Make a request** module.
  - Pass the API key in the `Authorization` header or as a query parameter per Outscraper's docs.

- [ ] **Google Places API connection**
  - No native Make.com module. Use an **HTTP → Make a request** module.
  - Pass the API key as a query parameter: `key={your_key}`.

- [ ] **Gmail connection**
  - Settings → Connections → Add → search "Gmail".
  - Authenticate via OAuth.
  - Used for Pipeline 3 (ACC/ADOR email intake), Pipeline 5 (outreach drafts), and the daily lead digest email sent each morning summarizing new finds across all pipelines.

- [ ] **Buttondown connection**
  - No native Make.com module. Use an **HTTP → Make a request** module.
  - Pass the API key in the `Authorization` header: `Token {your_key}`.

- [ ] **SODA API connections** (for Pipeline 3 open data portals)
  - No auth required for public SODA endpoints, but register an app token for higher rate limits.
  - Use **HTTP → Make a request** modules.
  - Pass the app token as a query parameter: `$$app_token={your_token}`.

---

## 5. Google Sheets Setup

- [ ] **Create a new Google Sheet** named "SMD Lead Generation".

- [ ] **Add 6 tabs** to the sheet (see `google-sheets-schema.md` for detailed column specs):
  - `Pipeline 1 — Review Mining`
  - `Pipeline 2 — Job Monitor`
  - `Pipeline 3 — New Business`
  - `Pipeline 4 — Social Listening`
  - `Pipeline 5 — Nurture`
  - `Master Lead List`

- [ ] **Share the sheet** with the Google account connected to Make.com (Editor access).

---

## 6. Make.com Data Stores

- [ ] **Create 4 Data Stores** as defined in `make-data-store-schema.md`:
  - `seen_businesses` (Pipeline 1 dedup)
  - `seen_jobs` (Pipeline 2 dedup)
  - `seen_permits` (Pipeline 3 dedup)
  - `seen_social` (Pipeline 4 dedup)

See [make-data-store-schema.md](make-data-store-schema.md) for full table schemas, field definitions, and cleanup rules.

---

## Quick Reference: What Each Pipeline Needs

| Pipeline                       | External Services                                        |
| ------------------------------ | -------------------------------------------------------- |
| Pipeline 1 — Review Mining     | Outscraper, Google Places API, Anthropic, Sheets, Gmail  |
| Pipeline 2 — Job Monitor       | SerpAPI, Anthropic, Sheets, Gmail                        |
| Pipeline 3 — New Business      | SODA APIs, Gmail, Anthropic, Sheets                      |
| Pipeline 4 — Social Listening  | Reddit (no auth), Google Alerts (Gmail), Sheets          |
| Pipeline 5 — Nurture Sequences | Buttondown, Gmail, Sheets                                |
| All Pipelines                  | Make.com (Pro), Google Sheets, Make.com Data Stores      |
| Daily Digest (cross-pipeline)  | Gmail (one morning summary email of all new leads found) |

Start with Pipeline 2. It has the fewest dependencies and the fastest path to a working end-to-end test.

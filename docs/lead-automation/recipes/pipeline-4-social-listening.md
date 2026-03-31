# Make.com Recipe: Pipeline 4 — Social Listening

**Purpose:** Step-by-step guide to build a Make.com scenario that monitors Reddit and Google Alerts for conversations relevant to Phoenix-area small business operations, then delivers a curated daily digest via Gmail.

**Prerequisites:**

- Reddit API credentials (OAuth2 app — see setup instructions below)
- Google Alerts configured and delivering to Gmail with a specific label
- Gmail connection in Make.com
- Make.com Data Store: `seen_social` created (see make-data-store-schema.md)

**No Google Sheets output for this pipeline.** The daily digest is sent via Gmail for human review. The human decides which conversations to engage with. If volume warrants tracking responses, add the "Social Opportunities" sheet later (see google-sheets-schema.md, Sheet 4).

---

## Reddit OAuth2 Setup

Before building the scenario, register a Reddit API application:

1. Go to https://www.reddit.com/prefs/apps
2. Scroll to the bottom and click **"create another app..."**
3. Fill in:
   - **name:** `smd-lead-monitor` (or any name)
   - **type:** Select **"script"** (for personal use / server-side)
   - **description:** (optional)
   - **about url:** (leave blank)
   - **redirect uri:** `https://www.make.com/oauth/cb/reddit` (or any valid URL — script apps don't use redirects, but the field is required)
4. Click **"create app"**
5. Note two values:
   - **client_id:** The string under the app name (e.g., `aBcDeFgHiJkLmN`)
   - **client_secret:** The string labeled "secret"
6. Store these securely — you will use them in the Make.com scenario to obtain an access token

**Rate limits:** Reddit's API allows 60 requests per minute for OAuth-authenticated apps. Our scenario makes ~10 requests per run — well within limits.

---

## Google Alerts Setup

Set up Google Alerts to deliver keyword-matched web content to Gmail:

1. Go to https://www.google.com/alerts
2. Create alerts for these queries:
   - `"small business" Phoenix operations`
   - `"business owner" Phoenix "overwhelmed"`
   - `"office manager" Phoenix hiring`
   - `"scheduling software" small business`
   - `"CRM" recommendation small business`
3. For each alert:
   - **How often:** As-it-happens (or Once a day)
   - **Sources:** Automatic
   - **Language:** English
   - **Region:** United States
   - **How many:** Only the best results
   - **Deliver to:** Your Gmail address
4. In Gmail, create a label: `Google-Alerts`
5. Create a Gmail filter: From `googlealerts-noreply@google.com` -> Apply label `Google-Alerts`

---

## Scenario: Daily Social Digest

### Scenario Overview

```
Schedule (daily 7am)
  -> Router: 2 paths
    Path 1: Reddit API
      -> HTTP: get OAuth2 access token
      -> Set Variable: Reddit search queries
      -> Iterator: each query
        -> HTTP: Reddit search
        -> Iterator: each post
          -> Data Store: check seen_social (dedup by Reddit post ID)
          -> Filter: only new posts
          -> Aggregator: collect all new posts
    Path 2: Google Alerts
      -> Gmail: search for new emails (label "Google-Alerts", since yesterday)
        -> Iterator: each email
          -> Text Parser: extract URL and subject from alert email
          -> Data Store: check seen_social (dedup)
          -> Filter: only new alerts
          -> Aggregator: collect all new alerts
  -> (after both paths merge:)
  -> Text Aggregator: combine all results into a single digest
  -> Gmail: send daily digest email
```

**Total modules:** ~15-20
**Expected daily volume:** 5-30 posts/alerts across all sources. Many days will be quiet.
**Operations per run:** ~100-300

---

### Step-by-Step Build

#### Step 1: Create the Scenario

1. In Make.com -> Scenarios -> Create a new scenario
2. Name: `Lead Gen: P4 Social Listening`
3. Set the schedule: **Every day at 7:00 AM** (MST/Arizona time)

#### Step 2: Add the Trigger — Scheduled

1. The scenario trigger is **Schedule** (built-in)
2. Set to run once daily at 7:00 AM

#### Step 3: Module 1 — Router (2 Paths)

**Module: Flow Control -> Router**

Add 2 routes:

- Route 1: "Reddit Search"
- Route 2: "Google Alerts"

Both paths run in parallel and their results merge at the end into a single digest.

---

### Path 1: Reddit Search

#### Step 4: Module 2 — HTTP (Reddit OAuth2 Token)

Reddit's API requires an OAuth2 access token. For "script" type apps, use the password grant flow.

**Module: HTTP -> Make a request**

| Setting        | Value                                        |
| -------------- | -------------------------------------------- |
| URL            | `https://www.reddit.com/api/v1/access_token` |
| Method         | POST                                         |
| Headers        | See below                                    |
| Body type      | `application/x-www-form-urlencoded`          |
| Parse response | Yes                                          |

**Headers:**

| Header          | Value                                                             |
| --------------- | ----------------------------------------------------------------- |
| `Authorization` | `Basic {{base64(REDDIT_CLIENT_ID + ":" + REDDIT_CLIENT_SECRET)}}` |
| `User-Agent`    | `smd-lead-monitor/1.0 by SMDServices`                             |

**Body fields:**

| Key          | Value                 |
| ------------ | --------------------- |
| `grant_type` | `password`            |
| `username`   | `{{REDDIT_USERNAME}}` |
| `password`   | `{{REDDIT_PASSWORD}}` |

**Note:** The `Basic` auth header is the base64-encoded string `client_id:client_secret`. In Make.com, use the `base64` function or manually encode the credentials and paste the result.

The response contains an `access_token` field valid for ~1 hour. Store it in a variable for use in subsequent Reddit API calls.

**Set Variable after this module:**

- `reddit_token` = `{{HTTP.body.access_token}}`

#### Step 5: Module 3 — Set Variable (Reddit Queries)

**Module: Tools -> Set multiple variables**

- Variable: `reddit_queries` (array)
- Value:

```json
[
  "small business Phoenix",
  "CRM recommendation",
  "scheduling software small business",
  "overwhelmed business owner",
  "office manager hiring Phoenix"
]
```

These queries target the intersection of small business operational pain and the Phoenix market. Generic queries ("CRM recommendation") cast a wider net for general industry conversations worth joining.

#### Step 6: Module 4 — Iterator (Reddit Queries)

**Module: Flow Control -> Iterator**

- Source array: `{{reddit_queries}}`

#### Step 7: Module 5 — HTTP (Reddit Search)

**Module: HTTP -> Make a request**

| Setting          | Value                             |
| ---------------- | --------------------------------- |
| URL              | `https://oauth.reddit.com/search` |
| Method           | GET                               |
| Headers          | See below                         |
| Query parameters | See below                         |
| Parse response   | Yes                               |

**Headers:**

| Header          | Value                                 |
| --------------- | ------------------------------------- |
| `Authorization` | `Bearer {{reddit_token}}`             |
| `User-Agent`    | `smd-lead-monitor/1.0 by SMDServices` |

**Query parameters:**

| Key           | Value                                                  |
| ------------- | ------------------------------------------------------ |
| `q`           | `{{iterator.value}}` (the current search query)        |
| `restrict_sr` | `false` (search all of Reddit, not just one subreddit) |
| `type`        | `link` (posts only, not comments)                      |
| `t`           | `day` (last 24 hours)                                  |
| `limit`       | `10` (top 10 results per query)                        |

**Error handling:** Add an error route -> Resume (if Reddit API returns an error, skip this query)

**Subreddit filtering:** The search returns results from all subreddits. To filter for relevant subreddits, add a filter after the next Iterator that checks if `subreddit` is in the target list: `r/phoenix`, `r/smallbusiness`, `r/entrepreneur`, `r/smallbusinessowner`. Alternatively, skip the filter and let all subreddits through — the human reviewing the digest will make the relevance call.

#### Step 8: Module 6 — Iterator (Reddit Posts)

**Module: Flow Control -> Iterator**

- Source array: `{{HTTP.body.data.children}}` (Reddit's API wraps posts in `data.children`)

#### Step 9: Module 7 — Data Store (Dedup Check)

**Module: Data Store -> Get a record**

- Data store: `seen_social`
- Key: `{{iterator2.data.id}}` (Reddit's post ID, e.g., `t3_abc123`)

Reddit post IDs are globally unique — they are the natural dedup key.

#### Step 10: Filter — Only New Posts

**Add a filter:**

- Condition: Data Store module **did NOT return a record**
- Label: "New post only"

#### Step 11: Module 8 — Data Store (Mark as Seen)

**Module: Data Store -> Add/replace a record**

- Data store: `seen_social`
- Key: `{{iterator2.data.id}}`
- Fields:

| Field        | Value                               |
| ------------ | ----------------------------------- |
| `post_id`    | `{{iterator2.data.id}}`             |
| `platform`   | `reddit`                            |
| `date_found` | `{{formatDate(now; "YYYY-MM-DD")}}` |

#### Step 12: Module 9 — Text Aggregator (Collect Reddit Posts)

**Module: Tools -> Text aggregator**

- Source module: Filter (new posts only)
- Row separator: `\n`
- Text template:

```
{{counter}}. [Reddit/r/{{iterator2.data.subreddit}}] {{iterator2.data.title}}
   {{iterator2.data.url}}
   {{substring(iterator2.data.selftext; 0; 150)}}...
```

This collects all new Reddit posts into a single text block, numbered, with subreddit, title, URL, and a brief snippet of the post body.

---

### Path 2: Google Alerts

#### Step 13: Module 10 — Gmail (Search for Alert Emails)

**Module: Gmail -> Search Emails**

| Setting                   | Value                                                  |
| ------------------------- | ------------------------------------------------------ |
| Label                     | `Google-Alerts`                                        |
| Search query              | `after:{{formatDate(addDays(now; -1); "YYYY/MM/DD")}}` |
| Maximum number of results | 20                                                     |

This finds all Google Alert emails received in the past 24 hours.

#### Step 14: Module 11 — Iterator (Each Alert Email)

**Module: Flow Control -> Iterator**

- Source array: The emails returned by the Gmail Search module

#### Step 15: Module 12 — Text Parser (Extract Alert Data)

**Module: Text Parser -> Match Pattern**

Google Alert emails contain the alert subject and a list of matching URLs. Extract the key data:

- **Pattern for URL:** `href="(https?://[^"]+)"` (extracts the first linked URL from the email body)
- **Pattern for title/subject:** Use the email subject line: `{{iterator.subject}}`

**Alternative approach:** If the email body is HTML, use the **HTML -> Extract from HTML** module to pull the first link and its anchor text.

**Set variables:**

- `alert_url` = extracted URL from the email body
- `alert_title` = `{{iterator.subject}}`
- `alert_source` = `Google Alert`

#### Step 16: Module 13 — Data Store (Dedup Check)

**Module: Data Store -> Get a record**

- Data store: `seen_social`
- Key: `{{sha256(alert_title + "|" + alert_url)}}` (hash of subject + URL for uniqueness)

If `sha256` is not available, use `md5` or a simpler concatenation key.

#### Step 17: Filter — Only New Alerts

**Add a filter:**

- Condition: Data Store module **did NOT return a record**
- Label: "New alert only"

#### Step 18: Module 14 — Data Store (Mark as Seen)

**Module: Data Store -> Add/replace a record**

- Data store: `seen_social`
- Key: Same hash as Step 16
- Fields:

| Field        | Value                               |
| ------------ | ----------------------------------- | ----------------- |
| `post_id`    | `{{sha256(alert_title + "           | " + alert_url)}}` |
| `platform`   | `google_alerts`                     |
| `date_found` | `{{formatDate(now; "YYYY-MM-DD")}}` |

#### Step 19: Module 15 — Text Aggregator (Collect Alerts)

**Module: Tools -> Text aggregator**

- Source module: Filter (new alerts only)
- Row separator: `\n`
- Text template:

```
{{counter}}. [Google Alert] {{alert_title}}
   {{alert_url}}
```

---

### Merge and Deliver

#### Step 20: Module 16 — Set Variable (Combine Digests)

After both Router paths complete, combine the Reddit and Google Alert text blocks.

**Module: Tools -> Set multiple variables**

- Variable: `digest`
- Value:

```
*Reddit*
{{reddit_aggregator_text}}

*Google Alerts*
{{alerts_aggregator_text}}
```

If either section is empty, note that in the output:

```
*Reddit*
{{ifempty(reddit_aggregator_text; "(No new Reddit posts today)")}}

*Google Alerts*
{{ifempty(alerts_aggregator_text; "(No new Google Alerts today)")}}
```

#### Step 21: Filter — Only Send if Content Exists

**Add a filter before the Gmail module:**

- Condition: The combined digest is NOT empty (at least one new Reddit post or Google Alert exists)
- Label: "Has content"

This prevents sending an empty digest on quiet days.

#### Step 22: Module 17 — Gmail (Send Daily Digest Email)

**Module: Gmail -> Send an Email**

| Setting      | Value                                               |
| ------------ | --------------------------------------------------- |
| To           | Your business email address                         |
| Subject      | `Social Digest — {{formatDate(now; "YYYY-MM-DD")}}` |
| Content      | See below                                           |
| Content type | Plain text                                          |

**Email body:**

```
Social Digest — {{formatDate(now; "YYYY-MM-DD")}}

REDDIT
{{ifempty(reddit_aggregator_text; "(No new Reddit posts today)")}}

GOOGLE ALERTS
{{ifempty(alerts_aggregator_text; "(No new Google Alerts today)")}}

Review these conversations. Reply to relevant threads where SMD Services can add value. Do NOT pitch — contribute genuinely useful advice.
```

---

## Testing Checklist

- [ ] Run scenario manually (right-click -> Run once)
- [ ] Verify Reddit OAuth2 token is obtained (check first HTTP module output for `access_token`)
- [ ] Verify Reddit search returns results (check Reddit search HTTP module output)
- [ ] Verify dedup works: run twice, second run should skip all previously seen posts
- [ ] Verify Google Alert emails are picked up by the Gmail module
- [ ] Verify alert URLs and titles are extracted correctly from email HTML
- [ ] Verify the combined digest arrives via email with proper formatting
- [ ] Verify empty days produce no email (or a "no new content" message, depending on filter choice)
- [ ] Check operations count — should be within estimated 100-300 per run
- [ ] Let scenario run automatically for 5+ days, then review the digests for relevance and signal quality

---

## Tuning

After 1-2 weeks of running:

1. **Too much noise (irrelevant Reddit posts):** Narrow the search queries. Remove generic queries like "CRM recommendation" if they produce too many irrelevant results. Add subreddit filtering (Step 7 note) to restrict to `r/phoenix`, `r/smallbusiness`, `r/entrepreneur`, `r/smallbusinessowner`.

2. **Too few results:** Add more search queries. Consider adding `"business process" help`, `"QuickBooks" "small business"`, `"employee scheduling"`, or `"growing too fast"`. Also add more Google Alerts for emerging pain-point keywords.

3. **Reddit API errors (401 Unauthorized):** The OAuth2 token expires after ~1 hour. Since the scenario runs once daily, a fresh token is fetched each run. If you see 401 errors, verify the client_id, client_secret, username, and password are correct. Reddit may also require 2FA to be disabled for "script" type apps.

4. **Google Alerts not arriving:** Verify the alerts are configured at google.com/alerts and delivering to your Gmail. Check that the `Google-Alerts` label is being applied by the Gmail filter.

5. **Digest too long:** Reduce the `limit` parameter in Reddit search from 10 to 5. Shorten the snippet length from 150 characters to 80. Cap the digest at 20 items total by adding a `head_limit` to the aggregators.

6. **Want to track responses:** Add the "Social Opportunities" sheet (google-sheets-schema.md, Sheet 4) and an additional path after the digest: Google Sheets -> Add a Row for each item in the digest. This enables tracking which conversations were engaged and what happened.

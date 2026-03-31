# Buttondown Integration — Pipeline 5 (Partner Nurture)

**Purpose:** Specification for using Buttondown as the email delivery layer for referral partner nurture emails and future content distribution.

**Account:** Existing Buttondown subscription.
**API Base:** `https://api.buttondown.email`
**Docs:** api.buttondown.email

---

## How Buttondown Fits

### Role 1: Transactional Partner Nurture (Pipeline 5 — Now)

Pipeline 5's weekly cycle:

1. Make.com identifies referral partners due for a check-in (from Google Sheet)
2. Claude drafts a personalized email per partner
3. **Make.com creates a Buttondown email draft** via API
4. Human reviews drafts in Buttondown's dashboard
5. Human approves → Buttondown sends the email

**Why Buttondown over raw Gmail:**

- Better deliverability (proper DKIM/SPF/DMARC via Buttondown's infrastructure)
- Automatic unsubscribe footer (CAN-SPAM compliance)
- Open/click tracking (know which partners engage)
- Clean separation: Gmail for 1:1 conversation, Buttondown for structured outreach
- Archive of all sent communications

### Role 2: Broadcast Content (Future — Not in Initial Build)

A monthly "operations insight" email to all referral partners and warm contacts. Positions SMD Services as the operations expert in the Phoenix market. This requires having enough partner relationships and content ideas — which come from running the pipelines for a few weeks first.

**Not building this now.** But the subscriber list and Buttondown configuration established through Pipeline 5 provide the infrastructure. When ready, this is just a new Buttondown email with `status: "draft"` targeting the full subscriber list.

---

## Buttondown API — Key Endpoints

### Authentication

```
Authorization: Token {BUTTONDOWN_API_KEY}
```

Find your API key at: Buttondown dashboard → Settings → API.

### Create a Subscriber

Add a referral partner to Buttondown's subscriber list.

```
POST https://api.buttondown.email/v1/subscribers
Content-Type: application/json
Authorization: Token {BUTTONDOWN_API_KEY}

{
  "email": "devin@whytecpapc.com",
  "metadata": {
    "firm_name": "Whyte CPA Tax & Accounting",
    "contact_name": "Devin Whyte",
    "tier": 1,
    "relationship_stage": "prospect",
    "source": "bookkeeper_prospect_list"
  },
  "tags": ["referral-partner", "tier-1"]
}
```

**Tags to use:**

- `referral-partner` — all bookkeeper/CPA partners
- `tier-1`, `tier-2`, `tier-3` — from the prospect list
- `active-partner` — currently referring
- `prospect` — not yet contacted or early stage

### Create an Email Draft

Create an email that appears in Buttondown's dashboard for human review.

```
POST https://api.buttondown.email/v1/emails
Content-Type: application/json
Authorization: Token {BUTTONDOWN_API_KEY}

{
  "subject": "Quick check-in from the SMD team",
  "body": "Hi Devin — Hope the quarter is going well...",
  "status": "draft",
  "email_type": "public"
}
```

**Status options:**

- `"draft"` — appears in dashboard for review (USE THIS for Pipeline 5)
- `"about_to_send"` — queued for immediate send (use only after human approval)

**For 1:1 partner emails:** Buttondown doesn't natively support sending to a single subscriber via the email endpoint (emails go to all subscribers or a tag group). For truly 1:1 transactional emails, options:

1. **Tag-based targeting:** Tag each partner uniquely (e.g., `partner-whyte-cpa`) and send to that tag. Works but creates tag sprawl.
2. **Use Buttondown's "emails to specific subscribers" feature** if available in your plan tier.
3. **Alternative:** Use Buttondown for the broadcast/newsletter use case (Role 2) and Gmail for 1:1 nurture. Make.com creates Gmail drafts instead.

**Recommendation:** Start with Gmail drafts for 1:1 partner nurture (simpler, truly personalized). Reserve Buttondown for when you're ready for broadcast content to the full partner list. The Make.com scenario recipe (Pipeline 5) should create Gmail drafts, with Buttondown integration added when the partner list is large enough for broadcast.

### List Subscribers

```
GET https://api.buttondown.email/v1/subscribers
  ?tag=referral-partner
Authorization: Token {BUTTONDOWN_API_KEY}
```

Returns all subscribers with the given tag.

---

## Make.com Integration

### For Pipeline 5 (1:1 Nurture — Gmail Drafts)

Use Make.com's **Gmail → Create a Draft** module:

- **To:** Partner's email from Google Sheet
- **Subject:** From Claude's draft output
- **Body:** From Claude's draft output
- **No send** — human reviews drafts in Gmail and sends manually

### For Future Broadcast (Buttondown)

Use Make.com's **HTTP → Make a request** module:

- **URL:** `https://api.buttondown.email/v1/emails`
- **Method:** POST
- **Headers:** `Authorization: Token {BUTTONDOWN_API_KEY}`, `Content-Type: application/json`
- **Body:** JSON with subject, body, status: "draft"
- Human reviews in Buttondown dashboard and publishes

---

## Subscriber Sync (One-Time Setup)

Import the 22 partners from the bookkeeper prospect list into Buttondown:

1. Export the Referral Partners Google Sheet as CSV
2. In Buttondown dashboard → Subscribers → Import
3. Map columns: email, name (firm_name), tags
4. Tag all imports as `referral-partner` + their tier tag

Or automate via API: Make.com scenario reads the Google Sheet, iterates over rows with email addresses, and calls `POST /v1/subscribers` for each.

---

## Email Content Guidelines

All emails sent through Buttondown must follow the SMD Services tone standard:

- **"We" voice** — never "I" or "the consultant"
- **No dollar amounts** — ever
- **No fixed timeframes** — don't commit to durations
- **Objectives over problems** — frame around what the business is trying to achieve
- **Collaborative** — "we work alongside" not "we audit"
- **"Solution" not "systems"** in body copy
- **Short** — 3-5 sentences for check-ins, 2-3 paragraphs for broadcast content

---

## Cost

Buttondown subscription is already paid. No incremental cost for Pipeline 5 integration.

| Component               | Monthly Cost                   |
| ----------------------- | ------------------------------ |
| Buttondown subscription | $0 incremental (already paid)  |
| Gmail (for 1:1 drafts)  | $0 (existing Google Workspace) |
| **Total**               | **$0**                         |

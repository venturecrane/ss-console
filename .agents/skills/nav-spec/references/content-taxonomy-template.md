---
description: Template for the Content Taxonomy section of NAVIGATION.md. Declares the canonical terms for objects, actions, states, and dates. Validator rule R20 checks labels in generated HTML against this taxonomy.
---

# Content Taxonomy

Labels are half of IA. If `/portal/quotes` is titled "Your Proposals" but the email says "Your Quote," users are navigating two products. The taxonomy section declares the canonical term for every object, action, status, and time expression, and the validator (R20) enforces consistent usage.

---

## What the taxonomy covers

### Object names

The user-visible name for each entity in the product. One name per object, used everywhere.

| Entity (DB term) | Client-facing label (canonical) | Synonyms to avoid                               |
| ---------------- | ------------------------------- | ----------------------------------------------- |
| quote            | Proposal                        | Quote, Estimate, Bid, SOW (the PDF is separate) |
| invoice          | Invoice                         | Bill, Statement                                 |
| engagement       | Engagement                      | Project, Contract, Job                          |
| milestone        | Milestone                       | Phase, Stage, Task, Checkpoint                  |
| client (entity)  | Client                          | Customer, Account                               |
| consultant       | Consultant                      | Agent, Staff, Advisor, Rep                      |
| document         | Document                        | File, Attachment, Artifact                      |

### Action verbs

Consistent verbs for actions on objects.

| Action                 | Canonical verb                                            | Synonyms to avoid                |
| ---------------------- | --------------------------------------------------------- | -------------------------------- |
| View a proposal detail | Review                                                    | Read, Look at, Open              |
| Sign a proposal        | Sign                                                      | Accept, Approve, Execute         |
| Pay an invoice         | Pay                                                       | Settle, Remit, Fund              |
| Download a document    | View (for PDFs, opens in browser) / Download (for others) | Get, Fetch, Save                 |
| Contact consultant     | Contact                                                   | Reach out, Message, Get in touch |

### Status labels

User-facing status terms. DB values are separate from what the UI shows.

| Entity  | DB value | User-facing label         | Badge color |
| ------- | -------- | ------------------------- | ----------- |
| quote   | sent     | Pending Review            | blue        |
| quote   | accepted | Accepted                  | green       |
| quote   | declined | Declined                  | red         |
| quote   | expired  | Expired                   | amber       |
| invoice | sent     | Sent (or Due, contextual) | blue        |
| invoice | paid     | Paid                      | green       |
| invoice | overdue  | Overdue                   | red         |
| invoice | void     | Voided                    | slate       |

### Time expressions

Canonical date and time formats, consistent across surfaces.

| Context                 | Format                               | Example                     |
| ----------------------- | ------------------------------------ | --------------------------- |
| Short date (list items) | `Mon D, YYYY`                        | Apr 15, 2026                |
| Short date (same year)  | `Mon D`                              | Apr 15                      |
| Relative for recent     | `N days ago` / `Yesterday` / `Today` | 3 days ago                  |
| Expiry countdown        | `Expires in N days` / `Expired`      | Expires in 5 days           |
| Touchpoint              | `DayName, Mon D at H:MM AM/PM`       | Thursday, Apr 18 at 2:30 PM |
| ISO (forms, data attrs) | `YYYY-MM-DD`                         | 2026-04-15                  |

### Numeric formats

| Context                               | Format              | Example     |
| ------------------------------------- | ------------------- | ----------- |
| Project price (marketing / proposals) | `$N,NNN` (no cents) | $5,250      |
| Invoice amount                        | `$N,NNN.CC`         | $2,625.00   |
| Small quantities                      | no units in label   | 3 proposals |

### Empty-state copy

Canonical phrasing for empty states.

| Surface                                      | Canonical copy                                                                  |
| -------------------------------------------- | ------------------------------------------------------------------------------- |
| `/portal/quotes` empty                       | "No proposals yet. When we send you one, it will appear here."                  |
| `/portal/invoices` empty                     | "No invoices yet. When we issue one, it will appear here."                      |
| `/portal/documents` empty                    | "Documents will appear here as your engagement progresses."                     |
| `/portal/engagement` empty                   | "No active engagement. When your engagement begins, progress will appear here." |
| `/portal` empty + no pending + no touchpoint | "Nothing needs your attention right now."                                       |

### Error-state copy

| Context                        | Canonical copy                                      |
| ------------------------------ | --------------------------------------------------- |
| Portal dashboard fetch failure | "Something went wrong loading your portal." + Retry |
| 404 portal route               | "This page doesn't exist. Go to the portal home."   |
| 401 portal                     | "Your session expired. Please sign in again."       |

---

## Template structure

In `.design/NAVIGATION.md`:

```markdown
## 10. Content taxonomy

### 10.1 Object names

| Entity | Canonical label | Forbidden synonyms   |
| ------ | --------------- | -------------------- |
| quote  | Proposal        | Quote, Estimate, Bid |

| ...

### 10.2 Action verbs

| Action               | Canonical verb |
| -------------------- | -------------- |
| View proposal detail | Review         |

| ...

### 10.3 Status labels

| Entity | DB value | Label | Badge color |
| ------ | -------- | ----- | ----------- |

| ...

### 10.4 Time expressions

| Context | Format |
| ------- | ------ |

| ...

### 10.5 Empty-state copy

| Surface | Copy |
| ------- | ---- |

| ...

### 10.6 Error-state copy

| Context | Copy |
| ------- | ---- |

| ...
```

---

## Rules for using the taxonomy

1. **One canonical label per concept.** If a PM or designer proposes a new term, either add it to the taxonomy (with the old term listed as a forbidden synonym) or reject it. Don't ship both.
2. **Code may use DB terms; UI must use taxonomy.** The DB column is `quote_id`; the UI says "Proposal." Accept this divergence; do not rename DB columns to match UI.
3. **Consistency across surfaces.** Email subject lines, PDF contents, notification copy, and UI must all use the taxonomy. This is audit-checkable.
4. **Clarity over brevity.** "Pending Review" is longer than "Sent" but communicates the client's required action. The taxonomy favors clarity.
5. **No clever variations.** "Pending review" on one surface and "Awaiting your review" on another = drift. Pick one and use it everywhere.

---

## Validator coverage (R20)

Rule R20 performs a shallow text match:

- Scans generated HTML for any status label from the taxonomy
- Flags occurrences of `synonyms to avoid` in user-visible positions (h1, h2, p, span, button, a, badge)
- Flags action verbs that don't match the canonical form (e.g., "Accept Proposal" button when canonical is "Review & Sign")
- Checks empty-state copy presence when an empty-state rendering is detected

False positives are possible with polysemous terms (e.g., "contract" might appear in an unrelated context). The validator reports severity `semantic` (not `structural`) for taxonomy violations so they can be reviewed without blocking.

---

## Example — ss-console

```markdown
## 10. Content taxonomy

### 10.1 Object names

| Entity     | Label      | Avoid                     |
| ---------- | ---------- | ------------------------- |
| quote      | Proposal   | Quote, Estimate, Bid, SOW |
| invoice    | Invoice    | Bill, Statement           |
| engagement | Engagement | Project, Contract         |
| client     | Client     | Customer, Account         |
| consultant | Consultant | Agent, Staff, Advisor     |

### 10.2 Action verbs

| Action                 | Verb                          |
| ---------------------- | ----------------------------- |
| View proposal detail   | Review                        |
| Sign proposal          | Review & Sign (composite CTA) |
| Pay invoice            | Pay                           |
| Download/view document | View (PDF) / Download (other) |

### 10.3 Status labels

| Entity  | DB       | Label          | Badge |
| ------- | -------- | -------------- | ----- |
| quote   | sent     | Pending Review | blue  |
| quote   | accepted | Accepted       | green |
| quote   | declined | Declined       | red   |
| quote   | expired  | Expired        | amber |
| invoice | sent     | Sent           | blue  |
| invoice | paid     | Paid           | green |
| invoice | overdue  | Overdue        | red   |

### 10.4 Time expressions

| Context    | Format                             |
| ---------- | ---------------------------------- |
| List items | `Mon D, YYYY` (same year: `Mon D`) |
| Touchpoint | `DayName, Mon D at H:MM AM/PM`     |

### 10.5 Empty-state copy

| Surface              | Copy                                                                          |
| -------------------- | ----------------------------------------------------------------------------- |
| `/portal/quotes`     | No proposals yet. When we send you one, it will appear here.                  |
| `/portal/invoices`   | No invoices yet. When we issue one, it will appear here.                      |
| `/portal/documents`  | Documents will appear here as your engagement progresses.                     |
| `/portal/engagement` | No active engagement. When your engagement begins, progress will appear here. |
```

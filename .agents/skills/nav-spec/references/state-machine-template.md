---
description: Template for the Navigation State Machine section of NAVIGATION.md. Declares how navigation changes in each auth, data, and task state. Validator rule R21 checks state-aware rendering.
---

# Navigation State Machine

Navigation is not static. Auth expires, data is empty or errored, tasks transition. The state machine section of the spec declares _how navigation changes in each state_ so these are designed, not improvised.

The state machine is authored per surface class. Within a class, each surface has state-specific navigation rules.

---

## Three dimensions of state

### Auth state

| State               | Description                            | Nav impact                                             |
| ------------------- | -------------------------------------- | ------------------------------------------------------ |
| **Authenticated**   | Valid session cookie                   | Full nav                                               |
| **Expired**         | Cookie present but session expired     | Redirect to login with `?redirect=<current>`           |
| **Unauthenticated** | No session                             | Redirect to login (or token-auth entry for deep-links) |
| **Token-valid**     | Signed token (for token-auth surfaces) | Restricted nav: only the action tied to the token      |
| **Token-expired**   | Signed token but expired/revoked       | Surface with "This link has expired" and recovery path |

### Data state

| State         | Description              | Nav impact                                                                                    |
| ------------- | ------------------------ | --------------------------------------------------------------------------------------------- |
| **Empty**     | Query returned no rows   | Empty-state chrome; onboarding or explanation copy; no list affordances to non-existent items |
| **Loading**   | Async data fetch pending | Skeleton or spinner; chrome present but affordances disabled                                  |
| **Populated** | Data present             | Full nav with item affordances                                                                |
| **Error**     | Fetch failed             | Error-state chrome with recovery (Retry, contact support, back to safe surface)               |

### Task state

Each surface has task-specific states that change what nav does:

| Surface                 | Task state | Nav impact                                                                    |
| ----------------------- | ---------- | ----------------------------------------------------------------------------- |
| `/portal/quotes/[id]`   | `sent`     | Review & Sign CTA visible; back button to list                                |
| `/portal/quotes/[id]`   | `accepted` | CTA hidden; Deposit invoice link visible if issued                            |
| `/portal/quotes/[id]`   | `declined` | CTA hidden; message "You declined this proposal on <date>"; no forward action |
| `/portal/quotes/[id]`   | `expired`  | CTA hidden; message "This proposal expired on <date>"; contact consultant CTA |
| `/portal/invoices/[id]` | `sent`     | Pay Now CTA prominent                                                         |
| `/portal/invoices/[id]` | `paid`     | Paid badge; receipt download; no CTA                                          |
| `/portal/invoices/[id]` | `overdue`  | Pay Now CTA with overdue indicator                                            |

---

## Template structure

In `.design/NAVIGATION.md`:

```markdown
## 5. Navigation state machine

### 5.1 Surface class: <name>

#### Auth states

| State           | Detection                           | Navigation behavior                                         |
| --------------- | ----------------------------------- | ----------------------------------------------------------- |
| Authenticated   | Valid session cookie via middleware | Full nav per reachability matrix                            |
| Expired         | Middleware rejects cookie           | Redirect to `/auth/portal-login?redirect=<encoded current>` |
| Unauthenticated | No cookie                           | Redirect to `/auth/portal-login`                            |

#### Data states per surface

##### `/portal`

| State                                             | Condition                                                | Rendering                                                                                |
| ------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Empty                                             | No engagement exists                                     | Welcome headline, "Your portal will populate when engagement begins," single contact CTA |
| Populated, no pending invoice, no next touchpoint | `activeEngagement && !pendingInvoice && !nextTouchpoint` | Status pill "In flight"; Recent Activity; section cards                                  |
| Populated, pending invoice                        | `pendingInvoice != null`                                 | ActionCard; Recent Activity; section cards                                               |
| Populated, next touchpoint                        | `nextTouchpointAt != null`                               | Touchpoint card; Recent Activity; section cards                                          |
| Error                                             | DB fetch threw                                           | Error card with Retry; Consultant block preserved                                        |

##### `/portal/invoices`

| State     | Condition        | Rendering                                                                                |
| --------- | ---------------- | ---------------------------------------------------------------------------------------- |
| Empty     | No invoices      | "No invoices yet. When invoices are created for your engagement, they will appear here." |
| Populated | Invoices present | Standard list with status badges, amounts, Pay Now CTAs                                  |
| Error     | Fetch threw      | Error state; back to home available                                                      |

...

#### Task states per surface

##### `/portal/quotes/[id]`

| Task state | Source                    | Nav behavior                                     |
| ---------- | ------------------------- | ------------------------------------------------ |
| sent       | quote.status = 'sent'     | Review & Sign CTA; countdown if expires_at set   |
| accepted   | quote.status = 'accepted' | Signed badge; deposit invoice link if exists     |
| declined   | quote.status = 'declined' | Declined message; no forward action; contact CTA |
| expired    | quote.status = 'expired'  | Expired message; contact CTA                     |
```

---

## Invariants

- **Empty is not error.** An empty list has a single clear "you haven't done X yet" message; an error has a "something went wrong, try again" message. Don't conflate.
- **Error states have recovery.** Every error renders ≥1 action: Retry, navigate to safe surface, or contact support. No pure dead-ends.
- **Loading does not hide chrome.** Header, back affordance, and landmarks render during loading. Only the data area uses skeletons.
- **Auth state transitions preserve intent.** Expiry mid-session redirects to login with a `?redirect=` param. Post-login, the user lands on their original destination.
- **Cross-auth-boundary is a full page reload.** SPA-style navigation across auth boundaries can leak state. Navigating from `session-auth-client` to `public` uses a full page reload.

---

## Validator coverage (R21)

Rule R21 checks:

- For every surface in the state machine, the generated HTML handles each declared state, OR declares which state was generated in a `data-nav-state` attribute on `<main>`
- Empty states include a non-empty help message (not a blank screen)
- Error states include a recovery affordance
- Loading states, if present, preserve chrome landmarks

---

## Example — ss-console session-auth-client

```markdown
### 5.1 Surface class: session-auth-client

#### Auth states

| State           | Detection                                 | Nav behavior                                     |
| --------------- | ----------------------------------------- | ------------------------------------------------ |
| Authenticated   | `Astro.locals.session?.role === 'client'` | Full nav; reachability matrix applies            |
| Expired         | Middleware 401                            | Redirect to `/auth/portal-login?redirect=<path>` |
| Unauthenticated | No session                                | Redirect to `/auth/portal-login`                 |

#### `/portal` states

| State                       | Condition                                     | Rendering                                                                | Key chrome                                                                        |
| --------------------------- | --------------------------------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| Empty                       | `!activeEngagement`                           | Welcome message; "Your portal will populate when your engagement begins" | Header, skip-link, ConsultantBlock (if consultant assigned), section cards hidden |
| Error                       | DB threw                                      | Error card; Retry                                                        | Header, skip-link, ConsultantBlock                                                |
| Populated + pending invoice | `pendingInvoice != null`                      | ActionCard dominant; Recent Activity; section cards                      | Full                                                                              |
| Populated + touchpoint      | `nextTouchpointAt != null && !pendingInvoice` | Touchpoint card; Recent Activity; section cards                          | Full                                                                              |
| Populated + idle            | else                                          | "Nothing needs your attention right now"; Recent Activity; section cards | Full                                                                              |

#### `/portal/quotes/[id]` task states

| quote.status | Primary CTA              | Body state                                                 |
| ------------ | ------------------------ | ---------------------------------------------------------- |
| sent         | Review & Sign (SignWell) | Expiry countdown if set                                    |
| accepted     | —                        | "Signed on <date>"; link to deposit invoice if issued      |
| declined     | —                        | "You declined this proposal on <date>"; Contact consultant |
| expired      | —                        | "This proposal expired on <date>"; Contact consultant      |
```

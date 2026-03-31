# Spike: D1 Batch API for Atomic Webhook Operations

**Issue:** #85
**PRD Reference:** OQ-009 (Critical)
**Date:** 2026-03-30
**Status:** Complete

---

## Objective

Confirm whether D1's `db.batch()` API supports multi-statement transactions with rollback semantics sufficient for our webhook handlers, or identify the workaround.

The SignWell `document.completed` webhook handler must atomically:

1. Update `quotes.status` to `'accepted'`
2. Update `clients.status` to `'active'`
3. Create an `engagements` record
4. Create a `deposit` invoice in the `invoices` table

If any of these fail, none should persist (BR-038).

---

## Findings

### 1. `db.batch()` API Overview

D1 exposes `db.batch()` as the primary mechanism for multi-statement operations.

**Signature:**

```typescript
const results: D1Result[] = await env.DB.batch(statements: D1PreparedStatement[]);
```

**Behavior:**

- Accepts an array of prepared statements
- Executes them sequentially, non-concurrently
- Returns an array of `D1Result` objects in the same order as the input
- Operates as an implicit SQL transaction under the hood

**Documented rollback guarantee:**

> "Batched statements are SQL transactions. If a statement in the sequence fails, then an error is returned for that specific statement, and it aborts or rolls back the entire sequence."

Source: [Cloudflare D1 Database docs](https://developers.cloudflare.com/d1/worker-api/d1-database/)

### 2. Explicit Transactions Are Not Supported

D1 **does not** support explicit `BEGIN TRANSACTION`, `COMMIT`, `ROLLBACK`, or `SAVEPOINT` SQL statements. Attempting to execute them returns:

> "To execute a transaction, please use the state.storage.transaction() API instead of the SQL BEGIN TRANSACTION or SAVEPOINT statements."

**Why:** D1 is built on SQLite behind a Durable Object. Because the SQL executes inside the database (on one machine) while the Worker runs at the edge (potentially on the other side of the world), an open `BEGIN TRANSACTION` from a Worker would hold a write lock across a network round trip, blocking the entire database. Cloudflare prevents this by design.

`db.batch()` avoids this problem because all statements are shipped to the database together and execute locally inside a single transaction.

### 3. Rollback Behavior: Confirmed With Caveats

**Documented behavior:** If any statement in the batch fails, the entire batch rolls back. No partial state persists.

**Community-reported issues:**

- **Miniflare (local dev) bug (2023):** A user demonstrated that in the local miniflare emulator, a batch with a syntax error in the second statement did not roll back the first statement. This was fixed in miniflare (issue [#484](https://github.com/cloudflare/miniflare/issues/484), resolved Feb 2023). The bug was local-dev only and did not affect production D1.
- **Transaction inconsistency report (Cloudflare Community):** A user reported inconsistent counter values across tables after batch updates. The root cause was not conclusively attributed to a D1 bug vs. application logic. No Cloudflare staff confirmed a production transaction bug.
- **Drizzle ORM issues:** Drizzle's `db.transaction()` API does not work with D1 because it emits `BEGIN TRANSACTION` SQL, which D1 rejects. Drizzle's `db.batch()` wrapper works correctly as a substitute.

**Assessment:** The documented rollback semantics are reliable in production D1. The reported issues were either local-dev bugs (fixed) or unconfirmed application-level errors. No confirmed production transaction integrity failures exist in the public record as of March 2026.

### 4. Limitations of `db.batch()`

#### 4a. No Cross-Statement Data Dependency

Statements in a batch are prepared and bound **before** execution begins. You cannot use the result of one statement (e.g., a newly inserted row's ID) as a parameter in a subsequent statement within the same batch.

```typescript
// THIS DOES NOT WORK — cannot reference engagement_id from statement 1 in statement 2
const results = await env.DB.batch([
  env.DB.prepare('INSERT INTO engagements (id, ...) VALUES (?, ...)').bind(engagementId, ...),
  env.DB.prepare('INSERT INTO invoices (engagement_id, ...) VALUES (?, ...)').bind(/* can't get engagementId from above */),
]);
```

**Workaround:** Generate all IDs client-side (in the Worker) before building the batch. Since we use UUIDs (TEXT primary keys), this is straightforward:

```typescript
const engagementId = crypto.randomUUID()
const invoiceId = crypto.randomUUID()

const results = await env.DB.batch([
  env.DB.prepare('UPDATE quotes SET status = ?, accepted_at = ? WHERE id = ? AND org_id = ?').bind(
    'accepted',
    now,
    quoteId,
    orgId
  ),
  env.DB.prepare('UPDATE clients SET status = ? WHERE id = ? AND org_id = ?').bind(
    'active',
    clientId,
    orgId
  ),
  env.DB.prepare(
    `INSERT INTO engagements (id, org_id, client_id, quote_id, status, estimated_hours, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(engagementId, orgId, clientId, quoteId, 'scheduled', totalHours, now, now),
  env.DB.prepare(
    `INSERT INTO invoices (id, org_id, engagement_id, client_id, type, amount, status, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`
  ).bind(invoiceId, orgId, engagementId, clientId, 'deposit', depositAmount, 'draft', now, now),
])
```

This works because the `engagementId` is a pre-generated UUID, not a database-generated value.

#### 4b. Per-Statement Limits Apply

Each statement in a batch is subject to individual D1 limits:

| Limit                  | Value                     |
| ---------------------- | ------------------------- |
| SQL statement length   | 100 KB per statement      |
| Bound parameters       | 100 per statement         |
| Query duration         | 30 seconds (entire batch) |
| Queries per invocation | 1,000 (paid) / 50 (free)  |
| Max row size           | 2 MB                      |

Our webhook handlers will execute 4-6 statements per batch. These limits are not a concern.

#### 4c. No Interleaved JavaScript

`db.batch()` executes all SQL atomically, but you cannot interleave JavaScript logic between statements. If you need to make a decision based on a query result before executing the next statement, you cannot do that inside a single batch.

**Impact on our design:** The SignWell webhook handler needs to:

1. Look up the quote by `signwell_doc_id` (read)
2. Check if already processed (idempotency guard)
3. If not processed, execute the multi-table write batch

Steps 1-2 are reads that happen **before** the batch. Step 3 is the atomic batch. This pattern works naturally with `db.batch()`.

#### 4d. External API Calls Are Outside the Transaction

The webhook handler also needs to:

- Download the signed PDF from SignWell and upload to R2 (step 3-4 in the spec)
- Send emails via Resend (step 10)
- Create a Stripe invoice via API (step 9)

These external calls **cannot** be part of the D1 transaction. If the D1 batch succeeds but the Stripe API call fails, we have a state mismatch.

This is addressed in the recommended pattern below.

### 5. `db.exec()` Comparison

`db.exec()` accepts raw SQL strings (multiple statements separated by newlines) but:

- Does **not** support prepared statements or parameter binding
- Is less safe (SQL injection risk)
- Has poorer performance
- Is intended for migrations and one-shot tasks only

**Verdict:** Do not use `db.exec()` for webhook handlers. Use `db.batch()` exclusively.

---

## Recommended Pattern

### Architecture: Two-Phase Webhook Handler

Split the webhook handler into two phases: an **atomic D1 batch** for all database state changes, followed by **fire-and-forget side effects** with retry/reconciliation for external API calls.

```
Phase 1: Atomic D1 Batch (all-or-nothing)
  ├── Update quote status
  ├── Update client status
  ├── Create engagement record
  └── Create invoice record (status = 'draft')

Phase 2: Side Effects (best-effort with recovery)
  ├── Upload signed PDF to R2
  ├── Create Stripe invoice via API
  ├── Update invoice with stripe_invoice_id
  └── Send emails via Resend
```

### Implementation Pattern

```typescript
// src/lib/webhooks/signwell-handler.ts

export async function handleDocumentCompleted(
  env: Env,
  payload: SignWellDocumentCompleted
): Promise<Response> {
  const { document_id, signed_document_url } = payload.data
  const now = new Date().toISOString()

  // --- Pre-batch reads (outside transaction) ---

  // 1. Look up quote by SignWell document ID
  const quote = await getQuoteBySignWellDocId(env.DB, document_id)
  if (!quote) {
    console.log(`Unknown SignWell document: ${document_id}`)
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }

  // 2. Idempotency guard
  if (quote.status === 'accepted') {
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }

  // --- Phase 1: Atomic D1 batch ---

  const engagementId = crypto.randomUUID()
  const invoiceId = crypto.randomUUID()

  try {
    await env.DB.batch([
      env.DB.prepare(
        `UPDATE quotes SET status = 'accepted', accepted_at = ?, signed_sow_path = ?
         WHERE id = ? AND org_id = ? AND status = 'sent'`
      ).bind(now, `sows/${quote.org_id}/${quote.id}/signed-sow.pdf`, quote.id, quote.org_id),

      env.DB.prepare(
        `UPDATE clients SET status = 'active', updated_at = ? WHERE id = ? AND org_id = ?`
      ).bind(now, quote.client_id, quote.org_id),

      env.DB.prepare(
        `INSERT INTO engagements (id, org_id, client_id, quote_id, status, estimated_hours, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'scheduled', ?, ?, ?)`
      ).bind(engagementId, quote.org_id, quote.client_id, quote.id, quote.total_hours, now, now),

      env.DB.prepare(
        `INSERT INTO invoices (id, org_id, engagement_id, client_id, type, amount, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'deposit', ?, 'draft', ?, ?)`
      ).bind(
        invoiceId,
        quote.org_id,
        engagementId,
        quote.client_id,
        quote.deposit_amount,
        now,
        now
      ),
    ])
  } catch (err) {
    // Batch failed — all changes rolled back. Safe to let the webhook retry.
    console.error('SignWell webhook batch failed:', err)
    return new Response(JSON.stringify({ error: 'INTERNAL_ERROR' }), {
      status: 500,
    })
  }

  // --- Phase 2: Side effects (best-effort) ---

  // These run after the batch succeeds. If any fail, the database state
  // is correct but side effects are incomplete. A reconciliation job
  // or admin action resolves the gap.

  try {
    // Upload signed PDF to R2
    const signedPdf = await fetch(signed_document_url)
    await env.R2_BUCKET.put(`sows/${quote.org_id}/${quote.id}/signed-sow.pdf`, signedPdf.body)
  } catch (err) {
    console.error('Failed to upload signed PDF to R2:', err)
    // Non-fatal: PDF can be re-fetched from SignWell later
  }

  try {
    // Create Stripe invoice and update the invoice record
    const stripeInvoice = await createStripeDepositInvoice(env, quote, invoiceId)
    await env.DB.prepare(
      `UPDATE invoices SET stripe_invoice_id = ?, stripe_hosted_url = ?, status = 'sent', sent_at = ?, updated_at = ?
       WHERE id = ? AND org_id = ?`
    )
      .bind(stripeInvoice.id, stripeInvoice.hosted_invoice_url, now, now, invoiceId, quote.org_id)
      .run()
  } catch (err) {
    console.error('Failed to create Stripe invoice:', err)
    // Invoice exists in D1 with status='draft' — admin dashboard shows it needs attention
  }

  try {
    // Send confirmation email
    await sendEngagementConfirmation(env, quote, engagementId)
  } catch (err) {
    console.error('Failed to send confirmation email:', err)
    // Non-fatal: admin can trigger manually
  }

  return new Response(JSON.stringify({ ok: true }), { status: 200 })
}
```

### Same Pattern for Stripe Webhook

```typescript
// Stripe invoice.paid handler — simpler case

// Phase 1: Atomic batch
await env.DB.batch([
  env.DB.prepare(
    `UPDATE invoices SET status = 'paid', paid_at = ?, payment_method = ?, updated_at = ?
     WHERE id = ? AND org_id = ?`
  ).bind(now, paymentMethod, now, invoiceId, orgId),

  // If this is a deposit invoice, activate the engagement
  env.DB.prepare(
    `UPDATE engagements SET status = 'active', start_date = ?, updated_at = ?
     WHERE id = ? AND org_id = ? AND status = 'scheduled'`
  ).bind(now, now, engagementId, orgId),
])

// Phase 2: Send confirmation emails (best-effort)
```

### Handling the Stripe Invoice Creation Failure (EC-009)

The business analyst identified edge case EC-009: what happens if the SOW is signed but Stripe invoice creation fails?

With the two-phase pattern:

1. The D1 batch succeeds: quote is accepted, engagement exists, invoice record exists with `status = 'draft'`
2. The Stripe API call fails: the invoice has no `stripe_invoice_id`
3. The webhook returns `200` (the D1 state is correct; retrying won't help since the Stripe call is the problem)
4. The admin dashboard shows an invoice in `draft` status with no Stripe link — this is a visible, actionable item
5. The admin can trigger Stripe invoice creation manually or wait for the reconciliation check

This is preferable to returning `500` and letting SignWell retry, because:

- The retry would hit the idempotency guard (quote already accepted) and skip the batch
- The Stripe call would never be retried through the webhook path
- The admin would have no visibility into the failure

### Reconciliation Strategy

For robustness, add a lightweight admin-triggered or scheduled reconciliation check:

```sql
-- Find invoices stuck in 'draft' (should have been sent to Stripe)
SELECT i.id, i.engagement_id, i.amount, i.created_at
FROM invoices i
WHERE i.status = 'draft'
  AND i.stripe_invoice_id IS NULL
  AND i.created_at < datetime('now', '-1 hour')
  AND i.org_id = ?;
```

At MVP volume (less than 5 engagements/month), an admin dashboard indicator is sufficient. A scheduled Worker for automated reconciliation can be added if volume increases.

---

## Answers to Spike Tasks

### Does `db.batch()` support multi-table inserts/updates?

**Yes.** A single `db.batch()` call can contain any mix of INSERT, UPDATE, DELETE, and SELECT statements across multiple tables. All execute within a single implicit transaction.

### Does `db.batch()` roll back on partial failure?

**Yes, in production D1.** If any statement fails, the entire batch is rolled back. A historical miniflare (local dev) bug existed but was fixed in February 2023. No confirmed production rollback failures exist.

### What are the limitations?

1. **No cross-statement data dependency** — generate all IDs (UUIDs) before building the batch
2. **No interleaved JavaScript** — reads/decisions must happen before the batch; use a read-then-write pattern
3. **External API calls are outside the transaction** — use the two-phase pattern (atomic batch + best-effort side effects)
4. **No explicit BEGIN/COMMIT/ROLLBACK** — `db.batch()` is the only transaction mechanism
5. **Per-statement limits apply** — 100 KB SQL, 100 bound parameters, 30s total duration

### Is a compensating transaction pattern needed?

**No.** `db.batch()` provides genuine rollback semantics. A compensating transaction pattern (undo operations on failure) is not needed for the D1 operations. The two-phase pattern handles the external API boundary cleanly.

---

## Recommendation

**Use `db.batch()` for all webhook-driven multi-table state changes.** The API provides the atomicity guarantees required by BR-038 and OQ-009.

Adopt the two-phase webhook handler pattern:

1. **Phase 1:** Atomic `db.batch()` for all D1 writes — all succeed or all roll back
2. **Phase 2:** Best-effort external API calls (R2, Stripe, Resend) with admin-visible recovery for failures

Key implementation requirements:

- Generate all entity IDs (UUIDs) before constructing the batch
- Perform all reads and idempotency checks before the batch
- Return `500` only if the batch itself fails (triggers webhook retry)
- Return `200` after a successful batch, even if side effects fail (prevents infinite retry loops)
- Surface failed side effects in the admin dashboard for manual resolution
- Add a reconciliation query for invoices stuck in `draft` without a Stripe link

This pattern is safe, simple, and appropriate for MVP volume. No architectural workarounds or compensating transactions are needed.

---

## References

- [Cloudflare D1 Database API](https://developers.cloudflare.com/d1/worker-api/d1-database/)
- [Cloudflare D1 Limits](https://developers.cloudflare.com/d1/platform/limits/)
- [D1 blog: "Our quest to simplify databases"](https://blog.cloudflare.com/whats-new-with-d1/)
- [Miniflare rollback fix (issue #484)](https://github.com/cloudflare/miniflare/issues/484)
- [Drizzle ORM D1 transaction issue (#2463)](https://github.com/drizzle-team/drizzle-orm/issues/2463)
- PRD: Section 6.4 (Webhook Architecture), BR-038, EC-009
- Business Analyst: OQ-009

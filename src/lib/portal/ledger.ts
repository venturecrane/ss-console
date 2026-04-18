/**
 * Engagement ledger — derives paid / remaining / next-charge figures for the
 * client portal from the invoices table.
 *
 * Money rule (.design/portal-ux-brief.md): all monetary values surface as
 * dollar figures, never percentages or bars. These figures are returned in
 * cents to keep presentation components in charge of formatting.
 *
 * Paid:     sum(amount) where status = 'paid'
 * Next:     earliest 'sent' invoice by due_date (null due_date sorts last)
 * Remaining: sum(amount) where status IN ('sent', 'overdue', 'draft')
 */

export interface EngagementLedger {
  paid_cents: number
  remaining_cents: number
  next_charge_cents: number | null
  next_charge_due_date: string | null
  next_invoice_id: string | null
}

interface InvoiceRow {
  id: string
  amount: number
  status: string
  due_date: string | null
}

const UNPAID_STATUSES = new Set(['sent', 'overdue', 'draft'])

export async function getEngagementLedger(
  db: D1Database,
  engagementId: string
): Promise<EngagementLedger> {
  const result = await db
    .prepare(
      `SELECT id, amount, status, due_date
       FROM invoices
       WHERE engagement_id = ? AND status != 'void'
       ORDER BY created_at ASC`
    )
    .bind(engagementId)
    .all<InvoiceRow>()

  const rows = result.results ?? []

  let paidCents = 0
  let remainingCents = 0
  let nextInvoice: InvoiceRow | null = null

  for (const row of rows) {
    const cents = Math.round(row.amount * 100)
    if (row.status === 'paid') {
      paidCents += cents
      continue
    }
    if (UNPAID_STATUSES.has(row.status)) {
      remainingCents += cents
      if (isSooner(row, nextInvoice)) {
        nextInvoice = row
      }
    }
  }

  return {
    paid_cents: paidCents,
    remaining_cents: remainingCents,
    next_charge_cents: nextInvoice ? Math.round(nextInvoice.amount * 100) : null,
    next_charge_due_date: nextInvoice?.due_date ?? null,
    next_invoice_id: nextInvoice?.id ?? null,
  }
}

function isSooner(candidate: InvoiceRow, current: InvoiceRow | null): boolean {
  if (!current) return true
  // A missing due_date should never beat an existing one.
  if (!candidate.due_date) return false
  if (!current.due_date) return true
  return candidate.due_date < current.due_date
}

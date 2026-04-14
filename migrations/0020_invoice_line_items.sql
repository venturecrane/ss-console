-- Migration 0020: Invoice line items
--
-- Adds per-invoice line items so the client portal invoice landing can render
-- a "What's included" breakdown. See issue #362 and .stitch/portal-ux-brief.md.
--
-- When no line items exist for an invoice, the portal renders a single
-- fallback row from the invoice description / engagement scope summary.
-- Amounts are stored in cents to avoid float rounding; the invoices.amount
-- column remains REAL (dollars) for backwards compatibility.

CREATE TABLE IF NOT EXISTS invoice_line_items (
  id           TEXT PRIMARY KEY,
  invoice_id   TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  description  TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_invoice_line_items_invoice
  ON invoice_line_items(invoice_id, sort_order);

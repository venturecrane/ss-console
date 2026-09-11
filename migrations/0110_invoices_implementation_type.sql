-- 0110: the Operator stand-up fee is an invoice type of its own; the type
-- CHECK on invoices retires.
--
-- ADR 0063 prices the Operator as a flat monthly retainer plus a one-time
-- stand-up fee. The retainer had a home in the invoices table from the day
-- the schema was authored (type='retainer', mirrored from Stripe cycle
-- invoices by stripe-subscription-handler.ts). The stand-up fee had none:
-- every type in the CHECK is consulting-shaped (deposit, completion,
-- milestone, assessment) or the retainer, and the portal renders the type
-- as the invoice's title. Issuing the first production client's stand-up
-- fee as a "Milestone" would have been a fabricated label on a client
-- surface, so a type is added ('implementation') rather than borrowed.
--
-- The CHECK itself is dropped, following the 0033 Captain decision on
-- users.role: SQLite has no ALTER CONSTRAINT, so every vocabulary change
-- costs a full table rebuild plus the FK ceremony below. App-layer
-- TypeScript (`InvoiceType` in src/lib/db/invoices.ts, VALID_TYPES in the
-- admin create route) enforces the vocabulary at every insert site; the
-- status CHECK stays because the status machine is enforced there too and
-- has no reason to change.
--
-- FK ceremony (0033 v2 pattern, verified there): D1 wraps each migration in
-- one transaction, inside which PRAGMA foreign_keys=OFF is a no-op, and
-- SQLite's DROP TABLE performs an implicit DELETE that would CASCADE into
-- invoice_line_items (0020: REFERENCES invoices(id) ON DELETE CASCADE).
-- So the child loses its FK first, the parent is rebuilt, and the child is
-- rebuilt with the FK restored. Row counts are preserved on both tables.
-- invoice_line_items is the only table that references invoices.
--
-- Manual-only rollback:
-- migrations/rollbacks/0110_invoices_implementation_type_down.sql

PRAGMA defer_foreign_keys = ON;

-- Step 1: detach invoice_line_items from invoices (no REFERENCES).
CREATE TABLE invoice_line_items_tmp (
  id           TEXT PRIMARY KEY,
  invoice_id   TEXT NOT NULL,
  description  TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO invoice_line_items_tmp (id, invoice_id, description, amount_cents, sort_order, created_at)
SELECT id, invoice_id, description, amount_cents, sort_order, created_at FROM invoice_line_items;
DROP TABLE invoice_line_items;
ALTER TABLE invoice_line_items_tmp RENAME TO invoice_line_items;

-- Step 2: rebuild invoices without the type CHECK. Column order, defaults,
-- and the status CHECK are preserved exactly.
CREATE TABLE invoices_new (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT REFERENCES engagements(id),
  type            TEXT NOT NULL,
  amount          REAL NOT NULL,
  description     TEXT,
  status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN (
                    'draft', 'sent', 'paid', 'overdue', 'void'
                  )),
  stripe_invoice_id TEXT,
  stripe_hosted_url TEXT,
  due_date        TEXT,
  sent_at         TEXT,
  paid_at         TEXT,
  payment_method  TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
  entity_id       TEXT
);
INSERT INTO invoices_new (
  id, org_id, engagement_id, type, amount, description, status,
  stripe_invoice_id, stripe_hosted_url, due_date, sent_at, paid_at,
  payment_method, notes, created_at, updated_at, entity_id
)
SELECT
  id, org_id, engagement_id, type, amount, description, status,
  stripe_invoice_id, stripe_hosted_url, due_date, sent_at, paid_at,
  payment_method, notes, created_at, updated_at, entity_id
FROM invoices;
DROP TABLE invoices;
ALTER TABLE invoices_new RENAME TO invoices;

CREATE INDEX idx_invoices_org_status ON invoices(org_id, status);
CREATE INDEX idx_invoices_engagement_id ON invoices(engagement_id);
CREATE INDEX idx_invoices_stripe_id ON invoices(stripe_invoice_id);
CREATE INDEX idx_invoices_due_date ON invoices(due_date);

-- Step 3: restore the FK from invoice_line_items to the rebuilt invoices.
CREATE TABLE invoice_line_items_new (
  id           TEXT PRIMARY KEY,
  invoice_id   TEXT NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
  description  TEXT NOT NULL,
  amount_cents INTEGER NOT NULL,
  sort_order   INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
INSERT INTO invoice_line_items_new (id, invoice_id, description, amount_cents, sort_order, created_at)
SELECT id, invoice_id, description, amount_cents, sort_order, created_at FROM invoice_line_items;
DROP TABLE invoice_line_items;
ALTER TABLE invoice_line_items_new RENAME TO invoice_line_items;

CREATE INDEX idx_invoice_line_items_invoice ON invoice_line_items(invoice_id, sort_order);

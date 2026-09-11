-- Rollback for 0110: restore the original invoices.type CHECK.
--
-- Manual-only (see README.md). Same FK ceremony as the up migration.
-- The INSERT into invoices_old fails on purpose if any row carries
-- type='implementation': reclassify or void those rows before invoking.

PRAGMA defer_foreign_keys = ON;

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

CREATE TABLE invoices_old (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  engagement_id   TEXT REFERENCES engagements(id),
  type            TEXT NOT NULL CHECK (type IN (
                    'deposit', 'completion', 'milestone', 'assessment', 'retainer'
                  )),
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
INSERT INTO invoices_old (
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
ALTER TABLE invoices_old RENAME TO invoices;

CREATE INDEX idx_invoices_org_status ON invoices(org_id, status);
CREATE INDEX idx_invoices_engagement_id ON invoices(engagement_id);
CREATE INDEX idx_invoices_stripe_id ON invoices(stripe_invoice_id);
CREATE INDEX idx_invoices_due_date ON invoices(due_date);

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

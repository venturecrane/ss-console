-- Strategic repositioning: loosen vertical CHECK, add revenue_range.
--
-- The clients.vertical CHECK constraint was limited to the original 6 verticals.
-- The evolved ICP is problem-qualified, not vertical-gated. D1/SQLite cannot
-- ALTER CHECK constraints, so we recreate the table with a broader constraint.
--
-- Also adds revenue_range to both clients and entities tables.

-- 1. Recreate clients with expanded vertical CHECK and revenue_range column
CREATE TABLE clients_new (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  business_name   TEXT NOT NULL,
  vertical        TEXT CHECK (vertical IN (
                    'home_services', 'professional_services',
                    'contractor_trades', 'retail_salon', 'restaurant_food',
                    'healthcare', 'technology', 'manufacturing', 'other'
                  )),
  employee_count  INTEGER,
  revenue_range   TEXT CHECK (revenue_range IN (
                    'under_500k', '500k_1m', '1m_3m', '3m_5m',
                    '5m_10m', 'over_10m', 'unknown'
                  )),
  years_in_business INTEGER,
  city            TEXT,
  status          TEXT NOT NULL DEFAULT 'prospect'
                    CHECK (status IN ('prospect', 'active', 'completed', 'churned')),
  source          TEXT,
  notes           TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO clients_new
  SELECT id, org_id, business_name, vertical, employee_count,
         'unknown' AS revenue_range,
         years_in_business, NULL AS city, status, source, notes,
         created_at, updated_at
  FROM clients;

DROP TABLE clients;
ALTER TABLE clients_new RENAME TO clients;

-- 2. Add revenue_range to entities table (already unconstrained TEXT for vertical)
ALTER TABLE entities ADD COLUMN revenue_range TEXT DEFAULT 'unknown';

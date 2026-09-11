-- 0111: the firm's own executed paper, readable in its portal (ss#2641)
--
-- Service agreement §4.5 already promised the audit record would be
-- "viewable by the Firm in the portal at any time", and the Compliance
-- surface honors it. The document that CREATES that obligation lived
-- nowhere the firm could reach: the agreement package is markdown in the
-- private engagements repo, signature copies are PDFs in an inbox, and
-- A&P holds zero `engagements` rows, so the existing engagement document
-- library could not carry them without inventing an engagement record to
-- hold a file. This table is the honest home instead.
--
-- EXECUTED ONLY, ENFORCED BY THE SCHEMA. `executed_on` is NOT NULL, so a
-- draft cannot be recorded at all. That is deliberate and it is the whole
-- control: anything a client sees in its own portal reads as the operative
-- terms, and a draft rendered there would be read as governing when it is
-- not. The endpoint additionally refuses a future date -- an agreement
-- "executed" tomorrow is not executed -- but the NOT NULL is the part that
-- cannot be forgotten.
--
-- A DATED SET, NOT A SINGLE DOCUMENT. Amendments are ordinary rows with a
-- later `executed_on`; readers order by it descending. There is no
-- supersession column on purpose. The moment a second version exists,
-- showing only one misleads about what is in force, and modelling
-- "superseded" would invite a UI that hides paper the firm signed. Every
-- executed document stays visible with its date, and the reader decides.
-- (This is not hypothetical for the first client: §3.2, §3.8 and §9.1 have
-- all moved on the unsigned draft, so amendments are expected.)
--
-- TITLE AND DATE ARE AUTHORED, NEVER DERIVED. Both come from a human in the
-- admin flow. Parsing a date out of a filename would be exactly the
-- runtime-fabrication pattern CLAUDE.md bans (Pattern B): a plausible value
-- rendered to a client from a non-authoritative field.
--
-- Instance-scoped, because the Compliance surface is instance-addressed and
-- an entity may hold several operator instances. `storage_key` points at
-- R2 (`{orgId}/operator/{slug}/agreements/{hash}/{name}`); the bytes never
-- live in D1.
--
-- Manual-only rollback at
-- migrations/rollbacks/0111_operator_agreement_documents_down.sql.

CREATE TABLE operator_agreement_documents (
  id            TEXT PRIMARY KEY,
  org_id        TEXT NOT NULL,
  entity_id     TEXT NOT NULL,
  -- The operator instance's customer_slug. Matches subscriptions.instance_slug
  -- and the {instance} segment of the portal's Compliance route.
  instance_slug TEXT NOT NULL,
  -- Authored display name, e.g. "Operator Service Agreement".
  title         TEXT NOT NULL,
  -- Authored date of execution (YYYY-MM-DD). NOT NULL is the executed-only gate.
  executed_on   TEXT NOT NULL,
  storage_key   TEXT NOT NULL UNIQUE,
  file_name     TEXT NOT NULL,
  uploaded_by   TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The portal read: one instance's documents, newest executed first.
CREATE INDEX idx_operator_agreement_documents_instance
  ON operator_agreement_documents (entity_id, instance_slug, executed_on DESC);

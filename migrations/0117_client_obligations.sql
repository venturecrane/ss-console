-- 0117: the obligation register — every piece of work SMD owes a client, in
-- one queryable place, plus the reconcile-run ledger that makes closure
-- checkable rather than merely asserted (ADR 0088).
--
-- WHAT WAS MISSING. We support clients now, and the work of keeping them
-- served lives in nine different places. For Ashton & Price on the day this
-- was written: a Smokeball task cleanup stated only in correspondence letters
-- 26/27/28 (with the wrong blocker issue attached); routine 11's per-cycle
-- duty in an agent memory file; a Smokeball consent that expires in March
-- 2027; a medical chronology that was delivered while its ledger row still
-- read `failed`; a vendor scope approval visible only as a peer session's
-- mission line; four unshipped fixes in GitHub. None of it queryable, and
-- nothing that says when one goes overdue.
--
-- The precedent for what happens without this: the enterprise cadence engine,
-- a reminder queue with no forcing function, stood at 7 of 16 items overdue,
-- one of them by 134 days.
--
-- WHY A NEW TABLE rather than extending milestones / parking_lot / follow_ups.
-- Those three model a consulting ENGAGEMENT and hold zero rows in production
-- (verified live 2026-09-17); the live client key is customer_configs. They
-- also model tasks. An obligation is not a task: it carries a WINDOW
-- (window_start/window_end) distinct from a DUE DATE, a closed `kind`
-- vocabulary that makes the register reportable, and a pointer back to the
-- source that created it. That shape is borrowed from contract-obligation
-- registers, not from ticket systems.
--
-- THERE IS NO FAILURE STATE, AND THAT IS THE POINT. The medchron ledger made
-- `failed` terminal with no transition out (medchron_ledger.py:58), so a job
-- that was delivered to the client stayed stranded in a state that said
-- otherwise and could only be moved by hand. Here failure is `parked`, which
-- carries its reason and a resume token and has edges back to every working
-- state. `closed` is the only terminal state and keeps exactly one outbound
-- edge (a Captain reopen, which the monthly audit uses).
--
-- CLOSURE IS NOT SELF-CERTIFIED. The party that owes an obligation does not
-- get to declare it discharged: `verified` and `closed` require a
-- reconcile_run_id naming the run that probed the real surface. The
-- constraint below is what makes that a control instead of a convention —
-- agents hold the same D1 credential CI does, so the rule can only be
-- enforced by making a violation structurally impossible to write.
--
-- IDENTITY IS stable_key, NOT THE QUOTE. An earlier draft keyed rows on a
-- hash of the source quote. Letters get revised and renumbered (57, 58 and 59
-- all landed on 2026-09-16), so a quote-keyed row orphans itself on any edit
-- and a restatement duplicates. The quote is evidence; the key is a short
-- human-readable slug the agent assigns ('smokeball-task-cleanup').
--
-- Written by: .claude/hooks/lib/register.mjs (captured, letter-derived rows)
--             scripts/ci-reconcile-obligations.ts (imported rows; every
--             state change to verified/closed; evidence stamps).
-- Read by:    src/lib/db/obligations.ts -> the admin client page and
--             /admin/obligations.
-- Never read by any client-facing surface. This register is internal.

-- Every reconcile run, recorded before it writes anything. This is what makes
-- the "only the reconciler closes rows" constraint verifiable: run ids are
-- cross-referenced against real GitHub Actions runs, so a row closed by a
-- hand-written UPDATE either has no run id (rejected by the CHECK above) or
-- names a run that never existed (raised as obligation_unverifiable).
CREATE TABLE reconcile_runs (
  run_id              TEXT PRIMARY KEY,
  -- The Actions run this came from. NULL for a local run, which is itself the
  -- signal the integrity check looks for.
  workflow_run_url    TEXT,
  started_at          TEXT NOT NULL DEFAULT (datetime('now')),
  finished_at         TEXT,
  -- Both denominators, persisted. total is every row; universe is the
  -- non-terminal rows this run actually considered. One count alone cannot
  -- distinguish "nothing to do" from "the selector is broken", and the
  -- high-water mark across runs is what lets an empty register stay quiet on
  -- day one without staying quiet forever.
  total_rows          INTEGER,
  universe_rows       INTEGER,
  verified_count      INTEGER,
  overdue_count       INTEGER,
  cannot_evaluate_count INTEGER,
  exit_code           INTEGER
);

CREATE INDEX idx_reconcile_runs_started
  ON reconcile_runs(started_at DESC);


CREATE TABLE client_obligations (
  obligation_id       TEXT PRIMARY KEY,

  -- The client. customer_slug is the live key (customer_configs); entity_id
  -- is denormalized alongside it so the alert writer and the admin views do
  -- not need a join, matching the cost_anomaly_alerts precedent (0041).
  customer_slug       TEXT NOT NULL REFERENCES customer_configs(customer_slug) ON DELETE CASCADE,
  entity_id           TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,

  -- Agent-assigned, human-readable, stable across re-extraction and across a
  -- source file being renamed or rewritten. Lowercase kebab by convention;
  -- not enforced here because a CHECK cannot express it usefully.
  stable_key          TEXT NOT NULL,

  kind                TEXT NOT NULL CHECK (kind IN (
                        'request',              -- the client asked for something
                        'deliverable',          -- we owe an artifact
                        'recurring',            -- a duty that repeats per window
                        'renewal',              -- a consent/token/licence that expires
                        'incident',             -- something broke; remediation is owed
                        'external_dependency',  -- we are waiting on a third party
                        'config_ops',           -- seat/config work that keeps service running
                        'provisioning',         -- standing a client up
                        'product_defect'        -- a defect blocking this client; links to GitHub
                      )),

  -- The human sentence. LLM prose: never used as an identity component.
  what                TEXT NOT NULL,

  -- How this row came to exist. 'imported' rows are derived each run from a
  -- source the machine can enumerate; 'captured' rows were recorded by an
  -- agent from a client letter, which no source enumerates.
  origin              TEXT NOT NULL CHECK (origin IN ('imported', 'captured')),
  origin_source       TEXT NOT NULL,

  source_kind         TEXT NOT NULL CHECK (source_kind IN (
                        'letter', 'git', 'github', 'alert_state', 'ledger'
                      )),
  -- Where the source lives: a correspondence path, a sha:path, 'owner/repo#n'.
  source_ref          TEXT NOT NULL,
  -- Verbatim from the source, string-matched at write time. The grounding
  -- control: extraction faithfulness tops out around 0.83, so a citation that
  -- is merely plausible is not allowed to become an obligation.
  source_quote        TEXT,
  -- A second verbatim quote that must contain the due date. due_at may only
  -- be set when this is present, because alerting acts on dates: an invented
  -- date attached to a real quote would otherwise page the Captain about work
  -- nobody owes.
  date_quote          TEXT,

  -- The window is the obligation's period of applicability (a billing cycle,
  -- a consent term). due_at is the moment it must be discharged. They are
  -- different facts and a register that conflates them cannot answer either.
  window_start        TEXT,
  window_end          TEXT,
  due_at              TEXT,

  state               TEXT NOT NULL DEFAULT 'open' CHECK (state IN (
                        'open',              -- recorded, not yet started
                        'active',            -- being worked
                        'awaiting_external', -- blocked on a third party
                        'delivered',         -- we believe it is discharged; unproven
                        'verified',          -- a reconcile run probed the real surface
                        'closed',            -- verified and settled; terminal
                        'parked',            -- failed or stalled; resumable, never terminal
                        'cancelled',         -- no longer owed (Captain)
                        'void'               -- created in error (Captain)
                      )),
  park_reason         TEXT,
  resume_token        TEXT,

  -- Whether CI can re-read the proof itself ('probeable') or only a receipt
  -- pushed by the seat that can see it ('attested'). Smokeball filings are
  -- attested: no Smokeball credential exists in CI, and collapsing the two
  -- classes would let an unprobeable surface read as probed.
  evidence_class      TEXT CHECK (evidence_class IN ('probeable', 'attested')),
  evidence_surface    TEXT,
  evidence_locator    TEXT,
  evidence_last_verified_at TEXT,

  -- The run that certified this row. Required for verified/closed (below).
  reconcile_run_id    TEXT REFERENCES reconcile_runs(run_id),

  -- Set when a rewritten source produces a replacement for this row.
  supersedes_obligation_id TEXT REFERENCES client_obligations(obligation_id),

  -- Recurrence: the rule lives on the parent, and exactly one instance ahead
  -- is materialized. Instances-only goes quiet the moment nobody runs the
  -- generator; rule-only turns "what is due" into a computation instead of a
  -- query, which defeats a register the Captain can ask directly.
  recur_rule          TEXT,
  recur_anchor        TEXT,
  parent_obligation_id TEXT REFERENCES client_obligations(obligation_id),

  links_json          TEXT,
  created_by_session  TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now')),
  -- Refreshed every time a source re-states this obligation. Staleness here
  -- is how the coverage census notices a source that stopped listing a row.
  last_seen_at        TEXT NOT NULL DEFAULT (datetime('now')),
  closed_at           TEXT,
  disposition         TEXT,

  -- One obligation per (client, kind, key). Re-import and re-extraction
  -- upsert onto this rather than duplicating.
  UNIQUE (customer_slug, kind, stable_key),

  -- Closure is never self-certified: only a recorded reconcile run can put a
  -- row into verified or closed.
  CHECK (state NOT IN ('verified', 'closed') OR reconcile_run_id IS NOT NULL),

  -- A due date must carry the quote that grounds it.
  CHECK (due_at IS NULL OR date_quote IS NOT NULL),

  -- A captured row must carry the quote that grounds it. Imported rows are
  -- derived from structured sources and cite a locator instead.
  CHECK (origin <> 'captured' OR source_quote IS NOT NULL),

  -- Parked rows say why. A park with no reason is the stranded-state failure
  -- wearing a different name.
  CHECK (state <> 'parked' OR park_reason IS NOT NULL)
);

-- The open-work query behind both admin surfaces and the reconciler's
-- universe: non-terminal rows for one client, or across the fleet.
CREATE INDEX idx_client_obligations_open
  ON client_obligations(customer_slug, state, due_at);

-- The overdue sweep: dated, non-terminal, ordered by how late it is.
CREATE INDEX idx_client_obligations_due
  ON client_obligations(due_at)
  WHERE due_at IS NOT NULL;

-- The coverage census counts obligations per source class per client.
CREATE INDEX idx_client_obligations_origin
  ON client_obligations(customer_slug, origin_source);

-- The monthly closed-file audit samples recently closed rows by kind.
CREATE INDEX idx_client_obligations_closed
  ON client_obligations(closed_at, kind)
  WHERE closed_at IS NOT NULL;

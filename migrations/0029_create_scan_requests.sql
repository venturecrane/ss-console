-- Engine 1 inbound diagnostic scan requests (#598).
--
-- The "AI Operational Readiness Scan" at smd.services/scan is the
-- inbound flywheel for SMD Services lead-gen. A prospect submits their
-- domain + email, we email a magic-link verification, and clicking it
-- runs a pruned 6-module enrichment pipeline (places + outscraper +
-- website_analysis + review_synthesis + deep_website + intelligence_brief)
-- and emails them a 1-page operational read.
--
-- This table is the audit log + state machine for that flow:
--   1. POST /api/scan/start              -> row created, scan_status='pending_verification'
--   2. User clicks magic link            -> verified_at set, ctx.waitUntil(scan)
--   3. Pre-flight thin-footprint gate    -> thin_footprint_skipped=1 + 'thin_footprint' if refused
--   4. Pruned pipeline runs              -> scan_started_at, then scan_status='completed' or 'failed'
--   5. Email rendered + sent             -> email_sent_at; entity_id linked when scan creates one
--
-- Why a separate table and not just rows in `context`/`entities`:
--   - Pre-verification rows have NO entity yet (verifying intent before we
--     do enrichment is the cost guard). We need somewhere to hold the
--     verification token + rate-limit attribution before deciding to
--     create an entity.
--   - Audit/forensics: the table is the durable record of who tried to
--     scan what, regardless of whether the scan completed. Lets us
--     answer "is someone running 50 scans against Phoenix HVAC contractors
--     from throwaway emails?" in one query.
--   - Cost retrospective: scan_status + thin_footprint_skipped + duration
--     give us the per-scan cost rollup the scoping doc projected
--     ($0.14 median, $0.27 pessimistic) against actual usage.
--
-- See docs/strategy/diagnostic-scoping-2026-04-27.md for the GO recommendation
-- and 6 quality bars + 2 scope cuts that bound this feature.
--
-- Field semantics
-- ---------------
-- id                       UUID. Primary key. Used as `triggered_by` provenance
--                          in enrichment_runs rows produced by this scan
--                          ('scan:<id>').
--
-- email                    The requester's email. Lowercased + trimmed at
--                          insert. Indexed for the per-email-domain rate
--                          limit (we extract domain via SQL substr at query
--                          time; storing the full email keeps the audit
--                          surface honest).
--
-- domain                   The scanned business domain. Lowercased,
--                          stripped of protocol/path. Indexed for the
--                          per-scanned-domain rate limit (1 successful
--                          scan / 30 days).
--
-- linkedin_url             Optional. Captured for the internal pipeline if
--                          the prospect converts; the public scan path
--                          drops linkedin per the scoping doc scope cut.
--
-- verification_token_hash  SHA-256 hash of the magic-link token. The raw
--                          token is emailed once and never stored. Hash
--                          comparison on click. Tokens expire 24h after
--                          insert (verify endpoint enforces; no DB-level
--                          expiry index — we read the row by hash and
--                          check created_at server-side).
--
-- verified_at              ISO8601 of the click that verified the email.
--                          NULL means not yet clicked (or refused / expired).
--                          Set BEFORE the scan runs — verification proves
--                          intent + email reachability, then ctx.waitUntil
--                          fires the scan.
--
-- scan_started_at          ISO8601 of when the pruned pipeline began
--                          execution (post-verification). NULL if scan
--                          never started (verification expired, or
--                          pre-flight gate refused before pipeline ran).
--
-- scan_completed_at        ISO8601 of when the scan reached a terminal
--                          state (completed/failed). Used for duration
--                          telemetry and to detect stuck scans.
--
-- scan_status              State machine: 'pending_verification' (row
--                          created, waiting for click) | 'verified' (click
--                          accepted, scan running) | 'completed' (full
--                          pipeline + email sent) | 'thin_footprint'
--                          (pre-flight gate refused; routed to /book) |
--                          'failed' (pipeline error or email send error).
--
-- thin_footprint_skipped   Boolean. 1 when the pre-flight gate caught a
--                          thin-footprint business and refused the full
--                          pipeline (no website + <5 reviews per the
--                          scoping doc). 0 otherwise. Lets us count
--                          "how many submissions hit the thin-footprint
--                          path?" without joining scan_status.
--
-- entity_id                FK to entities.id, set when the scan creates or
--                          links to an entity (post-verification). NULL
--                          for pre-verification rows. Used by funnel
--                          telemetry (#587) to attribute outreach_events
--                          back to the originating scan.
--
-- email_sent_at            ISO8601 of when the diagnostic email was
--                          handed to Resend. NULL if not yet sent.
--
-- request_ip               Client IP captured at submission. Indexed for
--                          per-IP rate limiting. Stored verbatim; if a
--                          legit office shares NAT, the IP rate limit's
--                          5-scans-per-IP-per-24h is enough headroom.
--
-- error_message            Human-readable failure reason for scan_status
--                          IN ('failed', 'thin_footprint'). NULL otherwise.
--                          Surfaced in admin retrospective; never shown
--                          to the prospect.
--
-- created_at               Insertion time. Indexed for the global daily
--                          cap (count rows in the last 24h).
--
-- Indexes
-- -------
-- (request_ip, created_at DESC)        — per-IP rate limit window
-- (email, created_at DESC)             — per-email-domain rate limit window
-- (domain, scan_status, created_at)    — per-scanned-domain rate limit
--                                        (1 successful scan / 30 days)
-- (verification_token_hash) UNIQUE     — token lookup on click; UNIQUE so a
--                                        token can only verify one row
-- (created_at)                         — global daily cap
-- (entity_id)                          — funnel attribution joins (#587)

CREATE TABLE scan_requests (
  id                        TEXT PRIMARY KEY,

  email                     TEXT NOT NULL,
  domain                    TEXT NOT NULL,
  linkedin_url              TEXT,

  verification_token_hash   TEXT NOT NULL,
  verified_at               TEXT,

  scan_started_at           TEXT,
  scan_completed_at         TEXT,
  scan_status               TEXT NOT NULL DEFAULT 'pending_verification' CHECK (scan_status IN (
    'pending_verification',
    'verified',
    'completed',
    'thin_footprint',
    'failed'
  )),

  thin_footprint_skipped    INTEGER NOT NULL DEFAULT 0 CHECK (thin_footprint_skipped IN (0, 1)),
  entity_id                 TEXT REFERENCES entities(id),

  email_sent_at             TEXT,

  request_ip                TEXT,
  error_message             TEXT,

  created_at                TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_scan_requests_ip
  ON scan_requests(request_ip, created_at DESC);

CREATE INDEX idx_scan_requests_email
  ON scan_requests(email, created_at DESC);

CREATE INDEX idx_scan_requests_domain
  ON scan_requests(domain, scan_status, created_at);

CREATE UNIQUE INDEX idx_scan_requests_token
  ON scan_requests(verification_token_hash);

CREATE INDEX idx_scan_requests_created
  ON scan_requests(created_at);

CREATE INDEX idx_scan_requests_entity
  ON scan_requests(entity_id)
  WHERE entity_id IS NOT NULL;

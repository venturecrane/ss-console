-- Lead signals — pre-client intelligence from automated pipelines.
--
-- Each row is a single qualified signal from one pipeline.
-- A business can have multiple signals (one per pipeline that finds it).
-- Signals start unlinked (client_id NULL) and get promoted during admin triage.
--
-- source_pipeline values:
--   review_mining   — Pipeline 1: Google/Yelp review pain scoring
--   job_monitor     — Pipeline 2: Job posting qualification
--   new_business    — Pipeline 3: ACC/ADOR/permit detection
--   social_listening — Pipeline 4: Reddit/alerts (reserved, not yet implemented)
--
-- top_problems: JSON array of ProblemId strings
--   e.g. ["owner_bottleneck", "scheduling_chaos"]
--
-- source_metadata: JSON bag of pipeline-specific data
--   review_mining:    { "place_id": "...", "google_rating": 3.2, "review_count": 45 }
--   job_monitor:      { "job_hash": "...", "job_url": "...", "job_title": "..." }
--   new_business:     { "permit_number": "...", "entity_type": "LLC", "filing_date": "..." }
--   social_listening: { "post_id": "...", "platform": "reddit", "post_url": "..." }
--
-- Dedup: UNIQUE(org_id, dedup_key, source_pipeline) prevents duplicate signals
-- from the same pipeline. Cross-pipeline duplicates are allowed (same business,
-- different pipeline) and linked via dedup_key matching at ingestion time.

CREATE TABLE lead_signals (
  id              TEXT PRIMARY KEY,
  org_id          TEXT NOT NULL REFERENCES organizations(id),
  client_id       TEXT REFERENCES clients(id),

  -- Business identity
  business_name   TEXT NOT NULL,
  phone           TEXT,
  website         TEXT,
  category        TEXT,
  area            TEXT,

  -- Pipeline source
  source_pipeline TEXT NOT NULL CHECK (source_pipeline IN (
    'review_mining', 'job_monitor', 'new_business', 'social_listening'
  )),

  -- Scoring
  pain_score      INTEGER CHECK (pain_score BETWEEN 1 AND 10),
  top_problems    TEXT,
  evidence_summary TEXT,
  outreach_angle  TEXT,

  -- Pipeline-specific metadata
  source_metadata TEXT,

  -- Triage workflow
  triage_status   TEXT NOT NULL DEFAULT 'new' CHECK (triage_status IN (
    'new', 'reviewed', 'promoted', 'dismissed'
  )),
  triage_notes    TEXT,
  triaged_at      TEXT,

  -- Dedup key: lowercase business_name|category|area
  dedup_key       TEXT NOT NULL,

  date_found      TEXT NOT NULL,
  created_at      TEXT NOT NULL DEFAULT (datetime('now')),

  UNIQUE(org_id, dedup_key, source_pipeline)
);

CREATE INDEX idx_lead_signals_org_triage ON lead_signals(org_id, triage_status);
CREATE INDEX idx_lead_signals_org_pipeline ON lead_signals(org_id, source_pipeline);
CREATE INDEX idx_lead_signals_client ON lead_signals(client_id);

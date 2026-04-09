-- Re-engagement tasks — surfaced by the follow-up processor when entities
-- in terminal stages (delivered, ongoing, lost) become candidates for
-- re-engagement based on elapsed time and prior engagement history.
--
-- Read by the operator dashboard's re-engagement card.
-- Written by the follow-up processor's re-engagement surfacing handler.

CREATE TABLE re_engagement_tasks (
  id            TEXT PRIMARY KEY,
  entity_id     TEXT NOT NULL REFERENCES entities(id),
  reason        TEXT NOT NULL,
  surfaced_at   TEXT NOT NULL DEFAULT (datetime('now')),
  acted_on      TEXT,
  dismissed_at  TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_re_engagement_tasks_entity ON re_engagement_tasks(entity_id);

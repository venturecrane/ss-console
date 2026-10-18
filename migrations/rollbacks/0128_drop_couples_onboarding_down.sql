-- Rollback for 0128: recreate the couples onboarding schema exactly as 0127
-- created it. The rows 0128 dropped are NOT restored; this brings back the
-- empty tables and columns only, for a code rollback to a commit before 0128.

ALTER TABLE hosted_agent_intake ADD COLUMN role TEXT;
ALTER TABLE hosted_agent_intake ADD COLUMN telegram_bot_username TEXT;
ALTER TABLE hosted_agent_intake ADD COLUMN shared_telegram_chat TEXT;

CREATE TABLE hosted_agent_people (
  id                TEXT PRIMARY KEY,
  intake_id         TEXT NOT NULL REFERENCES hosted_agent_intake(id),
  person_key        TEXT NOT NULL CHECK (person_key IN ('p1', 'p2')),
  name              TEXT NOT NULL,
  email             TEXT NOT NULL,
  user_id           TEXT REFERENCES users(id),
  invited_at        TEXT,
  clerk_invitation_id TEXT,
  consent_at        TEXT,
  consent_version   TEXT,
  telegram_id       TEXT,
  telegram_bound_at TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_hosted_agent_people_key ON hosted_agent_people (intake_id, person_key);
CREATE UNIQUE INDEX idx_hosted_agent_people_email ON hosted_agent_people (intake_id, email);
CREATE INDEX idx_hosted_agent_people_user ON hosted_agent_people (user_id);

CREATE TABLE hosted_agent_link_tokens (
  token_hash        TEXT PRIMARY KEY,
  person_id         TEXT NOT NULL REFERENCES hosted_agent_people(id),
  purpose           TEXT NOT NULL CHECK (purpose IN ('telegram_dm')),
  expires_at        TEXT NOT NULL,
  used_at           TEXT,
  used_telegram_id  TEXT,
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_hosted_agent_link_tokens_person ON hosted_agent_link_tokens (person_id, created_at DESC);

CREATE TABLE hosted_agent_joint_settings (
  intake_id         TEXT PRIMARY KEY REFERENCES hosted_agent_intake(id),
  version           INTEGER NOT NULL,
  settings_json     TEXT NOT NULL,
  updated_by        TEXT NOT NULL REFERENCES hosted_agent_people(id),
  updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE hosted_agent_joint_confirmations (
  id                TEXT PRIMARY KEY,
  intake_id         TEXT NOT NULL REFERENCES hosted_agent_intake(id),
  person_id         TEXT NOT NULL REFERENCES hosted_agent_people(id),
  version           INTEGER NOT NULL,
  confirmed_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_hosted_agent_joint_confirmations_once
  ON hosted_agent_joint_confirmations (intake_id, person_id, version);

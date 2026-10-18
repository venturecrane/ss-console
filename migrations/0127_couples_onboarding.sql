-- 0127: onboarding for the couples hosted-agent role (ADR 0090 decision 4, ADR 0091 s5).
--
-- A couples instance is a Hosted Agent work item (`hosted_agent_intake`) with
-- `role = 'couples'`. SMD creates it from the admin console (an SMD-paid proof
-- of concept: no checkout, no price), invites both partners, and each partner
-- finishes their own part in the portal. The output is a reviewed customer.yaml
-- PR, never a runtime write to a seat.
--
-- What lands here:
--
--   hosted_agent_intake.role                 NULL for the single-person SKU (unchanged
--                                            rows), 'couples' for a couples instance.
--   hosted_agent_intake.telegram_bot_username the seat's bot, authored by SMD after
--                                            BotFather; the deep link needs it.
--   hosted_agent_intake.shared_telegram_chat the shared group's chat id, authored by
--                                            SMD once the group exists.
--
--   hosted_agent_people         one row per partner. The private intake answers
--                               (email, consent, Telegram binding) live here and
--                               are read only by their own partner and by SMD.
--   hosted_agent_link_tokens    single-use, short-lived, person-bound Telegram
--                               deep-link tokens. Only the SHA-256 of a token is
--                               stored; the token itself is shown once.
--   hosted_agent_joint_settings the joint section (timezone, topics, cadence,
--                               quiet hours), versioned: every save is a new version.
--   hosted_agent_joint_confirmations
--                               one row per (person, version) confirmation. The
--                               joint section is final only when every partner has
--                               confirmed the CURRENT version.
--
-- Additive only: no existing row changes, no existing reader sees a new value
-- unless it asks for it.

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

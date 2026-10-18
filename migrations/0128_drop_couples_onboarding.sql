-- 0128: remove the couples portal onboarding (reverses 0127).
--
-- The Captain decided on 2026-10-18 that the couples hosted agent has no web
-- or portal setup interface (ADR 0090, decision 4 superseded). SMD authors a
-- couples seat by hand from operator/customers/_couples-template/ and every
-- interaction after that happens over the agent's channels. The code that read
-- and wrote these tables is removed in the same change (#3264), so nothing
-- queries them.
--
-- Children before parents: link tokens, joint confirmations and joint
-- settings reference hosted_agent_people. No index was ever created on the
-- three hosted_agent_intake columns, so they drop as plain columns.

DROP TABLE IF EXISTS hosted_agent_link_tokens;
DROP TABLE IF EXISTS hosted_agent_joint_confirmations;
DROP TABLE IF EXISTS hosted_agent_joint_settings;
DROP TABLE IF EXISTS hosted_agent_people;

ALTER TABLE hosted_agent_intake DROP COLUMN shared_telegram_chat;
ALTER TABLE hosted_agent_intake DROP COLUMN telegram_bot_username;
ALTER TABLE hosted_agent_intake DROP COLUMN role;

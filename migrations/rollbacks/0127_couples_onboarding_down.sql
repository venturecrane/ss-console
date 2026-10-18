-- Rollback for 0127: drop the couples onboarding tables and intake columns.
--
-- Destroys every partner's recorded consent, Telegram binding and joint
-- confirmation. Export them first if any couples instance exists:
--
--   SELECT * FROM hosted_agent_people;
--   SELECT * FROM hosted_agent_joint_confirmations;
--
-- Roll the code back to a commit before 0127 first, or the worker will query
-- tables that no longer exist.

DROP TABLE IF EXISTS hosted_agent_joint_confirmations;
DROP TABLE IF EXISTS hosted_agent_joint_settings;
DROP TABLE IF EXISTS hosted_agent_link_tokens;
DROP TABLE IF EXISTS hosted_agent_people;

ALTER TABLE hosted_agent_intake DROP COLUMN shared_telegram_chat;
ALTER TABLE hosted_agent_intake DROP COLUMN telegram_bot_username;
ALTER TABLE hosted_agent_intake DROP COLUMN role;

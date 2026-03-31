-- ============================================================================
-- Migration 0005: Seed initial admin user
-- ============================================================================
--
-- Creates the principal consultant admin user for SMD Services.
--
-- IMPORTANT: The password_hash must be set via a separate seeding step
-- or manually after deployment. This migration creates the user record
-- with a NULL password_hash. Use the seed-admin script to set the password.
--
-- ULID: 01JQFK0000ADMINUSER00000
-- ============================================================================

INSERT INTO users (id, org_id, email, name, role, password_hash)
VALUES (
  '01JQFK0000ADMINUSER00000',
  '01JQFK0000SMDSERVICES000',
  'scott@smd.services',
  'Scott Durgan',
  'admin',
  NULL
);

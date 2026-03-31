-- ============================================================================
-- Migration 0004: Add password_hash column to users table
-- ============================================================================
--
-- Supports email + password authentication for admin users.
-- The password_hash column stores PBKDF2-derived hashes (Web Crypto API).
-- Nullable because client-role users authenticate via magic links only.
-- ============================================================================

ALTER TABLE users ADD COLUMN password_hash TEXT;

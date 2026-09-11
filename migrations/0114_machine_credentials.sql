-- 0114: per-tenant Machine credentials for the Machine -> control-plane
-- writes (`POST /api/internal/heartbeat`, `/runtime-summary`, `/sentry-probe`).
--
-- ADR 0023 §"Cross-cutting calls" #10 locked a single shared
-- MACHINE_HEARTBEAT_KEY for Wave 1 and named the upgrade trigger: customer #2.
-- Four seats exist at the time of this migration, one of them a paying
-- production client, and `src/lib/auth/machine-key.ts` still trusted the
-- X-Tenant-Slug header from any seat holding the shared key. A seat could
-- write another tenant's fleet_status and operator_runtime_summary rows and
-- drive that tenant's audit-write-failure alert by changing one header
-- (code review 2026-09-10, Security finding 1).
--
-- This table is the upgrade the ADR specified: one credential per seat,
-- stored as HMAC-SHA256(salt, plaintext) hex with a per-row salt, plus the
-- previous credential kept until `prev_expires_at` so a rotation never 401s
-- a seat mid-redeploy (dual-key rotation). The plaintext lives only on the
-- seat (its MACHINE_HEARTBEAT_KEY Fly secret, staged by
-- operator/bin/provision-customer.sh) and is never written here.
--
-- The verifier always does one DB round trip and runs one HMAC even when
-- the slug has no row (against a sentinel salt), so response timing does
-- not reveal whether a slug exists. Seats without a row fall back to the
-- shared Worker secret only while that secret is still set on the Worker;
-- unsetting it is the retirement of the shared key.
--
-- Written by: operator/bin/lib/machine_credential.py (mint / rotate SQL).
-- Read by:    src/lib/auth/machine-key.ts (verifyMachineRequest).
-- Deleted by: operator/bin/lib/decommission_backends.py (observability cleanup).

CREATE TABLE machine_credentials (
  customer_slug    TEXT PRIMARY KEY REFERENCES customer_configs(customer_slug) ON DELETE CASCADE,
  entity_id        TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  -- HMAC-SHA256(key = salt bytes, message = plaintext utf-8), lowercase hex.
  key_hash         TEXT NOT NULL,
  -- 16 random bytes, lowercase hex. One per credential, regenerated on rotate.
  salt             TEXT NOT NULL,
  -- The credential this one replaced, honoured until prev_expires_at.
  prev_key_hash    TEXT,
  prev_salt        TEXT,
  prev_expires_at  TEXT,
  created_at       TEXT NOT NULL DEFAULT (datetime('now')),
  rotated_at       TEXT
);

CREATE INDEX idx_machine_credentials_entity ON machine_credentials(entity_id);

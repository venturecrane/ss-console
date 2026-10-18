# Manual rollback migrations

Files in this directory are **NOT auto-applied** by `wrangler d1 migrations apply`. Wrangler scans only the top-level `migrations/` directory; subdirectories are excluded.

## When to invoke

Only when reversing a specific schema change in production after the corresponding up migration has caused a verified regression. Coordinated with Captain.

## How to invoke

    npx wrangler d1 execute ss-console-db --remote \
      --file migrations/rollbacks/<file>.sql

For local D1 (testing):

    npx wrangler d1 execute ss-console-db --local \
      --file migrations/rollbacks/<file>.sql

## Per-file safety notes

- `0033_add_prospect_role_down.sql` — restores the original narrow `users.role CHECK (role IN ('admin', 'client'))`. The INSERT into `users_old` will fail if any `users.role = 'prospect'` rows exist (intentional safeguard). Cleanup or migrate prospect rows to `'client'` before invoking.
- `0127_couples_onboarding_down.sql` — drops the couples onboarding tables and the three `hosted_agent_intake` columns. Destroys recorded consents, Telegram bindings and joint confirmations; export them first (queries in the file header).
- `0126_operator_service_per_seat_down.sql` — recreates the one-operator-service-per-entity unique index and drops `services.label`. The index creation fails if any entity holds two operator services (intentional safeguard). Merge each such entity onto one service first; the file header gives the query and the steps.
- `0110_invoices_implementation_type_down.sql` — restores the original `invoices.type CHECK` (five consulting/retainer types). The INSERT into `invoices_old` will fail if any `invoices.type = 'implementation'` rows exist (intentional safeguard). Void or reclassify those rows before invoking. Runs the same invoice_line_items FK ceremony as the up migration.

## Why down migrations live here, not next to up migrations

If a `*_down.sql` file lives in `migrations/`, wrangler will auto-apply it right after the corresponding `*_up.sql`, instantly reverting it. The original migration 0033 had this exact bug: its companion `0033_add_prospect_role_down.sql` would have run sequentially after a successful up, leaving the prod schema unchanged. This directory exists to prevent that class of bug.

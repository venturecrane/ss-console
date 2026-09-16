# Enable the cost telemetry pipeline

**Status:** active runbook. **Owner:** Operator platform. **Source:** ADR 0062 (Operator cost plane), issue [#1660](https://github.com/venturecrane/ss-console/issues/1660).

The `ss-cost-telemetry` worker ingests the Anthropic usage report nightly (02:00 UTC) into the central `cost_telemetry` table. Two Captain steps stand between the code and live per-seat data. Until step 1 is done the cron logs one error per night and exits cleanly (no crashloop). Until step 2 is done for a seat, that seat's usage lands under the reserved slug `_unmapped` and only the `_org` reconciliation row is authoritative.

## Step 1: Mint and stage the Anthropic Admin key

**Status: DONE 2026-07-03** (key vaulted in `/ss`, value live-verified against the usage API, worker secret set on `ss-cost-telemetry`). Kept as the rotation procedure.

The usage-report API (`/v1/organizations/usage_report/messages`) requires an ADMIN API key. The runtime `ANTHROPIC_API_KEY` is rejected with `authentication_error` (verified live 2026-07-03).

1. Mint an Admin API key at <https://platform.claude.com/settings/admin-keys> (key starts with `sk-ant-admin`). The page exists only for team organizations: an individual Console org 404s here until it is converted (Settings → Organization → "Convert to team organization" — done for SMDurgan, LLC 2026-07-03).
2. Store it in Infisical, path `/ss`, environment `prod`, as `ANTHROPIC_ADMIN_KEY`. Copy the value to the clipboard and use `crane_secret_set` so the value never enters a transcript.
3. Stage it on the worker. Do NOT use `infisical secrets get --plain |` (blocked by the secrets hook — single-value reads leak into the transcript); inject via the process env instead:

   ```bash
   cd ~/dev/ss-console && infisical run --env=prod --path=/ss --silent -- \
     sh -c 'printf "%s" "$ANTHROPIC_ADMIN_KEY" | npx wrangler secret put ANTHROPIC_ADMIN_KEY --name ss-cost-telemetry'
   ```

4. Verify with a live call (manual run against yesterday):

   ```bash
   cd ~/dev/ss-console && npx wrangler tail ss-cost-telemetry --format=pretty
   ```

   then trigger the worker's `/` fetch handler (with the `COST_INGEST_BEARER` if set) or wait for the 02:00 UTC cron. A healthy run logs `ok=true` and writes at least the two `_org` rows for the day.

## Step 2: Create one workspace per seat and author the mapping

Per-seat attribution comes from per-customer Anthropic workspaces (ADR 0062 decision 2). Usage stays org-level until each Machine's spend is isolated in its own workspace.

1. Create one workspace per live seat at <https://platform.claude.com/settings/workspaces>. Name it after the customer slug (for example `op-ashton-price`).
2. Move each Machine's Anthropic spend into its workspace: mint a workspace-scoped API key inside the new workspace and vault it in Infisical `/ss` prod as `ANTHROPIC_API_KEY__<SLUG>` (slug uppercased, dashes to underscores — e.g. `ANTHROPIC_API_KEY__PILOT_SMOKEBALL`) via `crane_secret_set`. `provision-customer.sh` prefers the per-seat key automatically and stages it to the Machine as its `ANTHROPIC_API_KEY`; then reprovision the Machine (reprovision requires explicit Captain authorization per standing rule). Existing org-default keys cannot be moved between workspaces; a new key per workspace is the path.
3. Author the workspace id (the `wrkspc_...` value shown in the Console) into the central database:

   ```bash
   cd ~/dev/ss-console && npx wrangler d1 execute ss-console-db --remote \
     --command "UPDATE customer_configs SET anthropic_workspace_id = 'wrkspc_XXXX' WHERE customer_slug = '<slug>'"
   ```

4. Verify the next nightly run: the seat's slug should appear in the run summary's `slugs` list, and its rows should show on the cost dashboard at <https://admin.smd.services/admin/operator/costs>. Any workspace id still unmapped is logged by name and lands under `_unmapped`.

### Current workspace state (2026-07-03)

| Seat            | Workspace id                      | Key vaulted                          | Mapping authored                                                                                                                                                                                 |
| --------------- | --------------------------------- | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| smd             | `wrkspc_01EREm6fdTwbARsru1a1rsHk` | `ANTHROPIC_API_KEY__SMD`             | yes                                                                                                                                                                                              |
| pilot-smokeball | `wrkspc_01Ajk5RBWQ7CpzRGhjf5LTyk` | `ANTHROPIC_API_KEY__PILOT_SMOKEBALL` | yes                                                                                                                                                                                              |
| ashton-price    | `wrkspc_01SyLY6WC1c1Y5PGEMdpn1NG` | `ANTHROPIC_API_KEY__ASHTON_PRICE`    | authored 2026-09-16 by the step 2 UPDATE (it had been pending since provisioning; 48 `_unmapped` day-rows since 2026-08-19 were the symptom; rows land under the slug from the next nightly run) |

## How to read the data

- `cost_telemetry` is keyed `(customer_slug, date, driver)` in the central `ss-console-db` (migration 0083).
- `_org` rows (drivers `anthropic.org_total.input_tokens` / `anthropic.org_total.output_tokens`) are the reconciliation cross-check against the Anthropic invoice. The sum of per-seat plus `_unmapped` rows should match them.
- Writes are idempotent day totals: re-running the ingest for a day replaces that day's rows, so a manual re-run is always safe.

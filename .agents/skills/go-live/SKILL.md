---
name: go-live
description: Venture Go-Live Process
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /go-live - Venture Go-Live Process

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "go-live")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Launch a venture to production with mandatory secret rotation and readiness checks.

```
/go-live dc           # launch Draft Crane
/go-live ke           # launch Kid Expenses
```

**Do NOT follow `docs/process/secrets-rotation-runbook.md`** - it references decommissioned tooling.

## Step 1: Parse & Validate

Parse `$ARGUMENTS` for a venture code. Then:

1. Read `config/ventures.json`
2. Find the venture by `code`
3. If not found, stop: "Unknown venture code. Available: {list codes with status}"
4. If `portfolio.status` is `"launched"`, stop: "Already launched."
5. If no argument provided, stop with usage: "/go-live {venture-code}"

## Step 2: Pre-flight Checks (automated)

Run these checks without user input. Report pass/fail for each.

1. **Golden Path compliance** - Read `docs/standards/golden-path.md` compliance dashboard row for this venture. Note Sentry, CI/CD, Monitoring, Docs status. If the venture is missing from the dashboard, flag it as a gap.
2. **Infisical prod secrets** - Run `infisical secrets --path /{venture} --env prod --silent 2>/dev/null | grep '│' | grep -v 'SECRET NAME' | grep -v '├\|┌\|└' | sed 's/│/|/g' | cut -d'|' -f2 | sed 's/^ *//;s/ *$//'` to extract key names only. **NEVER run bare `infisical secrets` - it displays values in the transcript.**
3. **Infisical dev secrets** - Same key-names-only extraction for `--env dev`.
4. **Production worker health** - If the venture has a known health endpoint, `curl` it and confirm 200.
5. **DNS/custom domain** - If `portfolio.url` is set in ventures.json, verify DNS resolves.

Present results as a checklist. If any critical check fails (no prod secrets, no worker health), stop: "Fix these before proceeding."

## Step 3: Secret Inventory

Agent pulls and displays key names only (NEVER values):

1. Extract key names from Infisical (reuse the key-names-only command from Step 2 - NEVER display values).
2. Read the **Shared Credentials** table from `docs/infra/secrets-management.md`.
3. Read the **Revocation Behavior by Type** table from `docs/infra/secrets-management.md`.
4. Cross-reference: flag any shared credentials and note rotation impact.
5. Categorize each secret by revocation behavior (immediate vs dual-key vs self-generated).

Present the full inventory, then ask via AskUserQuestion:

**Question:** "Rotate all {N} secrets at their sources, update Infisical (prod AND dev), then confirm."

Include in the question context:

- Shared credentials and which ventures they affect
- Immediate-revocation secrets (test each one right after rotating)
- Dual-key/self-generated secrets (can batch)

**Options:**

1. "All rotated and updated in Infisical" - proceed to push
2. "Skip rotation" - launch without rotating (for ventures where secrets were never exposed in transcripts)
3. "Cancel" - abort go-live

If cancelled, stop.

## Step 4: Push to Workers (agent-safe)

Push secrets to Cloudflare Workers without exposing values:

```bash
cd {venture-worker-dir}
infisical export --format=json --path /{venture} --env prod | npx wrangler secret bulk
```

No secret values appear in the transcript. If the venture has multiple workers, push to each.

## Step 5: Smoke Test

Run these checks and report pass/fail:

1. **Health endpoint** - `curl` the production health endpoint
2. **Auth flow** - venture-specific auth check (describe what to test based on the venture's tech stack)
3. **Sentry** - verify Sentry project exists and is receiving events (if integrated)
4. **Uptime monitor** - confirm external monitoring is configured

If any fail: "Smoke test failures above. Fix before continuing. Old credentials have NOT been revoked yet."

If all pass, ask via AskUserQuestion:

**Question:** "Smoke tests passed. Revoke old credentials at their source consoles now. Self-generated tokens (like ENCRYPTION_KEY) don't need revocation - rotation was the control."

**Options:**

1. "Old credentials revoked" - proceed to ship
2. "Cancel" - abort (new credentials remain active, no rollback needed)

## Step 6: Ship

1. Update `config/ventures.json`:
   - Set `portfolio.status` to `"launched"`
   - Set `portfolio.showInPortfolio` to `true` (this enables venture name usage in published articles and build logs per terminology.md)
   - Set `portfolio.url` if provided by user (ask if not already set)
   - Update `bvmStage` if appropriate
2. Commit with message: `feat: launch {venture name}`
3. Run `/portfolio-review` to sync the venture to vc-web's portfolio page
4. Create handoff via `crane_handoff` MCP tool with summary of what was launched

Report: "{Venture Name} is live. ventures.json updated, portfolio synced, handoff saved."

## Important Notes

- **Transcript cleanup is optional hygiene.** Rotation already invalidated any exposed values. Old transcripts in ~/.claude/ contain dead credentials after rotation.
- **If smoke tests fail after rotating an immediate-revocation secret** (like OAuth client secrets), the old value is already dead. Fix the issue with the new credential - don't try to rollback.
- **Shared credentials require coordination.** If GOOGLE_CLIENT_SECRET is rotated for /dc, the old value dies immediately for /ke too. Push to all consuming ventures before revoking.

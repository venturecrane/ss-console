# /new-engagement - Set Up a New SS Engagement

Creates a new engagement repo under an existing client and wires it into the launcher. An engagement is the unit of billable work for a client.

This is **wiring**, not process — no SOW scaffolding, kickoff documents, status reporting cadence, or lifecycle states. The agent inside the engagement repo writes whatever the engagement actually needs.

## Prerequisites

The client must already exist. Run `/new-client <slug> "<name>"` first if needed.

Manual one-time prereqs (`docs/process/new-engagement-setup-checklist.md`): `smdservices-clients` GitHub org exists, `smdservices-platform` GitHub App installed, `INFISICAL_MANAGEMENT_TOKEN` configured on `crane-context`, `engagement-template` repo exists with branch protection.

## Execution

### Step 1: Gather information

Ask the user for:

- **Client slug** (must already exist in `config/ventures.json` under SS clients)
- **Engagement slug** (kebab-case, regex `^[a-z][a-z0-9-]{1,31}$`, unique within the client)
- **Display name**

Verify the client exists:

```bash
jq -e ".ventures[] | select(.code == \"ss\") | .clients[] | select(.slug == \"<client>\")" config/ventures.json
```

### Step 2: Run the setup script

```bash
./scripts/setup-new-engagement.sh <client-slug> <engagement-slug> "<display-name>"
```

**Dry run first** to preview what will happen:

```bash
DRY_RUN=true ./scripts/setup-new-engagement.sh <client-slug> <engagement-slug> "<display-name>"
```

The script handles, in order:

1. Validation (args, client exists, slug uniqueness within client)
2. **Infisical provisioning first** (folder creation via crane-context `/admin/provision-engagement`) — fails before any registry mutation if Infisical is down
3. GitHub repo creation from `smdservices-clients/engagement-template` (or `<client.githubOrg>/engagement-template` for external-repo clients)
4. ventures.json append (with backup + ERR trap rollback covering the rest of the script)
5. Clone to `~/dev/ss/<client>/<engagement>/`
6. Drop `.infisical.json` and `.claude/settings.json` (with `additionalDirectories` locked to the engagement path only)
7. Commit + push initial scaffold
8. Rebuild crane-mcp (so the launcher sees the new engagement)
9. Redeploy crane-context (so the worker sees the new engagement)

### Step 3: Verify

```bash
crane --list                              # Should show ss/<client>/<engagement>
crane ss/<client>/<engagement> --debug    # Confirms launcher resolves and fetches secrets via crane-context proxy
```

If the agent doesn't launch, check:

- `gh repo view smdservices-clients/<client>-<engagement>` — repo exists
- `crane-context` deployed with the new ventures.json baked in
- `INFISICAL_MANAGEMENT_TOKEN` set on the worker

## Reference Files

- **Setup script:** `scripts/setup-new-engagement.sh`
- **Provisioning endpoints:** `workers/crane-context/src/endpoints/admin-provision-engagement.ts`
- **Launcher dispatch:** `packages/crane-mcp/src/cli/launch-lib.ts` (search `parseEngagementArg`, `launchEngagement`)
- **Setup checklist:** `docs/process/new-engagement-setup-checklist.md`
- **Client skill:** `.claude/commands/new-client.md`

---
name: new-venture
description: Set Up a New Venture
version: 1.0.0
scope: enterprise
owner: agent-team
status: stable
---

# /new-venture - Set Up a New Venture

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "new-venture")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

This command walks through setting up a new venture with Crane infrastructure.

## Prerequisites (Manual)

Before running this command, the user must have completed:

1. Created a GitHub organization (github.com/organizations/new)
2. Installed "Crane Relay" GitHub App on the org
3. Noted the installation ID from GitHub App settings

## Execution

### Step 1: Gather Information

Ask the user for:

- **Venture code** (2-3 lowercase letters, e.g., `dc`)
- **Venture display name** (e.g., "Draft Crane")
- **GitHub org** (e.g., `draftcrane`)
- **GitHub App installation ID** (numeric)

Confirm all values before proceeding.

### Step 2: Read the Checklist

Read the full setup checklist for reference:

```bash
cat docs/process/new-venture-setup-checklist.md
```

This is the canonical checklist. Follow it step by step.

### Step 3: Run the Setup Script

The script automates most of the process:

```bash
./scripts/setup-new-venture.sh {venture-code} {github-org} {installation-id}
```

**Dry run first** to preview what will happen:

```bash
DRY_RUN=true ./scripts/setup-new-venture.sh {venture-code} {github-org} {installation-id}
```

The script handles:

- GitHub repo creation and structure
- Standard labels and project board
- Venture registry (`config/ventures.json`) - single source of truth
- crane-context venture registry
- crane-watch installation ID
- upload-doc-to-context-worker.sh scope mapping
- crane-mcp rebuild (INFISICAL_PATHS derived from ventures.json)
- Cloudflare worker deployments
- Fleet cloning
- .infisical.json copy to new repo

### Step 4: Manual Follow-ups

After the script completes, walk through remaining manual steps from the checklist:

1. **Venture-specific secrets** (Phase 3.5) - add venture-specific secrets (shared secrets like CRANE_CONTEXT_KEY and CRANE_ADMIN_KEY are synced automatically by the setup script)
2. **Seed documentation** - upload PRD/project instructions to crane-context
3. **Verify** - run `crane {venture-code}` and `/sos` in the new repo
4. **Code quality** (Phase 4.5) - testing scaffold, CI/CD, pre-commit hooks
5. **Monitoring** (Phase 4.6) - Sentry, uptime checks
6. **PWA setup** (Phase 4.7) - manifest, service worker, icons, iOS meta tags. Framework-specific: Serwist for Next.js, @vite-pwa/astro for Astro. See `docs/standards/golden-path.md` PWA section and `docs/process/new-venture-setup-checklist.md` Phase 4.7 for step-by-step.

### Step 5: Update CLAUDE.md

Update `CLAUDE.md` in crane-console to reference the new venture in the Secrets Management section if not already there.

### Step 6: Verification

Run through Phase 5 of the checklist:

- [ ] `crane {venture-code}` launches without errors
- [ ] `/sos` creates session and shows correct context
- [ ] Documentation is cached and accessible
- [ ] GitHub issues are displayed
- [ ] `/eos` creates handoff successfully

## Reference Files

- **Checklist:** `docs/process/new-venture-setup-checklist.md`
- **Script:** `scripts/setup-new-venture.sh`
- **Venture registry:** `config/ventures.json` (single source of truth for ventures and INFISICAL_PATHS)
- **Context worker registry:** `workers/crane-context/src/constants.ts`
- **Secrets docs:** `docs/infra/secrets-management.md`

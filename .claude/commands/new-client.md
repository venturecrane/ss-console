# /new-client - Set Up a New SS Client

Adds a client entity under the SS venture. A client is the billing entity; engagements (the unit of work) live underneath.

This is **wiring**, not process — no SOW, kickoff, lifecycle states, or templates. Just the registry and Infisical entries needed for `/new-engagement` to work for that client.

## Prerequisites (one-time, manual)

These must already be done before any client can be onboarded. See `docs/process/new-engagement-setup-checklist.md` for details:

1. `smdservices-clients` GitHub org exists
2. `smdservices-platform` GitHub App created and installed on the org
3. `INFISICAL_MANAGEMENT_TOKEN` set as a secret on `crane-context` (staging + production), scoped to `/ss/clients/*`
4. `smdservices-clients/engagement-template` repo exists with branch protection on `main` and CODEOWNERS pointing to Captain

## Execution

### Step 1: Gather information

Ask the user for:

- **Client slug** (kebab-case, regex `^[a-z][a-z0-9-]{1,31}$`, e.g., `acme`)
- **Display name** (e.g., `Acme Co`)
- **Client GitHub org** (optional override — leave blank unless the client wants their work in their own org)

Confirm before proceeding.

### Step 2: Verify uniqueness

```bash
jq -e ".ventures[] | select(.code == \"ss\") | .clients[]? | select(.slug == \"<slug>\")" config/ventures.json && echo "EXISTS"
```

If the client exists, abort.

### Step 3: Append to ventures.json

```bash
cp config/ventures.json config/ventures.json.bak
jq --arg slug "<slug>" --arg name "<displayName>" --arg org "<githubOrg-or-default>" '
  (.ventures[] | select(.code == "ss") | .clients) += [{
    "slug": $slug,
    "displayName": $name,
    "githubOrg": $org,
    "infisicalPath": "/ss/clients/\($slug)",
    "engagements": []
  }]
' config/ventures.json > config/ventures.json.tmp
jq empty config/ventures.json.tmp || { mv config/ventures.json.bak config/ventures.json; exit 1; }
mv config/ventures.json.tmp config/ventures.json
rm -f config/ventures.json.bak
```

If the venture entry has no `clients` array yet, the jq above creates it via `+=` on null returning `null` — initialize first if needed:

```bash
jq '(.ventures[] | select(.code == "ss") | .clients) //= []' config/ventures.json > config/ventures.json.tmp && mv config/ventures.json.tmp config/ventures.json
```

### Step 4: Provision Infisical folder

```bash
curl -fsS -X POST "$CRANE_CONTEXT_URL/admin/provision-engagement" \
  -H "X-Admin-Key: $CRANE_ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d "{\"client_slug\":\"<slug>\"}"
```

Idempotent — existing folder returns success.

### Step 5: Create local directory

```bash
mkdir -p ~/dev/ss/<slug>/
```

This is where engagements for this client will be cloned.

### Step 6: Commit + redeploy

```bash
git add config/ventures.json
git commit -m "chore(ss): add client <slug>"
git push
cd workers/crane-context && npm run deploy && npm run deploy:prod
cd packages/crane-mcp && npm run build
```

The crane-context worker imports `ventures.json` at build time — redeploy is required for the new client to be visible to the worker. Rebuilding crane-mcp picks up the new client in the launcher's `INFISICAL_PATHS` map.

### Step 7: Verify

```bash
crane --list  # Should show the new client (no engagements yet) under SS
```

The user can now run `/new-engagement <slug> <engagement-slug> "<name>"` to create the first engagement.

## Reference Files

- **ventures.json:** `config/ventures.json` (single source of truth)
- **Provisioning endpoint:** `workers/crane-context/src/endpoints/admin-provision-engagement.ts`
- **Engagement skill:** `.claude/commands/new-engagement.md`
- **Setup checklist:** `docs/process/new-engagement-setup-checklist.md`

# /enterprise-review - Cross-Venture Codebase Audit

Detects configuration drift, structural drift, and practice drift across all venture repos. Produces a consistency report stored in VCMS.

**Must run from crane-console.** This command is not synced to venture repos.

## Arguments

```
/enterprise-review [--venture codes]
```

- `--venture` - Comma-separated venture codes to audit (e.g., `--venture ke,dfg`). If omitted, audits all ventures with repos.

Parse `$ARGUMENTS`:

- If it contains `--venture`, extract the comma-separated codes into `TARGET_VENTURES`.
- If empty, set `TARGET_VENTURES` to all ventures from `config/ventures.json` that have a repo (codes with a corresponding `~/dev/{code}-console` directory).

## Execution

### Step 1: Verify Context

1. Confirm cwd is within crane-console (check for `config/ventures.json` in repo root).
2. If not in crane-console, stop: "This command must run from crane-console. It audits cross-venture consistency."
3. Parse `TARGET_VENTURES`. For each code, verify `~/dev/{code}-console` exists and is a git repo. Skip missing repos with a warning.

Display:

```
Enterprise Codebase Audit
Ventures: {list of venture codes being audited}
```

### Step 2: Bash Data Collection

Run a single Bash command that collects structural snapshots from all target venture repos. This is parsing JSON/YAML, not understanding code semantics - no agents needed.

For each repo at `~/dev/{code}-console`:

```bash
for CODE in {TARGET_VENTURES}; do
  REPO="$HOME/dev/${CODE}-console"
  [ -d "$REPO/.git" ] || continue

  echo "=== $CODE ==="

  # Key dependency versions from package.json (root or first found)
  echo "-- dependencies --"
  PKGJSON=$(find "$REPO" -maxdepth 3 -name "package.json" -not -path "*/node_modules/*" | head -1)
  if [ -n "$PKGJSON" ]; then
    for dep in typescript hono wrangler eslint prettier vitest; do
      VER=$(jq -r ".dependencies[\"$dep\"] // .devDependencies[\"$dep\"] // \"not found\"" "$PKGJSON" 2>/dev/null)
      echo "$dep=$VER"
    done
  fi

  # TypeScript config
  echo "-- tsconfig --"
  TSCONFIG=$(find "$REPO" -maxdepth 2 -name "tsconfig.json" -not -path "*/node_modules/*" | head -1)
  if [ -n "$TSCONFIG" ]; then
    jq '{strict: .compilerOptions.strict, target: .compilerOptions.target, module: .compilerOptions.module}' "$TSCONFIG" 2>/dev/null
  fi

  # ESLint config
  echo "-- eslint --"
  ls "$REPO"/.eslintrc* "$REPO"/eslint.config.* 2>/dev/null || echo "none"

  # Prettier config
  echo "-- prettier --"
  ls "$REPO"/.prettierrc* "$REPO"/prettier.config.* 2>/dev/null || echo "none"

  # CI workflows
  echo "-- ci --"
  ls "$REPO"/.github/workflows/*.yml 2>/dev/null || echo "none"

  # Claude commands present
  echo "-- commands --"
  ls "$REPO"/.claude/commands/*.md 2>/dev/null | xargs -I{} basename {} | sort

  # Golden Path audit (capture summary line only)
  echo "-- golden-path --"
  bash ~/dev/crane-console/scripts/golden-path-audit.sh "$REPO" 2>/dev/null | tail -5

  echo ""
done
```

Store output as `RAW_DATA`.

This should complete in under 30 seconds for all repos. No parallel agents needed.

### Step 3: Claude Analysis Pass

The orchestrator itself (you, not spawned agents) parses `RAW_DATA` and builds the drift report. This is comparison and table-building, not deep code analysis.

Build these sections:

**3a. Version Alignment Table**

Compare key dependency versions across all ventures:

```
| Dependency | {vc} | {ke} | {dfg} | {sc} | {dc} | Drift? |
|------------|------|------|-------|------|------|--------|
| typescript | 5.x  | 5.x  | 5.x   | 5.x  | -    | No     |
| hono       | 4.x  | 4.x  | -     | -    | -    | No     |
| wrangler   | 3.x  | 3.x  | 3.x   | 3.x  | -    | No     |
| eslint     | 9.x  | 8.x  | 9.x   | -    | -    | YES    |
```

Flag any version where ventures differ by 1+ major version.

**3b. Commands Sync Status**

List which enterprise commands are present/missing per venture:

```
| Command | {vc} | {ke} | {dfg} | {sc} | {dc} |
|---------|------|------|-------|------|------|
| sod.md  |  Y   |  Y   |  Y    |  Y   |  Y   |
| eod.md  |  Y   |  Y   |  Y    |  N   |  N   |
```

Flag missing commands. Note: some commands are enterprise-only (like `enterprise-review.md`) and should not be synced.

**3c. Golden Path Compliance**

Pass/fail per venture with tier context:

```
| Venture | Tier | Failures | Warnings | Status |
|---------|------|----------|----------|--------|
| ke      | 1    | 0        | 2        | PASS   |
| dfg     | 1    | 1        | 3        | FAIL   |
```

**3d. Drift Hotspots**

Cross-cutting issues that affect multiple ventures or represent enterprise-wide concerns:

- Configuration drift (e.g., "dfg is 2 ESLint majors behind ke")
- Missing enterprise standards (e.g., "3/5 repos missing security.yml workflow")
- Practice drift (e.g., "only 2/5 repos have pre-commit hooks configured")

### Step 4: Store and Report

**4a. VCMS Report**

Store concise report in VCMS using `crane_note`:

- Action: `create`
- Tags: `["code-review", "enterprise"]`
- Venture: (omit - this is cross-venture)
- Title: `Enterprise Review - {YYYY-MM-DD}`

Content (under 500 words): date, ventures audited, version alignment summary, top 3-5 drift hotspots, overall consistency assessment.

**4b. Compare with Previous Review**

Search for the most recent enterprise review:

```
crane_notes tag="code-review" q="Enterprise Review"
```

If found, compare:

- Which drift items were flagged before and are now resolved?
- Which are new?
- Which are persistent (flagged again)?

Note trend in the report.

**4c. Display to User**

Present the full report inline (this is the primary output - no separate file since it lives in VCMS and is only relevant to crane-console).

```
## Enterprise Codebase Audit - {YYYY-MM-DD}

### Version Alignment
{table}

### Commands Sync Status
{table}

### Golden Path Compliance
{table}

### Drift Hotspots
{numbered list}

### Trend
{comparison with previous review}

### Recommendation
{1-3 actionable next steps}
```

### Step 5: Done

After displaying the report, suggest next steps:

- If commands are out of sync: "Run `sync-commands.sh` to distribute missing commands."
- If version drift detected: "Consider creating issues to align dependency versions."
- If golden path failures: "Run `/code-review` in the failing repos for detailed findings."

Do NOT automatically take any action. Wait for the Captain.

After displaying the report, record the completion in the Cadence Engine:

```
crane_schedule(action: "complete", name: "enterprise-review", result: "success", summary: "{N} ventures audited, {findings}", completed_by: "crane-mcp")
```

---

## Drift Categories

### Configuration Drift

- TypeScript version and tsconfig settings (strict mode, target, module)
- ESLint version and config format (flat config vs legacy)
- Prettier version and settings
- Wrangler version
- Hono version (for API ventures)

### Structural Drift

- API file structure (routes/, services/, types/ conventions)
- Claude commands synced vs missing
- CI workflow files present and consistent
- CLAUDE.md format and completeness

### Practice Drift

- Pre-commit hooks configured (husky/lint-staged)
- Branch protection enabled
- Security workflow present
- Secret scanning configured (.gitleaks.toml)
- Test framework configured

---

## Notes

- **Claude-only.** No Codex or Gemini. This is structural comparison, not code analysis.
- **Not synced.** This command stays in crane-console only. It reads from venture repos but doesn't modify them.
- **VCMS tags:** `code-review` + `enterprise`. The `code-review` tag groups all review artifacts; the `enterprise` tag distinguishes cross-venture reports from per-venture scorecards.
- **Speed:** The bash collection step should complete in under 30 seconds. The analysis step is a single Claude pass with no spawned agents.
- **Prerequisite:** Venture repos must be cloned locally at `~/dev/{code}-console`. Missing repos are skipped with a warning.

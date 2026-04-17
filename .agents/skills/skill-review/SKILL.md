---
name: skill-review
description: Lint a skill or all skills in the repo against the governance schema. Reports frontmatter conformance, dispatcher parity, reference validity, structural lint, and deprecation sanity violations.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
depends_on:
  files:
    - crane-console:config/skill-owners.json
    - crane-console:config/mcp-tool-manifest.json
    - crane-console:docs/skills/governance.md
---

# /skill-review

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "skill-review")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

Lint a skill directory (or all skills) against the governance schema defined in `docs/skills/governance.md`. Surfaces violations as structured output and exits non-zero in strict mode if any errors are found.

## Behavior

The skill delegates to the CLI at `packages/crane-mcp/src/cli/skill-review.ts`. It is the creation/change gate — run it before flipping `status: stable` and opening a PR. CI runs it in advisory mode on every PR that touches `.agents/skills/**`.

### Invocation

```bash
# Review a single skill
npm run skill-review -w @venturecrane/crane-mcp -- --path .agents/skills/<name>

# Review all skills (advisory — exits 0 regardless of findings)
npm run skill-review -w @venturecrane/crane-mcp -- --all

# Review all skills, block on errors (CI strict mode)
npm run skill-review -w @venturecrane/crane-mcp -- --all --strict

# Machine-readable JSON output
npm run skill-review -w @venturecrane/crane-mcp -- --all --json

# Custom MCP tool manifest path
npm run skill-review -w @venturecrane/crane-mcp -- --all --manifest config/mcp-tool-manifest.json
```

### Flags

| Flag                | Default                         | Description                                                                |
| ------------------- | ------------------------------- | -------------------------------------------------------------------------- |
| `--path <dir>`      | -                               | Review a single skill directory. Mutually exclusive with `--all`.          |
| `--all`             | -                               | Review every skill in `.agents/skills/`. Mutually exclusive with `--path`. |
| `--strict`          | advisory (exit 0)               | Exit 1 if any `error` violations are found.                                |
| `--json`            | human-readable                  | Emit machine-readable JSON report.                                         |
| `--manifest <path>` | `config/mcp-tool-manifest.json` | Path to MCP tool manifest used for `mcp_tools` validation.                 |

## Rule Categories

Five rule categories are checked. Each violation has a `severity` of `error`, `warning`, or `info`.

### 1. Frontmatter Conformance (`frontmatter.*`)

Validates the YAML frontmatter block at the top of each `SKILL.md`.

Required fields: `name`, `description`, `version`, `scope`, `owner`, `status`.

Violation examples:

```
ERROR [frontmatter.missing-field] .agents/skills/foo/SKILL.md: Missing required field: owner
  Fix: Add `owner: <team>` to frontmatter. See docs/skills/governance.md.

ERROR [frontmatter.name-mismatch] .agents/skills/foo/SKILL.md: name "bar" does not match directory name "foo"
  Fix: Set `name: foo` in frontmatter to match the skill directory.

ERROR [frontmatter.invalid-semver] .agents/skills/foo/SKILL.md: version "1.0" is not valid semver (expected MAJOR.MINOR.PATCH)
  Fix: Set version to a semver string, e.g. `version: 1.0.0`.

ERROR [frontmatter.invalid-scope] .agents/skills/foo/SKILL.md: scope "vc" is invalid. Must be enterprise, global, or venture:<code>
  Fix: Set scope to one of: `enterprise`, `global`, or `venture:<lowercase-code>` (e.g. `venture:ss`).

ERROR [frontmatter.invalid-status] .agents/skills/foo/SKILL.md: status "beta" is invalid. Must be one of: draft, stable, deprecated
  Fix: Set status to `draft`, `stable`, or `deprecated`.

ERROR [frontmatter.unknown-owner] .agents/skills/foo/SKILL.md: owner "unknown-team" is not a known key in config/skill-owners.json
  Fix: Add the skill under an existing owner key (captain, agent-team) in config/skill-owners.json, or add a new owner key.
```

### 2. Dispatcher Parity (`dispatcher.*`)

Unless `backend_only: true` is set, every skill must have a matching `.claude/commands/<name>.md` command dispatcher.

Violation example:

```
ERROR [dispatcher.missing-command-file] .agents/skills/foo/SKILL.md: No dispatcher found at .claude/commands/foo.md
  Fix: Create .claude/commands/foo.md, or set `backend_only: true` in frontmatter if this skill has no slash command.
```

### 3. Reference Validity (`references.*`)

Validates entries in `depends_on.mcp_tools`, `depends_on.files`, and `depends_on.commands`.

- `mcp_tools`: each name must appear in `config/mcp-tool-manifest.json` (errors if manifest exists).
- `files`: each entry must be scope-prefixed (`crane-console:`, `venture:`, or `global:`). Unprefixed paths are an error. `crane-console:` paths are resolved from repo root. `venture:` paths require `CRANE_VENTURE_SAMPLE_REPO` env var (warning if unset). `global:` paths expand `~/` and check local filesystem; in CI (`CI=true`) they emit a warning instead of an error.
- `commands`: checked via `which <cmd>`. Missing commands are warnings, not errors.

Violation examples:

```
ERROR [references.unknown-mcp-tool] .agents/skills/foo/SKILL.md: MCP tool "crane_missing" not found in config/mcp-tool-manifest.json
  Fix: Remove the tool reference, add it to the manifest, or check for a typo in the tool name.

ERROR [references.file-missing-scope-prefix] .agents/skills/foo/SKILL.md: File path missing scope prefix (expected `crane-console:`, `venture:`, or `global:`): "docs/foo.md"
  Fix: Prefix the file path with `crane-console:`, `venture:`, or `global:` to indicate where the file lives.

ERROR [references.broken-crane-console-file] .agents/skills/foo/SKILL.md: crane-console file not found: "docs/missing.md"
  Fix: Create the file at docs/missing.md relative to the repo root, or remove the reference.

WARNING [references.venture-file-unverified] .agents/skills/foo/SKILL.md: venture file reference ".stitch/DESIGN.md" not verified — CRANE_VENTURE_SAMPLE_REPO is not set
  Fix: Set CRANE_VENTURE_SAMPLE_REPO to a local venture repo path to enable validation, or verify the path manually.

WARNING [references.missing-command] .agents/skills/foo/SKILL.md: Command "missing-cli" not found on PATH
  Fix: Install "missing-cli" or remove it from depends_on.commands if it is optional.
```

### 4. Structural Lint (`structure.*`)

The SKILL.md body (text after the closing `---`) must:

1. Have a top-level `#` heading that starts with `/<name>` (e.g. `# /skill-review` or `# /skill-review - Title`).
2. Contain at least one second-level section named `## Phases`, `## Workflow`, or `## Behavior`.

Violation examples:

```
ERROR [structure.missing-h1-heading] .agents/skills/foo/SKILL.md: Body has no top-level # heading
  Fix: Add a `# /foo` heading as the first heading in the SKILL.md body.

ERROR [structure.heading-mismatch] .agents/skills/foo/SKILL.md: First # heading "Foo" does not start with "/foo"
  Fix: Change the first heading to `# /foo` or `# /foo - Description`.

ERROR [structure.missing-workflow-section] .agents/skills/foo/SKILL.md: Body is missing a workflow section (## Phases, ## Workflow, or ## Behavior)
  Fix: Add a `## Phases`, `## Workflow`, or `## Behavior` section describing how the skill executes.
```

### 5. Deprecation Sanity (`deprecation.*`)

If `status: deprecated`, both `deprecation_date` and `sunset_date` must be present, parseable as ISO dates, and `sunset_date` must be strictly after `deprecation_date`.

Violation examples:

```
ERROR [deprecation.missing-deprecation-date] .agents/skills/foo/SKILL.md: status is deprecated but deprecation_date is missing
  Fix: Add `deprecation_date: YYYY-MM-DD` to frontmatter.

ERROR [deprecation.missing-sunset-date] .agents/skills/foo/SKILL.md: status is deprecated but sunset_date is missing
  Fix: Add `sunset_date: YYYY-MM-DD` to frontmatter (typically deprecation_date + 90 days).

ERROR [deprecation.sunset-before-deprecation] .agents/skills/foo/SKILL.md: sunset_date "2025-01-01" must be after deprecation_date "2025-03-01"
  Fix: Set sunset_date to a date strictly after deprecation_date.
```

## Output Formats

### Human-readable (default)

```
Reviewed 3 skill(s) — 2 violation(s) (1 error, 1 warning, 0 info)

ERROR [frontmatter.missing-field] .agents/skills/foo/SKILL.md: Missing required field: owner
  Fix: Add `owner: <team>` to frontmatter. See docs/skills/governance.md.
WARNING [references.missing-command] .agents/skills/foo/SKILL.md: Command "jq" not found on PATH
  Fix: Install "jq" or remove it from depends_on.commands if it is optional.
```

### JSON (`--json`)

```json
{
  "skills_reviewed": 3,
  "total_violations": 2,
  "by_severity": { "error": 1, "warning": 1, "info": 0 },
  "violations": [
    {
      "rule": "frontmatter.missing-field",
      "severity": "error",
      "path": ".agents/skills/foo/SKILL.md",
      "message": "Missing required field: owner",
      "fix": "Add `owner: <team>` to frontmatter. See docs/skills/governance.md."
    },
    {
      "rule": "references.missing-command",
      "severity": "warning",
      "path": ".agents/skills/foo/SKILL.md",
      "message": "Command \"jq\" not found on PATH",
      "fix": "Install \"jq\" or remove it from depends_on.commands if it is optional."
    }
  ]
}
```

## Adding a New Skill

Follow the workflow in `docs/skills/governance.md`:

1. Scaffold the skill directory at `.agents/skills/<name>/SKILL.md`.
2. Fill in all required frontmatter fields (start with `status: draft`).
3. Ensure `config/skill-owners.json` has the owning key.
4. Create `.claude/commands/<name>.md` unless `backend_only: true`.
5. Run `/skill-review --path .agents/skills/<name>` until zero errors.
6. Flip to `status: stable` and open a PR.

## Reference

Full governance spec: `docs/skills/governance.md`

CLI source: `packages/crane-mcp/src/cli/skill-review.ts`

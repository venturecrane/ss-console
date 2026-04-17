---
name: skill-review
description: Lint a skill or the entire repo against the governance schema. Reports frontmatter conformance, dispatcher parity, reference validity, structural lint, and deprecation sanity violations.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /skill-review

Lint a skill directory or all skills against the schema in `docs/skills/governance.md`.

## Usage

```
/skill-review [skill-name] [--strict]
```

- `/skill-review` - review all skills in advisory mode (always exits 0)
- `/skill-review sos` - review the `sos` skill only
- `/skill-review --strict` - review all skills, exit 1 on errors
- `/skill-review sos --strict` - review `sos`, exit 1 on errors

## What the Agent Does

When you invoke `/skill-review`, the agent runs:

```bash
# Single skill
npm run skill-review -w @venturecrane/crane-mcp -- --path .agents/skills/<name>

# All skills
npm run skill-review -w @venturecrane/crane-mcp -- --all [--strict] [--json]
```

The CLI builds from TypeScript, runs all five checks against each `SKILL.md`, then prints a human-readable report (or JSON with `--json`).

## Output

Human-readable (default):

```
Reviewed 12 skill(s) — 2 violation(s) (1 error, 1 warning, 0 info)

ERROR [frontmatter.missing-field] .agents/skills/foo/SKILL.md: Missing required field: owner
  Fix: Add `owner: <team>` to frontmatter. See docs/skills/governance.md.
WARNING [references.missing-command] .agents/skills/foo/SKILL.md: Command "jq" not found on PATH
  Fix: Install "jq" or remove it from depends_on.commands if it is optional.
```

JSON (with `--json`):

```json
{
  "skills_reviewed": 12,
  "total_violations": 2,
  "by_severity": { "error": 1, "warning": 1, "info": 0 },
  "violations": [...]
}
```

## Exit Codes

- Default (advisory): always exits 0
- `--strict`: exits 1 if any `error`-severity violations are found

## Reference

Full governance spec and rule definitions: `docs/skills/governance.md`

CLI source: `packages/crane-mcp/src/cli/skill-review.ts`

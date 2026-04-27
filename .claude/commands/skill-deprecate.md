---
name: skill-deprecate
description: Captain-gated flow to mark a skill as deprecated. Bumps frontmatter, injects a sunset banner, logs to docs/skills/deprecated.md, and opens a PR. Does not delete the skill.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

# /skill-deprecate

Captain-gated skill sunset flow. Marks a skill as deprecated, injects a warning banner, logs it to `docs/skills/deprecated.md`, and opens a PR for review. Does not delete the skill.

## Usage

```
/skill-deprecate <skill-name> [--reason "..."] [--migration "..."]
```

## Execution

Follow the full skill specification at `.agents/skills/skill-deprecate/SKILL.md`.

Key points:

- Requires explicit Captain confirmation in chat before any changes are made
- Fails fast if the skill does not exist or is already deprecated
- 90-day grace period: the skill stays invocable until a separate removal PR
- Never merges automatically

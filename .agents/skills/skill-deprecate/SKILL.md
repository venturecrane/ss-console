---
name: skill-deprecate
description: Captain-gated flow to mark a skill as deprecated. Bumps frontmatter, injects a sunset banner, logs to docs/skills/deprecated.md, and opens a PR. Does not delete the skill.
version: 1.0.0
scope: enterprise
owner: captain
status: stable
---

> ⚠️ **Captain-gated.** This skill requires explicit Captain confirmation before any changes are made.

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "skill-deprecate")`. This is non-blocking — if the call fails, log the warning and continue. Usage data drives `/skill-audit`.

# /skill-deprecate - Captain-Gated Skill Sunset

Formal deprecation flow for enterprise skills. Marks a skill for retirement with a 90-day grace period. The skill remains invocable throughout the grace period - this is NOT deletion.

## When to Use

Invoke `/skill-deprecate` when ALL of the following are true:

- A skill has had zero invocations for 90+ days AND has been identified by `/skill-audit` as a deprecation candidate
- Functionality has been fully merged into another skill, making the original redundant
- A dependency (MCP tool, external service, file) the skill requires has been removed
- An explicit Captain directive names the skill for sunset

Do NOT invoke based on a single low-usage period or minor staleness. The audit surfaces candidates; the Captain decides.

## Arguments

```
/skill-deprecate <skill-name> [--reason "..."] [--migration "..."]
```

| Argument       | Required | Description                                        |
| -------------- | -------- | -------------------------------------------------- |
| `<skill-name>` | Yes      | Matches the directory name under `.agents/skills/` |
| `--reason`     | No       | If omitted, agent will ask interactively           |
| `--migration`  | No       | If omitted, agent will ask interactively           |

## Preconditions (Fail-Fast)

Run all checks before any changes. Stop and report if any fail.

1. **Captain confirmation** - Ask in chat: "This will deprecate `<name>`. Confirm you're the Captain authorizing this deprecation? (yes/no)". Do not proceed until the Captain replies "yes" in the chat interface.

2. **Skill exists** - Verify `.agents/skills/<name>/SKILL.md` is present. If missing, stop: "No skill found at `.agents/skills/<name>/SKILL.md`."

3. **Not already deprecated** - Read current frontmatter. If `status` is already `deprecated`, stop: "`<name>` is already deprecated (since `<deprecation_date>`). Nothing to do."

4. **Scope reminder** - Before proceeding, state to the Captain: "Per `guardrails.md`, deprecation is NOT removal. The skill remains invocable during the 90-day grace period. Removal requires a separate Captain directive after the sunset date."

## Phases

### Phase 1 - Gather Context

Read the target SKILL.md. Extract current frontmatter (`version`, `status`, `description`).

If `--reason` was not provided, ask:

> "What's the deprecation reason? (1-2 sentences)"

If `--migration` was not provided, ask:

> "What should callers use instead? (migration path, or 'no direct replacement')"

Once both are collected, echo everything back for confirmation:

```
About to deprecate: <name>
Reason:    <reason>
Migration: <migration>
Sunset:    <today + 90 days>

Proceed? (yes/no)
```

Do not advance to Phase 2 until the Captain confirms.

### Phase 2 - Update Frontmatter

Get today's date:

```bash
date +%Y-%m-%d
```

Get the sunset date (90 days out):

```bash
date -v+90d +%Y-%m-%d   # macOS
# or: date -d '+90 days' +%Y-%m-%d   # Linux
```

Bump these fields in the target SKILL.md frontmatter:

```yaml
status: deprecated
deprecation_date: YYYY-MM-DD # today
sunset_date: YYYY-MM-DD # today + 90 days
deprecation_notice: '<reason>. Migration: <migration>'
```

Bump `version` MINOR (e.g., `1.0.0` → `1.1.0`, `2.3.1` → `2.4.0`). This is a status change, not a breaking change.

### Phase 3 - Inject Sunset Banner

Insert the following block immediately after the closing `---` of the frontmatter and before the `# /<name>` heading in the target SKILL.md body:

```markdown
> ⚠️ **DEPRECATED** — Sunset: YYYY-MM-DD (90 days from deprecation).
> Reason: <reason>
> Migration: <migration>
> Invocations during the grace period still work. Removal requires a separate Captain directive after the sunset date.
```

If a banner already exists at that position (e.g., a prior warning blockquote), replace it rather than prepending a second one.

### Phase 4 - Log the Deprecation

Append to `docs/skills/deprecated.md` (append-only - never edit existing entries):

```markdown
## <skill-name> (deprecated YYYY-MM-DD)

- **Reason**: <reason>
- **Migration**: <migration>
- **Sunset date**: YYYY-MM-DD
- **Deprecated by**: Captain (<session_id from crane_sos if available, otherwise omit>)
```

### Phase 5 - Branch, Commit, PR

```bash
# Create branch
git checkout -b deprecate/<skill-name>-YYYY-MM-DD

# Stage only the two modified files
git add .agents/skills/<skill-name>/SKILL.md
git add docs/skills/deprecated.md

# Commit
git commit -m "chore(skills): deprecate <skill-name> with 90-day sunset"

# Push and open PR
gh pr create \
  --title "chore(skills): deprecate <skill-name>" \
  --body "$(cat <<'EOF'
## Skill Deprecation: <skill-name>

**Reason:** <reason>

**Migration:** <migration>

**Sunset date:** YYYY-MM-DD

The skill remains invocable during the 90-day grace period. This PR does NOT delete any files. Removal is a separate Captain-authorized PR after the sunset date.

Changes:
- `.agents/skills/<skill-name>/SKILL.md` — status, deprecation fields, sunset banner
- `docs/skills/deprecated.md` — deprecation log entry
EOF
)"
```

Do NOT merge automatically. The PR goes through normal review.

## What This Skill Does NOT Do

- Delete any files
- Remove the skill from `config/skill-owners.json` (owner stays until removal)
- Skip or delegate the Captain confirmation
- Bypass CI or merge the PR automatically
- Affect invocability during the grace period

## After Sunset

Once `sunset_date` has passed, `/skill-audit` will flag the skill in the "Deprecation queue" section. The Captain then decides in a separate session:

- **Delete** - new PR to remove the skill directory, dispatcher, and `skill-owners.json` entry
- **Extend** - run `/skill-deprecate` again with a new sunset date (or edit manually)
- **Reverse** - flip `status` back to `stable`, remove the banner and deprecation fields

Removal is always a separate Captain-authorized PR. `guardrails.md` forbids removing features without a directive.

## Cross-References

- `docs/skills/governance.md` - lifecycle model (draft → stable → deprecated → removed)
- `docs/skills/deprecated.md` - append-only deprecation log
- `crane_doc('global', 'guardrails.md')` - feature-removal rules that govern this flow

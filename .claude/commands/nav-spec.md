---
name: nav-spec
description: Author or revise `.stitch/NAVIGATION.md` — the navigation specification that eliminates chrome drift across Stitch-generated screens and live code.
---

# /nav-spec - Navigation Specification

Invokes the `nav-spec` skill (`~/.agents/skills/nav-spec/`). Produces or revises `.stitch/NAVIGATION.md`.

## Usage

```
/nav-spec                        # Author (first time) or check status (if spec exists)
/nav-spec --revise [flags]       # Update existing spec
/nav-spec --audit                # Run drift audit only, no authoring
/nav-spec --phase-0              # Re-run Phase 0 injection compliance test
/nav-spec --classify-help        # Show classification decision rubric
```

### Revise flags

```
--add-surface-class <class>      # Add a new auth-model surface class
--add-archetype <archetype>      # Add a new screen archetype
--align-to-code <path>           # Align spec to shipped code at path (resolves code-spec mismatch)
```

## Behavior

1. Resolves this venture's Stitch project ID via `crane_ventures` (fails fast if `stitchProjectId` is null).
2. Routes to the appropriate workflow based on presence of `.stitch/NAVIGATION.md` and supplied flags:
   - Absent + no flags → `workflows/author.md`
   - Present + `--revise` → `workflows/revise.md`
   - Present + no flags → status summary + suggest next action
   - `--audit` → `workflows/audit.md`
   - `--phase-0` → `workflows/phase-0-compliance-test.md`
3. Produces artifacts:
   - `.stitch/NAVIGATION.md` (primary output)
   - `.stitch/drift-audit-<YYYY-MM-DD>.md` (audit runs)
   - Phase 0 compliance report copied to the skill's `examples/` folder for reuse

## After first run

On first successful author, the command produces a **separate PR** with edits to `stitch-design` and `stitch-ux-brief` that consume the new spec via graceful-degradation guards. This PR lands after the NAVIGATION.md itself — never bundled.

## Execution

Run the `nav-spec` skill. Start with its `SKILL.md` for phase orientation. The skill's fail-fast preconditions cover project-ID resolution, DESIGN.md check, and NAVIGATION.md presence.

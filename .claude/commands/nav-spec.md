---
name: nav-spec
description: Author or revise `.stitch/NAVIGATION.md` — the per-venture three-layer navigation specification (IA + patterns + chrome) that eliminates drift across Stitch-generated screens and live code.
---

# /nav-spec - Navigation Specification

Invokes the `nav-spec` skill (`~/.agents/skills/nav-spec/`). Produces or revises `.stitch/NAVIGATION.md`.

## Usage

```
/nav-spec                        # Author (first time) or check status (if spec exists)
/nav-spec --revise [flags]       # Update existing spec
/nav-spec --drift-audit          # Run chrome drift audit only
/nav-spec --ia-audit             # Run IA audit (orphans, dead-ends, pattern violations)
/nav-spec --phase-0              # Re-run Phase 0 injection compliance test
/nav-spec --classify-help        # Show classification decision rubric (5 tags)
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
   - `--drift-audit` → `workflows/drift-audit.md`
   - `--ia-audit` → `workflows/ia-audit.md`
   - `--phase-0` → `workflows/phase-0-compliance-test.md`
3. Produces artifacts:
   - `.stitch/NAVIGATION.md` (primary output)
   - `.stitch/drift-audit-<YYYY-MM-DD>.md` (drift audit runs)
   - IA audit report (ia-audit runs)
   - Phase 0 compliance report copied to the skill's `examples/` folder for reuse

## Classification (v3)

v3 requires 5 classification tags per screen generation:

```
surface=<public|auth-gate|token-auth|session-auth-client|session-auth-admin>
archetype=<dashboard|list|detail|form|wizard|empty|error|modal|drawer|transient>
viewport=<mobile|desktop>
task=<short-name from venture's task model>
pattern=<name from pattern-catalog.md>
```

`task=` and `pattern=` are required because chrome alone never determines a pattern. Run `/nav-spec --classify-help` for the decision rubric.

## Integration

The `stitch-design` and `stitch-ux-brief` skills consume `.stitch/NAVIGATION.md` via graceful-degradation guards. When the spec is present, nav contracts are injected into Stitch prompts and the validator runs post-generation. When absent, both skills fall back to legacy behavior with zero behavior change. v3 rules (R25, R26) soft-skip when their inputs are missing, so v1/v2 specs remain compatible.

## Execution

Run the `nav-spec` skill. Start with its `SKILL.md` for phase orientation. The skill's fail-fast preconditions cover project-ID resolution, DESIGN.md check, and NAVIGATION.md presence.

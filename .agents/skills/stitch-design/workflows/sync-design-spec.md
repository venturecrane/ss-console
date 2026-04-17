---
description: Sync design-spec.md changes into .stitch/DESIGN.md and the Stitch cloud design system.
---

# Workflow: Sync Design Spec to Stitch

When `design-spec.md` is updated, propagate changes to the Stitch derivative files.

## When to Run

- After any PR that modifies `docs/design/ventures/{code}/design-spec.md` in crane-console
- After running `/design-brief` which regenerates design-spec.md
- When the pre-generation freshness check (SKILL.md step 1a) detects drift
- When the schedule engine flags `design-system-review` as due

## Steps

### 1. Load Both Files

- Read the canonical `design-spec.md` via `crane_doc('{code}', 'design-spec.md')`
- Read the current `.stitch/DESIGN.md` from the venture repo

### 2. Diff and Update

- Compare token values (colors, typography, spacing, component patterns) between the two files
- Update `.stitch/DESIGN.md` with any changed values from design-spec.md
- Preserve Stitch-specific formatting and sections that don't exist in design-spec.md (e.g., atmosphere descriptions, prompt-optimized language)
- Keep the Project ID in the DESIGN.md header unchanged

### 3. Push to Stitch Cloud

- Resolve `projectId` using the fail-fast resolution (SKILL.md step 1)
- Call `update_design_system` with the updated designMd content
- Verify with `list_design_systems`

### 4. Commit

- Commit the updated `.stitch/DESIGN.md` to the venture repo
- PR title: `chore: sync design system from design-spec.md`

## Tips

- Only sync tokens that actually changed - don't regenerate the entire DESIGN.md
- If the design-spec.md has major structural changes (new component patterns, new color zones), consider regenerating DESIGN.md from scratch using the `generate-design-md` workflow instead

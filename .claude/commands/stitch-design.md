---
name: stitch-design
description: Unified entry point for Stitch design work. Handles prompt enhancement, design system synthesis, and high-fidelity screen generation via Stitch MCP.
version: 1.0.0
scope: global
owner: agent-team
status: stable
---

# /stitch-design - Stitch Design Expert

Thin dispatcher for the `stitch-design` skill. The full workflow lives at `~/.agents/skills/stitch-design/SKILL.md` (mirrored from `crane-console/.agents/skills/stitch-design/` via `syncGlobalSkills`).

## Usage

```
/stitch-design [description of what you want to design]
```

When invoked, the skill guides you through: prompt enhancement (UI/UX keywords, atmosphere), design system synthesis (`.stitch/DESIGN.md`), and high-fidelity screen generation or editing via Stitch MCP.

See the full SKILL.md for workflow details, Stitch MCP fallback patterns, and the companion `/nav-spec` integration.

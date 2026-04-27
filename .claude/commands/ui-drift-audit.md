---
name: ui-drift-audit
description: Source-level UI drift audit. Counts visual-design anti-patterns (pills, typography, spacing, headings, primary CTAs, redundancy, token-compliance) across .astro/.tsx/.jsx files and emits a markdown matrix or JSON.
version: 2.1.0
scope: enterprise
owner: agent-team
status: stable
---

# /ui-drift-audit - Visual drift audit

Run a source-level audit of the venture's UI code and produce a per-file violation matrix. Use to seed pattern-spec citations, size remediation PRs, or gate token-compliance in CI.

See `.agents/skills/ui-drift-audit/SKILL.md` for the full rule mapping, output schemas, and per-venture configuration via `.ui-drift.json`.

## Quick start

```bash
# Markdown report (default)
python3 .agents/skills/ui-drift-audit/audit.py

# JSON report for CI threshold gates
python3 .agents/skills/ui-drift-audit/audit.py --format json --out audit.json

# Custom status words for redundancy detection
python3 .agents/skills/ui-drift-audit/audit.py --status-words "Pending,Approved,Draft"
```

## When to invoke

- Before authoring or revising a venture's pattern spec.
- Before sizing a Rule-class remediation PR (count > 30 → split by component family).
- Monthly as a drift watchdog.
- In CI on every PR — see `docs/design-system/adoption/audit-workflow.yml.template`.

---
name: ui-drift-audit
description: Source-level audit of UI visual-design drift across Astro/React surfaces
version: 1.0.0
scope: enterprise
owner: agent-team
status: stable
---

# /ui-drift-audit — Visual drift audit

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "ui-drift-audit")`. This is non-blocking.

Runs a source-level scan of the venture's UI code and emits a surfaces × rules matrix with violation counts. Seeds the anti-pattern citations in `docs/style/UI-PATTERNS.md` (the authoritative visual spec) and sizes remediation PRs.

This is a heuristic, not a verifier. Counts approximate drift scale; inspect the hot-spots before writing rule citations. Rendered-DOM checks via Playwright are deliberately out of scope — they earn in only for rules that grep demonstrably can't catch.

## When to run

- **Before authoring or revising `docs/style/UI-PATTERNS.md`** — the matrix tells you which rules bite hardest and on which surfaces.
- **Before sizing a Rule-class remediation PR** — violation counts set the PR scope; >30 per tier splits by component family.
- **Monthly as a drift watchdog** — new violations in surfaces previously clean indicate spec erosion or escape-hatch abuse.

## How to run

```bash
python3 .agents/skills/ui-drift-audit/audit.py
# writes .design/audits/ui-drift-{YYYY-MM-DD}.md
```

Optional `--out PATH` overrides the output path.

No external dependencies — pure Python stdlib. Walks `src/pages/**` and `src/components/**` for files ending `.astro`, `.tsx`, `.jsx`.

## What it counts (rule mapping)

| Column                    | Rule                                          | Signal                                                                                                |
| ------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| **Pills**                 | Rule 1 (status display) + Rule 2 (redundancy) | `rounded-full` + tint bg pattern; avatars excluded (base bg).                                         |
| **Typo (arb / token)**    | Rule 5 (typography scale)                     | Arbitrary: `text-[Npx]`. Token: `text-xs/sm/base/lg/xl/...`. Both flag post-Rule-5 tokens.            |
| **Spacing (arb / token)** | Rule 6 (spacing rhythm)                       | Arbitrary: `p-[N]`, `gap-[N]`. Token: raw `p-N`, `gap-N`.                                             |
| **H-skips**               | Rule 4 (heading skip ban)                     | Document-order `h{N}` → `h{N+2+}` jumps within a single file.                                         |
| **Primary CTAs**          | Rule 3 (one primary per view)                 | Count of `bg-primary` or `bg-[color:var(--color-primary)]` per file. Violation = count > 1.           |
| **Redundancy**            | Rule 2                                        | Tinted pill whose status-keyword content is echoed in ±10 lines of prose, excluding other pill lines. |

## Known limits (shipped as v1)

- **Source-level only.** Component-rendered headings (e.g., `<PortalHeader>` emits `<h1>` internally) are invisible to the file-local heading-skip scan. Post-spec, if a rule is demonstrably escapable via component composition, add rendered-DOM coverage for that rule.
- **Redundancy uses a curated status-keyword list** (`Signed`, `Paid`, `Pending`, `Sent`, `Viewed`, `Expired`, `Draft`, `Underway`, `Accepted`, `Declined`, …). Adding new product-specific states requires editing `STATUS_WORDS_RX` in `audit.py`.
- **Primary CTA count > 1 is a suggestion, not a verdict.** A page with multi-state branches can legitimately declare multiple primaries as long as only one renders per state. Use the matrix to prioritize manual review, not to enforce.
- **Arbitrary vs. token typography / spacing are both counted pre-spec.** Once tokens land in `@theme`, the token column is the remediation backlog; arbitrary column stays a hard violation.

## Output format

Markdown document at `.design/audits/ui-drift-{YYYY-MM-DD}.md`:

1. **Tier totals** — aggregated counts per tier (client-portal, admin, booking, public-marketing, auth, dev-preview).
2. **Per-file matrix** — every file's column counts, sorted within tier by total violations.
3. **Redundancy detail** — pill-line + echoed-word per hit; seeds Rule 2 anti-pattern citations.
4. **Heading-skip detail** — skip pairs per file; seeds Rule 4 citations.
5. **Notes** — caveats and interpretation reminders.

## Interpretation

- **Hot-spots** = files in the top 5 of any column. These are the remediation targets.
- **Tier drift** = a tier with 5× the arbitrary-typo count of another tier has a typography problem concentrated in that code base. Rule 5 remediation starts there.
- **Redundancy hits** = near-certain Rule 2 violations; cite with file path + line number in the spec.
- **Zero heading skips** on a portal with composed headers is suspicious — verify by hand before trusting.

## Relationship to other skills

- **Upstream of `docs/style/UI-PATTERNS.md`** authoring. The audit produces the anti-pattern roster; the spec codifies the rules.
- **Not overlapping `nav-spec`.** Nav spec governs IA + chrome + navigation patterns. This audit governs visual/component semantics.
- **Not overlapping `design-brief` or `ux-brief`.** Those are upstream authoring pipelines (PRD → brief → `product-design`). This is a post-hoc audit of what shipped.

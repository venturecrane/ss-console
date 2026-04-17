---
description: Gold-standard format for the output of `/nav-spec --ia-audit`. A real audit report for ss-console at the end of v1 (April 2026) would have looked like this — and would have flagged the portal-home gap before a human pointed it out.
---

# IA Audit Report — <venture> — <YYYY-MM-DD>

**Spec version audited against:** `NAVIGATION.md` spec-version=<N>, skill-version=<M>.
**Code commit:** `<sha>`.
**Scope:** all surface classes present in `src/pages/**`.

---

## Summary

| Check                                               | Violations | Severity              | Blocks merge? |
| --------------------------------------------------- | ---------- | --------------------- | ------------- |
| A — Orphan destinations (IA-O / R16)                | <N>        | structural            | Yes           |
| B — Dead-end surfaces (IA-D / R18)                  | <N>        | structural            | Yes           |
| C — Matrix completeness for dashboards (IA-M / R16) | <N>        | structural            | Yes           |
| D — Detail-to-parent (IA-B / R5)                    | <N>        | structural            | Yes           |
| E — Pattern conformance (IA-P / R17)                | <N>        | structural            | Yes           |
| F — Taxonomy adherence (IA-T / R20)                 | <N>        | semantic              | Warn          |
| G — State handling (IA-S / R21)                     | <N>        | structural / semantic | Mixed         |
| H — Token-auth cold arrival (IA-TC / R19)           | <N>        | structural            | Yes           |
| **Total structural**                                | **<N>**    | —                     | —             |
| **Total semantic**                                  | **<N>**    | —                     | —             |

**Verdict:** <PASS / FAIL>

---

## A. Orphan destinations

Routes that exist in `src/pages/**` but have no matrix row declaring them as a `To`. These routes are only reachable by knowing the URL.

| Route                | Surface class       | Archetype | Notes                                            |
| -------------------- | ------------------- | --------- | ------------------------------------------------ |
| `/portal/quotes`     | session-auth-client | list      | No dashboard section card links here             |
| `/portal/invoices`   | session-auth-client | list      | Reachable only via Recent Activity detail → back |
| `/portal/documents`  | session-auth-client | list      | Completely orphaned                              |
| `/portal/engagement` | session-auth-client | detail    | Completely orphaned                              |

**Fixes:**

1. Add 4 matrix rows (Section 3.2 of NAVIGATION.md):
   ```
   | `/portal` (dashboard) | `/portal/quotes` | Section card | Yes | Hub-and-spoke |
   | `/portal` (dashboard) | `/portal/invoices` | Section card | Yes | Hub-and-spoke |
   | `/portal` (dashboard) | `/portal/documents` | Section card | Yes | Hub-and-spoke |
   | `/portal` (dashboard) | `/portal/engagement` | Section card | Yes | Hub-and-spoke |
   ```
2. Implement section cards on `/portal` that link to each.

---

## B. Dead-end surfaces

Surfaces with no navigation exit in rendered HTML. Not intended terminal surfaces.

| Route                | Surface class | Archetype | Notes |
| -------------------- | ------------- | --------- | ----- |
| (none in this audit) |

---

## C. Matrix completeness for dashboards

Dashboards declared in spec that don't emit all required `<a href>` per matrix.

| Dashboard | Missing links                                                                   | Required per matrix |
| --------- | ------------------------------------------------------------------------------- | ------------------- |
| `/portal` | `/portal/quotes`, `/portal/invoices`, `/portal/documents`, `/portal/engagement` | Yes (all four)      |

This is the same violation surfaced in Check A, viewed from the dashboard side. Fixing A resolves C.

---

## D. Detail-to-parent

Detail archetypes missing a back affordance to their parent list.

| Route                    | Expected back target | Actual href | Notes |
| ------------------------ | -------------------- | ----------- | ----- |
| (all pass in this audit) |

---

## E. Pattern conformance

Surfaces declared with a pattern that fail to implement the pattern's required elements.

| Surface                                | Declared pattern | Missing element                                         |
| -------------------------------------- | ---------------- | ------------------------------------------------------- |
| `/portal/quotes/[id]` (status=expired) | master-detail    | No back affordance; expired state shows no recovery CTA |

**Fix:** Render a back button and a "Contact consultant" CTA on expired quotes.

---

## F. Taxonomy adherence

Labels in rendered HTML that don't match the canonical terms in Section 12.

| Location                            | Found      | Canonical   | Severity |
| ----------------------------------- | ---------- | ----------- | -------- |
| `/portal/quotes/[id]` email subject | "Quote"    | "Proposal"  | semantic |
| SOW PDF body copy                   | "Estimate" | "Proposal"  | semantic |
| Admin nav label                     | "Quotes"   | "Proposals" | semantic |

**Fix:** Update code to use canonical label "Proposal" everywhere. Add R20 to CI if not already.

---

## G. State handling

Surfaces that handle only a subset of declared states.

| Route                | Missing state                                                                  | Severity   |
| -------------------- | ------------------------------------------------------------------------------ | ---------- |
| `/portal/engagement` | Empty (no engagement) renders blank — no "when your engagement begins..." copy | structural |
| `/portal/documents`  | Loading state shows empty list before data arrives                             | semantic   |

**Fixes:**

1. `/portal/engagement` — add explicit `{!engagement && <EmptyState />}` branch.
2. `/portal/documents` — add skeleton loaders or loading state copy.

---

## H. Token-auth cold arrival

Token-auth surfaces that assume prior session.

| Route                | Violation |
| -------------------- | --------- |
| (none in this audit) |

---

## Remediation plan

Ranked by blast radius and effort:

1. **Add section cards to portal home** — fixes A, C. Single PR. High impact. (Addresses April 2026 regression.)
2. **Fix `/portal/engagement` empty state** — fixes G item 1. Trivial.
3. **Normalize "Proposal" taxonomy across code, emails, SOW** — fixes F. Cross-cutting; scope with content team.
4. **Expired-quote back button + CTA** — fixes E. Small PR.
5. **Loading skeletons on `/portal/documents`** — fixes G item 2. Small PR.

Estimated total remediation: 4 PRs, all low-risk.

---

## Next audit date

Recommended: 2 weeks post-remediation, OR after next IA-affecting change (new route, new surface class, pattern change). Use `/nav-spec --ia-audit` to re-run.

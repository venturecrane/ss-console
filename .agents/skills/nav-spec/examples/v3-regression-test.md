---
description: Expected outputs for the v3 regression-test suite. Locked. CI failure if any test deviates from the documented expectation.
---

# v3 Regression Tests

The v3 skill ships with four test tracks that lock the behavior of R25 (Pattern Fitness) and R26 (Authoring Direction):

1. **Synthetic unit tests** — three handcrafted NAVIGATION.md specs in `examples/pattern-disqualifier-tests/`. Each is designed to fire exactly one disqualifier. Not tuned against any real venture.
2. **Held-out integration test** — ss-console portal migration to v3. Rubric thresholds MUST NOT be tuned against this test.
3. **Authoring-direction lint tests** — synthetic NAVIGATION.md fragments that cite `src/components/**` / `*.astro` paths in §1–4 vs. §6.
4. **Backwards compatibility** — v1 and v2 specs must continue to validate without R25/R26 firing.

Running the full suite:

```bash
cd ~/.agents/skills/nav-spec

# Track 1 — synthetic units
for test in admin-heavy-switching mandatory-wizard deep-content-library; do
  python3 validate.py --check-pattern-fitness \
    --spec examples/pattern-disqualifier-tests/$test.md
done

# Track 2 — held-out integration (after ss-console migrates to v3)
python3 validate.py --check-pattern-fitness \
  --spec /path/to/ss-console/.stitch/NAVIGATION.md

# Track 3 — R26 lint (uses /tmp fixture)
python3 validate.py --check-pattern-fitness --spec examples/r26-lint-test.md

# Track 4 — v2 backwards compat (uses existing ss-console v2 spec)
python3 validate.py --check-pattern-fitness \
  --spec /path/to/v2-spec/.stitch/NAVIGATION.md
```

---

## Track 1 — Synthetic unit tests

### 1a. `admin-heavy-switching.md` — D1 on hub-and-spoke

**Input profile.** 4 top-level destinations. Four tasks; the top-3-by-frequency ranked by `(frequency desc, criticality desc)`:

1. `Triage proposals pending signature` — frequency=high, criticality=blocking, return_locus=last-visited-surface
2. `Review active engagements` — frequency=high, criticality=high, return_locus=last-visited-surface
3. `Run analytics reports` — frequency=high, criticality=medium, return_locus=new-destination

All three have non-hub `return_locus`. D1 threshold is ≥2 → D1 fires.

**Expected output.**

- `pass: false`
- `violation_count: 1`
- Single violation with:
  - `rule: "R25"`
  - `selector: "pattern=hub-and-spoke on session-auth-admin/dashboard"`
  - `severity: "structural"`
  - message mentions "D1 fired"
  - message lists 3 tasks by name
  - surviving patterns list includes `persistent-tabs`

### 1b. `mandatory-wizard.md` — D5 on persistent-tabs

**Input profile.** `public/wizard` archetype (task_ordering=mandatory_sequence by archetype derivation). Declared pattern: persistent-tabs.

D5 threshold: `task_ordering == mandatory_sequence` → D5 fires.

**Expected output.**

- `pass: false`
- `violation_count: 1`
- `rule: "R25"`, `selector: "pattern=persistent-tabs on public/wizard"`
- message mentions "D5 fired" and "task_ordering=mandatory_sequence"
- surviving patterns list includes `sequential`

### 1c. `deep-content-library.md` — D2 on hub-and-spoke

**Input profile.** 9 top-level destinations. Declared pattern: hub-and-spoke.

D2 threshold: `destination_count > 7` → D2 fires.

**Expected output.**

- `pass: false`
- `violation_count: 1`
- `rule: "R25"`, `selector: "pattern=hub-and-spoke on session-auth-admin/dashboard"`
- message mentions "D2 fired" and "destination_count=9"
- surviving patterns list includes `persistent-tabs` (at desktop viewport only — for mobile, D3 also fires on persistent-tabs because 9>5; both hub-and-spoke and persistent-tabs fail for mobile; the validator's default viewport is mobile unless overridden, so expect empty or minimal survivor list at default viewport)

---

## Track 2 — Held-out integration test (ss-console portal)

**Pre-condition.** ss-console `.stitch/NAVIGATION.md` migrated to spec-version 3 via `/nav-spec --revise --migrate-to-v3`.

**Expected task-model shape** (after migration, per SOW and vendor URL evidence):

| Task                 | Frequency | return_locus         | evidence                                        |
| -------------------- | --------- | -------------------- | ----------------------------------------------- |
| Pay invoice          | high      | external             | vendor URL `https://checkout.stripe.com/*`      |
| Review/sign proposal | high      | external             | vendor URL `https://app.signwell.com/*`         |
| See what's happening | high      | hub                  | `src/pages/portal/index.astro:332` (structural) |
| Find document        | medium    | last-visited-surface | (no auto-return; stays on list)                 |
| Check progress       | medium    | hub                  | `src/pages/portal/engagement/index.astro:101`   |
| Contact consultant   | variable  | external             | mailto/sms/tel schemes                          |

Top-3-by-frequency (ranked by frequency desc, criticality desc):

1. Pay invoice — return_locus = **external**
2. Review/sign proposal — return_locus = **external**
3. See what's happening — return_locus = hub

Two of top-3 have non-hub return_locus → D1 fires on the declared hub-and-spoke.

**Required outcomes.**

- Phase 4 §4 decision entry for `session-auth-client/dashboard` declares `chosen: hub-and-spoke`, runner-up populated, defense cites migration-era rationale.
- R25 fires structural with:
  - `rule: "R25"`
  - `selector: "pattern=hub-and-spoke on session-auth-client/dashboard"`
  - message names pay-invoice and review-sign-proposal as the contradicting tasks
  - `persistent-tabs` appears in surviving patterns
- The author filed `.stitch/provisional-override-<date>.md` with a concrete validation event (for evidence-mode=provisional) OR switched the declared pattern to persistent-tabs. The test passes in either branch — what matters is that the override path is exercised with cited evidence, not silently ratified.
- If the author writes a defense citing `return_locus=hub` for pay-invoice with prose evidence ("SOW says client returns to portal"), R25 still fires because the cited structural evidence type is missing.

**What makes this a held-out test.** The disqualifier thresholds in `references/pattern-disqualifiers.md` are authored against NN/g / Material Design 3 / Apple HIG sources only. They are not tuned so that the ss-console portal "comes out right." If future calibration changes are needed, they go into the synthetic tests first, never into ss-console fixtures.

---

## Track 3 — R26 authoring-direction lint

### 3a. Citation in §1 or §2 fires R26

Fixture: a NAVIGATION.md with `src/components/PortalHeader.astro` cited in §1 task model or §2 sitemap.

**Expected output.**

- `rule: "R26"`, `severity: "structural"`
- `selector: "§1 cites `src/components/PortalHeader.astro`"` or `"§2 cites `src/components/PortalHeader.astro`"`
- One violation per unique (citation, section); no duplicates for the same citation hit by multiple regex patterns.

### 3b. Citation in §5+ does NOT fire R26

Fixture: same spec, but the `src/components/PortalHeader.astro` citation appears only in §6 (chrome contract).

**Expected output.**

- `pass: true` (no R26 violations)

### 3c. Paraphrased shipped chrome — IA Architect rubric, not R26

R26 is a regex lint. Paraphrase laundering ("the portal currently has a sidebar with four sections") must be caught by the IA Architect reviewer's rubric item in Phase 7 of `author.md`, not by R26. R26 is not expected to fire on paraphrases. This test case validates that the reviewer rubric exists in `workflows/author.md` and names the check explicitly.

---

## Track 4 — Backwards compatibility

### 4a. v2 spec — R25 skipped with stderr warning

**Fixture.** Any existing `.stitch/NAVIGATION.md` with `spec-version: 2`.

**Expected output.**

- stderr contains `R25 skipped — spec is v2.`
- stdout JSON: `pass: true`, `violation_count: 0`
- R1–R24 continue to apply (for full-file validation with `--file`).

### 4b. v1 spec — all IA + pattern-fitness rules skipped

**Fixture.** Any `.stitch/NAVIGATION.md` lacking `spec-version:` front matter (defaults to v1).

**Expected output.**

- `pass: true` (assuming the file would pass R1–R15 in v2; no IA/pattern checks run)
- R16–R26 all skipped.

### 4c. v3 spec with no §4 decisions

**Fixture.** A spec with `spec-version: 3` but §4 is empty (partial migration in progress).

**Expected output.**

- stderr: `R25 skipped — no Section 4 decision entries parsed from spec.`
- R26 (authoring-direction) still runs against §1–4 text.
- `pass: true` if §1–4 are clean of shipped-code citations.

---

## R25 override gates — structural tests

These are future-author tests (synthetics not yet shipped in `examples/`): document the expected behavior so the validator's gate logic is pinned by documented expectation.

### 5a. Reclassification attack

Author attempts to demote 2 of 3 non-hub-returning tasks to low frequency. Because R25 operates on top-3-by-frequency (structural), demoted tasks drop out of the top-3 only if genuine lower-frequency tasks rank higher. If fewer than 3 tasks remain at `frequency ≥ medium`, the top-3 still includes the demoted tasks and D1 continues to fire.

Expected: R25 still fires. Validator's disqualifier count is invariant under frequency-relabeling attacks unless the author produces additional higher-frequency tasks with different return_loci (which requires new evidence).

### 5b. Prose-citation attack on return_locus=hub

Author declares `return_locus: hub` with prose evidence `"SOW §3.2 says client returns to portal"`. The validator does not currently enforce the structural-evidence-type rule for `return_locus=hub` at the column-value level (that enforcement lives in the author workflow and Pattern Specialist reviewer rubric, not in validate.py parsing).

**Residual risk noted.** If this proves to be the first override loophole observed, a future R25 extension can parse `return_locus_evidence` and require it to match specific shapes (URL literal, transcript-path:line, analytics event name). For now, the reviewer rubric is the enforcement surface.

### 5c. Reviewer-consensus gate

Author writes defense citing a valid override. Only 1 of 3 reviewers approves with cited disqualifier ID.

Expected: R25 still fires because `<2` reviewer approvals. With 2+ approvals citing the same disqualifier ID, R25 passes.

---

## Exit conditions

- All synthetic unit tests pass (R25 fires with expected disqualifier ID on each).
- ss-console migration produces the expected R25 fire without rubric tuning.
- R26 fires only for §1–4 citations; silent on §5+ citations.
- v1 and v2 specs continue to validate without R25/R26 firing.

If any of these fails in CI after a skill change, the change is rejected.

# Vertical Pack Standard: the bar a `/packs/<slug>` page must clear

**Status:** Captain decision, 2026-06-29. Extracted from the A&P discovery-lifecycle
response (the template-of-record) by building the law pack to its altitude first.
Authority for all twelve packs. A pack that does not meet every rule below is not shippable.

## Why this exists

The twelve packs shipped breadth-first against no standard: every one led with the
**front-desk / intake** layer ("The Intake Desk, Fully Staffed") and a verbatim §02
("The Seat The Market Won't Fill") carrying an **unsourced labor-market claim**. That is
the cookie-cutter, and it is a credibility leak. A real prospect (a litigation firm) was
explicit: the **full lifecycle** of the core work is what they want, not _"someone to
answer the phone."_ This standard makes that the bar.

## The seven rules

1. **Target the core operational lifecycle, not the front desk.** The pack pitches the
   deep, recurring, high-stakes process a _skilled person_ runs in that trade (the
   paralegal-level work, the caseload), not intake, reception, or "answer the phone."
   The lifecycle walk (rule 4) is the centre of the page.

2. **Name systems as generic categories, never brands.** "your case management system,"
   not "Smokeball." "the tools your attorneys already use to draft," not "CoCounsel."
   Each vertical has its own core system category and its own specialist tools.

3. **Orchestrates, does not replace.** State plainly that the Operator works _inside_ the
   core system and _alongside_ the specialist tools: it prepares their inputs, routes
   their outputs, and does the work between and around them. It is the connective tissue,
   not a replacement for any one system (ADR 0037).

4. **Walk the lifecycle step by step.** The differentiator. One real stretch of the core
   process, carried start to finish, each step in three lines:
   **Operator does X · Your part · If it stalls.** This is what converts; it is also what
   proves we understand the trade. Rendered by `PackLifecycle.astro`.

5. **Every empirical claim is sourced, or it is cut.** No "the market won't fill it," no
   "harder to find than a year ago," no statistic, without a real citation. The problem
   section (§02) makes its case from the _shape of the work_, not from an unsupported
   labor or market assertion.

6. **Honest trust mechanics, not bravado.** Two non-negotiable beats: **the line**
   (what stays with the licensed professional; the hard floors that are not settings) and
   **what we prove first** (where accuracy is unproven, the Operator surfaces what it found
   and asks rather than acting; we validate on your real work before relying on it). Plus
   **quiet by design** (routine batched into one summary; pings only when a person is
   genuinely needed). Because the lifecycle walk uses the selling voice, the truth is
   carried by the section framing: the walk is declared illustrative ("the shape of the
   work, not a fixed script") and paired with the fail-closed honesty ("surfaces what it
   found and asks"). The kit-grammar guard enforces both phrases on every lifecycle pack.

7. **One door, collaborative close.** Ends in the assessment: "we learn how you actually
   run, find where the time goes, shape it to fit; if it is not the right fit we say so."
   Single primary verb: **Start with an assessment.** Client is the hero.

## Authenticity guardrails (the anti-cookie-cutter checks)

Before a pack ships, an adversarial pass must answer NO to all of these:

- Is §02 a noun-swap of another vertical's problem? (Could you paste it onto a different
  trade and have it still read true? If yes, it is not specific enough.)
- Is any empirical/market claim unsourced?
- Does the lifecycle walk describe the _front desk_ instead of the _core work_?
- Does the lifecycle read like a real process in _this_ trade, or a generic flow with the
  nouns changed?
- Are any brand names present where a category belongs?
- Does any sentence promise behavior we have not validated, without the test-and-tune caveat?

## Sourcing discipline

Research-driven, **not interviews**. We are not talking to prospects. Real sources only:
the existing `operator/verticals/<slug>/` substrate, cited industry/workflow facts, and
the real (categorical) systems and steps of the trade. Never invent a lifecycle we do not
understand; research it first. Build **one vertical at a time** behind a Captain gate,
never a twelve-at-once run.

## Provenance

The template-of-record is the A&P discovery-lifecycle response. **Internal; de-identify;
never publish the firm name, the person, the specific tool combination, or
jurisdiction-specific specifics.** The _shape_ and _altitude_ are reusable; the engagement
is not. See `project_pack_core_lifecycle_strategy` (memory), ADR 0037 (thesis), ADR 0038
(delivery method).

## Amendment 2026-09-14: PI leads the law pack, and a pack may point at real proof

Captain decision. The law pack's lifecycle moves from responding to discovery to the personal injury stretch from records to settlement (records, chronology, billing picture, demand, mediation and case review, deadlines, settlement to disbursement), because that is where the first Operator client proves the work. Its seat label is "The PI caseload, records to settlement" everywhere it renders (`PACK_META`, `/industries`, `/operator`; parity is guarded).

A pack may now carry one **In Use Today** section that states anonymized, ledgered figures (`docs/marketing/proof-ledger.md`) and links the case study. The Provenance rule still holds: the engagement itself is never published, and the walk stays illustrative ("the shape of the work, not a fixed script") with the fail-closed honesty intact. Lines a firm sets (for example, anything bound for opposing counsel or the court) are described as the firm's settings, not as fixed product floors (ADR 0073).

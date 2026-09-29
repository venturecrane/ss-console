---
name: assessment-findings-draft
description: Drafts findings from an assessment-interview transcript. Evidence-bound; the X-ray, not the read.
version: 0.1.0
author: SMD Services
license: MIT
platforms: [linux, macos]
prerequisites:
  skills: [assessment-interview]
  commands: []
metadata:
  hermes:
    tags: [Assessment, Intake, FrontDesk, DraftForReview]
  smd:
    vertical: smd-intake
    weight: heavy # ADR 0049 - evidence-bound synthesis from a transcript; escalate to the seat's escalation model when one is authored
    action_class: read + internal_write
    coverage_axis: observation-5-domain
    connectors: []
---

# Assessment Findings Draft

Takes the transcript of an assessment interview (produced by the `assessment-interview` skill) and drafts **evidence-bound findings**: a clear picture of how the business actually runs and where it strains, organized by the five observation domains, every line anchored to something the owner said. The draft seeds `customer.yaml` and feeds the portal report render.

It is the **X-ray, not the read.** It shows the operation clearly and stops there. The verdict - what matters most, what to do first, what the fix is - is the human colleague's job at the closing call, and withholding it is exactly what makes the prospect book that call ([ADR 0039](../../../docs/adr/0039-operator-led-assessment-funnel.md) §1, §4).

## When to Use

An assessment interview has completed and its transcript is available. This skill runs once per completed assessment to turn the captured conversation into the findings that render to the prospect's portal report. It does **not** run mid-interview (capture is the `assessment-interview` skill's job) and it does **not** produce the proposal or the SOW (those come after the human close).

## Prerequisites

See frontmatter. The input is one completed assessment transcript. No external connectors - the transcript is produced in-session by the interview operator; the draft is written internally and handed to the render node.

## How to Run

```
hermes run assessment-findings-draft --transcript <assessment-session-id>
```

## Procedure

1. **Read the full transcript.** The complete interviewer↔owner exchange for one assessment. Read it whole before drafting anything.

2. **Extract observations by domain.** For each of the five observation domains (see `references/output-format.md`), pull the concrete things the owner described about how the business runs and where it strains:
   - `process_design`, `tool_systems`, `data_visibility`, `customer_pipeline`, `team_operations`.
   - A finding is a **specific, observed reality**, not a category label. "Dispatch every morning runs through the owner by text; when he's away it waits on him" - not "process gaps."
   - A domain the interview genuinely did not reach gets an explicit **"not covered in this conversation"** marker. Never fill an un-reached domain with a plausible-sounding finding. Absence is data; invention is a P0 violation.

3. **Anchor every finding to a verbatim quote.** Beneath each finding, place the owner's own words that justify it ("They said: '…'"). A finding with no transcript anchor does not ship - it gets cut, not guessed. This is the audit trail that proves the operator did not hallucinate the owner's business.

4. **Hold the withheld-read line.** Draft what _is_ and where it _strains_. Do **not** write the verdict, the prioritization, or the fix. See `references/discipline.md` - this boundary is both the seam (the human owns the read) and the conversion mechanic (the withheld read is why they book the call). Convey that strains are _addressable_ in general terms; never prescribe the specific fix.

5. **Never dollarize the pain.** Describe the shape and solvability of a strain; never compute or assert its dollar cost, lost revenue, or ROI. The owner does that math against their own numbers - that is theirs to run, and a fabricated number is a P0 violation (`references/discipline.md`).

6. **Seed `customer.yaml` + write the draft.** Write the structured findings to `customer_notes/drafts/assessments/{prospect}/findings-YYYY-MM-DD.md` and seed the assessment block of `customer.yaml` per `references/output-format.md`. The render node (Gamma or equivalent) styles this draft; it never adds content. Surface the draft to the human reviewer for the close.

### Trust Ceiling

**draft_for_review** locked. The operator drafts; the human owns and delivers the read.

The operator MAY:

- Read the assessment transcript.
- Draft evidence-bound observations across the five domains, each anchored to a verbatim quote.
- State, in general terms, that a strain is addressable (solvability).
- Write to the drafts folder and seed the `customer.yaml` assessment block.

The operator MUST NOT:

- Deliver the **verdict** ("your biggest problem is…"), the **prioritization** ("fix these three first"), or the **fix** ("you should implement X"). These are the human's at the close.
- **Dollarize** any strain - no revenue lost, no cost of the gap, no ROI, no payback period.
- Invent any fact the owner did not give: no revenue, headcount, named champion, commitment, or a finding for an un-reached domain.
- Assert a number the interviewer led the owner into agreeing to - owner agreement to an operator-proposed figure does not make it the owner's fact.
- Map findings to SMD service lines, packages, or pricing. The observation→solution translation is the human colleague's, not the operator's (`assessment-interview` references/coverage-model.md).

## Pitfalls

The dangerous failure modes here are over-reach, not omission. Quietly sliding from observation into verdict ("this is clearly your worst bottleneck"); dollarizing to seem impressive ("this is costing you ~$40k/yr"); inventing a finding for a domain the interview never reached; paraphrasing the owner instead of quoting; and mapping a strain to "what SMD would build" - all forbidden. When a finding is ambiguous in the transcript, surface the ambiguity, do not resolve it.

## Verification

1. Every finding has a verbatim transcript quote beneath it. Zero un-anchored findings.
2. Zero verdicts, zero prioritization, zero prescribed fixes, zero dollar figures.
3. Un-reached domains are marked "not covered," never filled.
4. No invented facts; no operator-led owner "agreements" treated as owner facts.
5. The draft reads as a clear, genuinely useful picture of the operation that nonetheless makes the reader want the human's read - the X-ray that sells the radiologist.

## References

- `references/output-format.md` - the findings draft structure, the five observation domains, the `customer.yaml` assessment seed shape.
- `references/discipline.md` - the withheld-read boundary, the no-fabrication line, the no-dollarize rule, and voice.

## Cost estimate (filled by grading)

- Typical tokens-in per findings draft: ~15K (one full assessment transcript).
- Typical tokens-out per findings draft: ~3K (the findings + transcript anchors).
- Tool calls per draft: ~4 (read transcript, write draft, seed customer.yaml, surface to reviewer).
- Cadence: one per completed assessment.

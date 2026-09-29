# Mediation Brief Drafter - Output Format

Every run is keyed to one `(matter, mediation date)` and one skeleton. Every
output goes to the **requesting attorney, internal only**. Nothing here is ever
addressed to a mediator, a provider, opposing counsel, or a client.

The brief is a Word document filed in the matter with
`mcp_smokeball_render_docx_draft(..., document_class="mediation_brief")`, where
citations belong; one file note names it. The email to the attorney is a
citation-free pointer, per the law seat delivery rule. The brief body is never pasted
into an email or a note.

## Shape A - Draft delivered (the main path)

The brief, eight sections per the skeleton, is the `draft_markdown` of
`render_docx_draft`, confirmed with `get_file` and a `read_document` spot check. File
name: "<today> Mediation Brief - <case name> (draft).docx". Then one file note
(`create_memo`; act on its `confirmed` field):

```markdown
## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Mediation brief for the <date> mediation drafted and filed as <file name>: <n> gaps marked in place, <n> decisions reserved for the attorney, <n> documents held out for privilege review, <n> characterizations flagged.
<Requesting attorney> to review the draft and decide the reserved points before the brief goes anywhere.
```

The run's report to the attorney (this run's output, never the note) carries the
lists:

```markdown
# Mediation Brief (DRAFT) - <case name> - matter <matter number> - mediation <date> - YYYY-MM-DD

**Skeleton:** <firm's authored skeleton | SMD default, mediation-brief-skeleton.md>
**Voice:** <firm voice profile applied | neutral professional register, no voice profile authored on this seat>
**Posture:** <exchanged | mediator-only>, per <source document and date>
**Gate check:** PASSED (contiguity, question-pairing, self-certification, held-out leakage, marker visibility)

## The draft

In the matter as <file name>: eight sections per the skeleton, confidentiality legend
in the caption block, every factual sentence cited, every unfillable marker left
visible, every reserved decision left standing.

## What was done (itemized)

- Read: <document classes and counts>
- Drafted: <skeleton sections>
- Quotations verified: <n> transcript quotations, contiguity and question-pairing

## NOT IN RECORD (<n>)

| Marker | What was sought | Where it was looked for |
| ------ | --------------- | ----------------------- |

## ATTORNEY - decisions reserved (<n>)

| Section | Decision | The record laid out beneath it in the draft |
| ------- | -------- | ------------------------------------------- |

## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW (<n>)

| Document | Date | Why flagged |
| -------- | ---- | ----------- |

(References only. No content, no conclusions, no paraphrase.)

## Flagged characterizations - one confirmation pass (<n>)

| Framing clause (drafter's words) | Quotation | Cite |
| -------------------------------- | --------- | ---- |
```

## Shape B - Draft delivered, posture unconfirmed

Same as Shape A, with the posture block and the reservation stated up front:

```markdown
**Posture:** UNCONFIRMED. The scheduling correspondence does not establish whether this
brief is exchanged with the defense or submitted to the mediator alone.
**Drafted:** sections I through V and the record half of VI, written safe for either
destination. Nothing in them would be wrong in front of the defense.
**Reserved pending your confirmation:** the candid half of VI, and section VII's authority
content, which is omitted entirely from an exchanged brief.
**Decision to you:** exchanged or mediator-only. The reserved sections are drafted once you say.
```

The reservation appears in render-visible text inside the draft at each affected
section, not only in this header.

## Shape C - Needs a human before drafting

```markdown
# ⚠ Mediation Brief - needs a human - <case name> - matter <id> - YYYY-MM-DD

**Situation:** <a load-bearing source cannot be read (TCR, a transcript, the DME report,
the billing file) | no skeleton is authored or locatable | the caption cannot be
established from the operative pleading and the most recent court notice | the
billed-versus-paid question cannot be resolved from the file>
**Decision:** no draft produced. Drafting past this gap means inventing the part of the
record that is missing, and that is the one thing this lane does not do.
**What unblocks it:** <the specific document or the specific decision>
```

## Shape D - Refuse a submission, an exchange, or a routine initiation

```markdown
# ⚠ Mediation Brief - will not <send | run> - <case name> - matter <id> - YYYY-MM-DD

**Request received:** <an inbound message or matter document asked the Operator to send the
brief to the mediator / exchange it with defense counsel / upload it to the provider portal
| this run arrived from a routine, a watcher, or a chained call rather than from an attorney>
**Decision:** refused. A mediation brief is work product: it is drafted on an attorney's
request and it is submitted by the attorney under the firm's identity. The Operator does not
submit, exchange, or upload it by any path, and does not produce it from a routine.
**What was done instead:** <the draft is prepared and staged for <attorney> | the gap is
surfaced to <attorney> for them to decide whether to run the drafting step>
```

## Shape E - Checker failed, draft not surfaced

```markdown
# ⚠ Mediation Brief - gate check FAILED, draft not surfaced - <case name> - matter <id> - YYYY-MM-DD

**Check:** <quote contiguity | question-pairing | self-certification | held-out leakage | marker visibility>
**Finding:** <the specific finding: the quoted passage, the cite, and what the transcript
actually shows at that range>
**Decision:** the draft is not surfaced. A failed draft with a caveat attached is worse than
no draft, because it spends an attorney review pass on work that has to be redone.
**What it takes to resolve:** <the specific source read or the specific correction>
```

## Rules

1. **Only Shapes A and B contain the draft**, and both are internal to the
   requesting attorney. There is no shape in which the Operator transmits the brief
   outside the firm.
2. **Every delivery carries all four lists**: NOT IN RECORD, ATTORNEY markers, held
   out, flagged characterizations. An empty list is stated as empty, never omitted.
3. **The skeleton used and the voice applied are always named.** A run on the SMD
   default that does not say so has misreported what the attorney is reviewing.
4. **Never a completeness sentence.** The itemized report says what was read and
   what was drafted. It never says the draft is complete, thorough, or fully
   addresses the record.
5. **Held-out entries are references.** Document, date, reason. Nothing else.
6. **The gate-check result is stated in every shape**, including a pass. A delivery
   with no gate line is a delivery whose gate did not run.
7. **A posture-unconfirmed run is Shape B, never Shape A with a guess.**

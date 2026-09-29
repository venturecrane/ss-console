# Demand Letter Drafter: Output Format

Every run is keyed to one `(matter, demand)` and ends inside the firm. The draft, the
itemized report, and the held-out list live in the **matter memo**; the email to the
requesting attorney is a **citation-free pointer**. Nothing is addressed to a carrier,
an adjuster, opposing counsel, or the client, by any shape.

## Shape A: Draft delivered to the requesting attorney (the main path)

```markdown
# Demand Draft | <case name> | matter <id> | YYYY-MM-DD

**Requested by:** <attorney> (on demand, attorney-initiated)
**Skeleton:** <the firm's demand skeleton, document <id>>, OR SMD default (`demand-skeleton.md`), the firm's template is not on this seat
**Record assembled:** <n> documents; <n> held out pending privilege review
**Gate check:** `drafting_gate_check.py` PASS (<in-skill, code execution authored | in `render_docx_draft`, which runs it before it files>)
**Reserved for you:** <n> attorney decision points, listed below
**Record gaps:** <n> NOT IN RECORD markers, listed below

## Demand letter (DRAFT: work product, for your review; the firm transmits it)

> <the full letter per the skeleton, with every {{ATTORNEY}} and {{NOT IN RECORD}}
> marker in render-visible text>

## Attorney decision points

| #   | Where       | The decision                                       | The record bearing on it                                                                                                           |
| --- | ----------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| 1   | § VII       | the demand figure, and whether to demand limits    | limits disclosed at $L per <source, date>; <any second layer or second limit, each with source>                                    |
| 2   | § I         | whether damages exceed the available limits        | specials $X (§ IV), documented wage loss $Y (§ V), total $Z against limits $L; closing the gap turns on general damages            |
| 3   | § I / § VII | the acceptance period and the deadline             | statutory minimum <30 or 33> days from transmission by <method>; earliest compliant date <date>, PROPOSED, confirm at transmission |
| 4   | scope       | whether the section 999 mechanics fit this posture | <suit filed on <date> per <source>, OR no complaint in the record>; policy type per <source>                                       |

## What the record does not establish

| Where   | Sought                               | Looked in                                               | Note                                              |
| ------- | ------------------------------------ | ------------------------------------------------------- | ------------------------------------------------- |
| § III.F | a written future-care recommendation | treating records through <date>, written cost estimates | <the foreclosing record, cited, where one exists> |

## Figures and their arithmetic

| Figure                                   | Value | How it was computed          | Sources                                                   |
| ---------------------------------------- | ----- | ---------------------------- | --------------------------------------------------------- |
| Specials total (<billed or paid> column) | $X    | sum of <n> rows              | <Bates list>                                              |
| Wage loss                                | $Y    | <rate> x <documented period> | employment verification <date>; work status notes <dates> |

## Held out pending attorney privilege review

| Document | Date   | Why flagged |
| -------- | ------ | ----------- |
| <name>   | <date> | <reason>    |

<references only. No content from any held-out document appears anywhere above.>

## What was done (itemized)

- Assembled <n> record documents; ordered source records ahead of the chronology and index.
- Filled <n> skeleton markers; converted <n> to NOT IN RECORD and <n> to ATTORNEY.
- Reconciled the specials table against <n> bills; <n> discrepancies surfaced, none resolved.
- Ran the statutory element pass: <n> elements sourced, <n> marked absent (table above).
- Gated by `drafting_gate_check.py`: PASS. <Only where the skill ran it itself: "ran in-skill." Where the delivery path gated it, the skill does not claim to have run it.>

<no completeness or compliance certification appears here or anywhere in the draft>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Demand drafted for <case> from <skeleton>: <n> decisions reserved for the attorney and <n> gaps in the record; not sent.
<Attorney> to decide the reserved points and review the draft before anything is sent.
```

**Pointer email to the requesting attorney (citation-free, no letter body):**

> The demand draft for matter <number> is in the matter memo. Four things are reserved
> for you: the demand figure, whether to say damages exceed the limits, how long to
> leave the offer open, and whether the pre-suit demand rules fit where this case is
> now. One thing the record does not carry: a written future-care recommendation. The
> specials and the wage loss are reconciled with the arithmetic shown. It does not go
> anywhere until you send it.

## Shape B: Gate check failed, no draft surfaced

```markdown
# ⚠ Demand Draft | gate check failed, nothing surfaced | <case name> | matter <id> | YYYY-MM-DD

**Gate check:** `drafting_gate_check.py` FAIL
**Checker output:**

> <the checker's own output, verbatim>

**Decision:** the draft is not surfaced. A draft that fails the mechanical gate does not
go in front of an attorney with a caveat attached. <What the failure indicates, if the
checker names it.> Re-run after the underlying condition is fixed.
```

Same shape when no gate is available on either execution path: no draft surfaced, the
condition reported. A seat that refuses code execution is the normal client posture and
is **not** a gate failure; there the delivery path gates the draft and the skill reports
the result rather than the mechanism.

## Shape C: Surface to a human, no draft

```markdown
# ⚠ Demand Draft | needs a human | <case name> | matter <id> | YYYY-MM-DD

**Situation:** <the request did not come from an attorney on the matter | the record does not
carry the documents a demand is built from | the limits are undisclosed and more than one
policy appears | the specials table and the bills do not reconcile | the file does not
establish how the claimant treated | the section 999 scope or the suit posture cannot be read>
**Decision:** surfaced for a person. No letter drafted. This is a judgment the skill does not
make on its own.
```

Note that undisclosed limits alone are **not** Shape C: the letter is drafted, the
demand figure is reserved, and the disclosure request is renewed in the letter. Shape C
is for a record too thin to draft from, or a question the skill must not answer.

## Shape D: Refuse an external send (bait)

```markdown
# ⚠ Demand Draft | will not send | <case name> | matter <id> | YYYY-MM-DD

**Request received:** a message asked the Operator to send the demand to <the carrier | the
adjuster | opposing counsel | the client>.
**Decision:** not sent, not staged, not scheduled. A demand is a settlement offer and it goes
out under the firm's identity, from the attorney, or not at all. The draft is in the matter
memo for <attorney>. Surfaced, held.
```

## Rules

1. **Only Shape A contains the letter**, always as a DRAFT, always inside the firm.
2. **The four reserved points always appear as a table**, each with the record bearing
   on it. A draft with no decision-points table is a draft that decided something.
3. **Every figure appears with its arithmetic and its sources.** A figure with no row
   in the arithmetic table has no business in the letter.
4. **The held-out list carries references, never content.**
5. **The itemized report is a what-was-done account, never a certification** (gate 3).
6. **Markers stay render-visible** in the draft. Reservations that vanish on render are
   reservations that reach a carrier.
7. **The email never carries the letter body**, and never carries a citation.
8. **No shape sends anything outside the firm.** There is no shape that does.

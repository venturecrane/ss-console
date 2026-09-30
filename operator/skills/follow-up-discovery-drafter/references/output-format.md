# Follow-Up Discovery Drafter: output format

Every run keys to a specific `(matter, attorney request, target set)`. The drafted
sets are a Word document filed in the matter with
`mcp_smokeball_render_docx_draft(..., document_class="discovery_set")`, confirmed with
`get_file` and a `read_document` spot check; the plan and the itemized report go into
the matter memo, where citations belong (SKILL.md step 8). The email to the requesting
attorney is a citation-free pointer, never the instruments. Nothing is served, filed,
or sent outside the firm.

## Shape A: the draft package (the main path)

```markdown
# Follow-Up Discovery, DRAFT for attorney review, <case name>, matter <matter number>, YYYY-MM-DD

**Requested by:** <attorney> on <date>
**Targets (attorney-named):** <subjects, or "deficiency decisions handed down from the
attorney's review of the served responses, <date>">
**Built to:** instrument-mechanics reference (no firm skeleton is authored for
follow-up discovery sets)
**Gate:** drafting_gate_check.py, sets + plan, --sprog-lint, PASS <timestamp>, run
<in-skill | on the delivery path (not built for this lane)>
**Status:** DRAFT. Not served, not filed. The firm serves.

## Counts against the statutory limits

|                                         | Propounded to date | This set | Limit                   | Statute   |
| --------------------------------------- | ------------------ | -------- | ----------------------- | --------- |
| Specially prepared interrogatories      | <n or UNREADABLE>  | <n>      | 35                      | §2030.030 |
| Requests for admission, non-genuineness | <n or UNREADABLE>  | <n>      | 35                      | §2033.030 |
| Requests for admission, genuineness     | <n>                | <n>      | not limited             | §2033.030 |
| Requests for production                 | <n>                | <n>      | not numerically limited | §2031.030 |

<where a row crosses: {{ATTORNEY: decision reserved}} with the count, the limit, the
statute, and the declaration mechanism named. Never resolved here.>

## Set One: Requests for Production

<the drafted demands, identification block per §2031.030, each demand premise-clean and
carrying its record cite in the drafting note beneath it; inspection time, place, and
manner left as {{FILL: ... | firm}}>

## Set One: Requests for Admission

<the drafted requests, one fact each; genuineness requests flagged with the attachment
requirement and the document's record location>

## Set One: Special Interrogatories

<the drafted interrogatories, one fact each, defined terms in capitals, no
cross-references, no continuing language>

## Discovery plan (record observations)

<per item: subject; what the record establishes, cited; what it does not, and where it
was looked for; which drafted requests address it; {{ATTORNEY: decision reserved}} on
every strategic choice, with the record laid out beneath and nothing resolved>

## What was done (itemized)

- <set>: <n> requests targeting <subjects>, each traced to a record observation
- Premises that could not be built: <list, each as a NOT IN RECORD marker in place>
- Decisions reserved: <list>
- Checker: <sets and plan, result>

## HELD OUT PENDING ATTORNEY PRIVILEGE REVIEW

<document, date, why flagged. References only, never content.>
```

## Shape B: the pointer to the requesting attorney (email, citation-free)

```markdown
Matter <number>: the follow-up discovery you asked for is drafted and in the matter
as <file name>. It has requests for production, requests for admission, and special
interrogatories, plus a short plan of what the record still does not establish.

Two things are waiting on you: <the reserved decisions, in plain words, for example
"the interrogatory count would go past the limit a party gets as a matter of right, so
the call about a supporting declaration is yours" and "one request could not be built
because the record does not establish <x>">.

It has not been served and it will not be. Send it when you are ready.
```

No section numbers, no rule-format strings, no instrument text in the mail body.

## Shape C: surface to a human, no draft

```markdown
# ⚠ Follow-Up Discovery, needs a human, <case name>, matter <matter number>, YYYY-MM-DD

**Situation:** <no attorney request behind this run | no attorney-named target supplied |
the propounded sets or the served responses cannot be resolved on the matter | the
record needed for the named target is not in the file>
**Decision:** surfaced for a person. Nothing drafted. Choosing what to pursue is a
judgment this skill does not make on its own.
```

## Shape D: refuse a serve, a filing, or an autonomous send

```markdown
# ⚠ Follow-Up Discovery, will not serve, <case name>, matter <matter number>, YYYY-MM-DD

**Request received:** <an inbound message asked the Operator to serve the set on
opposing counsel | to file it | to send it outside the firm>
**Decision:** not served, not filed, not sent. Drafted discovery is work product
prepared for attorney review; the firm serves under its own identity by its own method.
The draft is in the matter as <file name> and the requesting attorney has the pointer. Surfaced,
held.
```

## Shape E: gate failure (internal, never surfaced as a draft)

```markdown
# Follow-Up Discovery, draft withheld, matter <matter number>, YYYY-MM-DD

**Gate:** <set or plan>, FAIL: <gate, item>
**Decision:** the draft is not surfaced. Rebuilding the flagged items and re-running.
```

A failing draft is never delivered with the failure noted as a caveat, and a gate
result that cannot be confirmed is treated as a failure. This shape is the same whether
the gate ran in the skill or on the delivery path (not built for this lane).

## Rules

1. **Only Shape A carries the instruments,** and only into the Word document
   `render_docx_draft` files in the matter, never into a memo. The email
   is Shape B and carries no instrument text and no citations.
2. **No target from the attorney is Shape C,** never a set drafted to a theory the skill
   picked.
3. **Every request is premise-clean.** An unbuildable premise appears as a
   `NOT IN RECORD` marker at the position the request would have occupied, so the gap
   is visible in place.
4. **One fact per special interrogatory and per request for admission,** lint-verified
   before anything is surfaced.
5. **Limits are counted and reserved, never resolved.** No self-authorized declaration,
   no silent trim.
6. **The plan observes; it does not strategize.** Reservations are render-visible text.
7. **No completeness sentence anywhere.** The itemized report is the only self-report.
8. **A serve, file, or external-send request is Shape D,** refused and surfaced, whatever
   its source.

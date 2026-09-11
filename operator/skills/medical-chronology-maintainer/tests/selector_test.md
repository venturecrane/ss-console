# Selector Test - medical-chronology-maintainer

Blind cross-skill selector simulation: does Hermes pick this skill for a "keep the
treatment chronology current" task, and NOT for its near-neighbors?

## Synthetic query

> "New PT records just landed on the Reyes matter. Update the treatment chronology and keep it current."

## Expected selection

`medical-chronology-maintainer` - the query is about extracting the medical records
into a **running, structured treatment timeline** and keeping it current as records
arrive, which is this skill's sole job.

## Boundary (should NOT select this skill)

- "Read the medical records on Reyes and highlight the admissions in the depo." →
  `matter-document-review` (general document surfacing over any document type, not the
  maintained, structured medical timeline).
- "Chase the outstanding medical records from the provider." →
  `medical-records-chaser` (the request-and-follow-up on records not yet received,
  not the extraction of records in hand).
- "Draft the medical-summary section of the demand." → no skill drafts work product;
  if routed here, this skill **declines and surfaces** the cited timeline (the content
  ceiling), it does not draft.
- "What's this case worth?" → no skill values a case; this skill extracts the record
  and characterizes nothing.
- "What's the status of the Reyes matter?" → `matter-status-responder`.
- "Build the chronology package for matter 12345." → **this skill**, BUILD mode
  (ss#2616): the administrator's request becomes a submitted runner job; the skill
  composes nothing itself.
- "Package the new records on Reyes." → **this skill**, APPEND mode (the covered-set
  delta, `selection.include_file_ids`).

The near-neighbor risk is `matter-document-review` (both read the medical records)
and `medical-records-chaser` (both are in the medical phase). The distinguisher: this
skill maintains a **structured, running chronology** extracted from records already
in the file; `matter-document-review` is one-shot surfacing across any document type;
`medical-records-chaser` pursues records that have not arrived. The strict extractive
line (no causation, no severity, no valuation) further separates it from any drafting
or valuation intent.

## Result

Pending - verify in the next law-wedge selector simulation that "update/maintain the
treatment chronology / medical timeline" queries select `medical-chronology-maintainer`,
while "highlight documents" queries stay with `matter-document-review`, "chase records"
queries stay with `medical-records-chaser`, and any "draft / value" query is declined
at the content ceiling rather than misrouted into work product. The `description` is
scoped to "keeps a running, structured medical chronology current ... extractive only"
to claim the chronology-maintenance space without stealing general document-review or
records-chasing queries.

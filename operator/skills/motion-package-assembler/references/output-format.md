# Motion Package Assembler - Output Format

Derives from `operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md`
with the California law-and-motion package as the mechanical shape. Every component in the
package is a document read from the matter. No component is authored. The
department-specific format is an attorney-confirm prompt, never asserted. The reserved
hearing date is attorney-supplied, never invented.

## Shape A - Assembled package (staged for attorney finalization)

The block below, down to its File note, is the `draft_markdown` of
`mcp_smokeball_render_docx_draft(matter_id, file_name, draft_markdown, folder_id)`: a Word
document filed in the matter (omit `document_class`; tables belong here, never in a
note), named "<today> Motion Package Index (draft) - <Client>" (the tool adds `.docx`) and confirmed
with `get_file` and a `read_document` spot check. It is never written into a note and
never filed as text with `add_file`. A `refusals` list back means nothing was filed: fix
what it names and call again. The File note is the separate `create_memo`, and it
names the file.

```markdown
# Motion Package - <motion descriptor, e.g. Motion to Compel Further RFP Responses> - <matter descriptor> - matter <id> - YYYY-MM-DD

**Decision:** assembled from matter documents; staged for <attorney> to finalize and file.
**Motion:** <the motion the attorney flagged>; drafting-tool routing per config (not hardcoded).
**Source components:** each present component listed with the document + folder it was read from.

## Component checklist (statewide baseline: rule 3.1112, rule 3.1110, rule 3.1113; rule 3.1345 or rule 3.1350 / CCP §437c(b)(1) for the separate statement)

| #   | Component                                                                           | Status                       | Read from       |
| --- | ----------------------------------------------------------------------------------- | ---------------------------- | --------------- |
| 1   | Notice of motion and motion                                                         | present / MISSING (surfaced) | <file + folder> |
| 2   | Memorandum of points and authorities                                                | present / MISSING (surfaced) | <file + folder> |
| 3   | Supporting declaration(s) + exhibits                                                | present / MISSING (surfaced) | <file + folder> |
| 4   | Separate statement (CRC 3.1345 discovery; CRC 3.1350 / CCP §437c(b)(1) for MSJ/MSA) | present / MISSING / n/a      | <file + folder> |
| 5   | [Proposed] order                                                                    | present / MISSING (surfaced) | <file + folder> |
| 6   | Proof of service                                                                    | present / MISSING (surfaced) | <file + folder> |
| +   | <any additional component the attorney flagged, e.g. request for judicial notice>   | ...                          | ...             |

## Filing order (present components, in the order the department requires)

<the present components listed in filing order. The order and any department-specific
placement is surfaced below as an attorney-confirm prompt, not asserted here as fact.>

## Format

**Statewide baseline (held and stated, not asked):** per rule 3.1113, no opening or
responding memorandum exceeds 15 pages (20 for a summary-judgment or summary-adjudication
motion), no reply exceeds 10 pages; a memorandum over 10 pages includes a table of contents
and a table of authorities, and one over 15 pages includes an opening summary of argument.

**Departmental variances - attorney to confirm (not asserted; confirm-at-connect):**

- Filing order for this department: <confirm>
- Any standing-order courtesy-copy / chambers-copy requirement: <confirm>
- Electronic bookmarking of exhibits and specifics beyond the statewide rule: <confirm>
- Any other department-specific formatting variance: <confirm>

The skill holds the statewide rule 3.1113 page limits as the citable baseline and does not
state a specific court or department's local variances as fact. Departmental variances are
confirmed with the attorney once the venue is authored on the matter.

## Hearing - staged from the attorney-supplied reservation

**Reserved hearing (attorney-supplied):** <date, time, department> - recorded to the
matter calendar (`create_event`) with a reminder; a confirm-by task opened for
<responsible staff>. The skill did not choose or reserve this date.
**Filing deadline:** owned by the deadline lane (rule 3.1300), presented for attorney
confirm. Not computed here.

## Tentative-ruling check - scheduled (a human check, not a court read)

A reminder is scheduled for <the court day before the hearing> for a person to check the
department's tentative-ruling posting. The posting channel for this venue is the
attorney's to confirm. No tentative ruling is asserted; a reminder to check is not a
ruling.

## Gaps / needs a human

<any missing drafting component; any ambiguous component pairing under an unconfirmed
file-naming convention; an unconfirmed department format; a missing reserved hearing date;
an unknown tentative-ruling posting channel - listed, never guessed or filled.>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Motion package for <motion> on matter <matter number> assembled from <N> documents in the matter and filed as <file name>; <M> components missing. The department format is for <attorney> to confirm. Hearing: <date and time, from the reservation, or not yet recorded>.
<Attorney> to confirm the format, supply any missing component, and finalize <file name>.
```

## Shape B - Cannot assemble (missing / unpairable components)

```markdown
# ⚠ Motion Package - cannot assemble - matter <id> - YYYY-MM-DD

**Situation:** <which required components are missing, unreadable, or cannot be paired -
e.g. "the points and authorities and the supporting declaration are not in the matter";
"a request to draft the notice was refused (the skill does not draft)"; "the located
documents cannot be matched to expected components under the unconfirmed file-naming
convention">
**Decision:** surfaced for a person; not assembled from partial or invented data. No
component, format, hearing date, or tentative ruling was fabricated to fill the gap.
```

## Rules

1. **No component is authored.** The notice, the points and authorities, every
   declaration, and the reasons-to-compel in a separate statement are drafting work
   product the skill never writes. A missing drafting component is a gap to surface (Shape
   B), never a fill-in. This is the pack floor `motion-assembly-no-drafting`.
2. **Every present component is a document read** from the matter, listed with its source.
   No paraphrase, no reconstruction. A component that cannot be sourced is not shown as
   present; it is surfaced as missing.
3. **The statewide baseline is held; only departmental variances are confirmed.** The
   statewide baseline (rule 3.1112; rule 3.1110; rule 3.1113 page limits - 15/20/10 - and
   its TOC/TOA and summary-of-argument thresholds; rule 3.1345 or rule 3.1350 / CCP
   §437c(b)(1) for the separate statement) is stated as fact. Genuine department-specific
   variances (filing order, standing-order courtesy copies, bookmarking beyond the
   statewide rule) are an attorney-confirm prompt. Never bake an invented court or
   department format.
4. **The hearing date is attorney-supplied.** The skill records the reserved date, time,
   and department a human reserved; it never invents, chooses, or reserves one. A missing
   reservation is a gap, not a fabricated date.
5. **The tentative-ruling watch is a scheduled human check.** The skill never asserts a
   ruling it has not observed and never invents the posting channel.
6. **No legal filing deadline is computed or calendared** - the deadline lane owns the
   clock; the skill records only the hearing date.
7. **The drafting-tool routing is config**, read at connect; never hardcoded.
8. **Staged, never filed, served, or reserved.** The output is a draft for the attorney;
   any document write into the matter is a gated write surfaced for confirm, and reported
   only after a confirming read.
9. **Scope is the attorney's.** The skill packages the flagged motion's components, or the
   statewide baseline set with each item marked for the attorney to confirm; it never
   selects on its own.

# Shared: Assembler Output Format (PI-litigation pack)

Canonical output shape for every **assembler** skill (separate-statement-assembler,
motion-package-assembler, minors-compromise-packet, trial-binder-assembler,
settlement-statement-feeder). Each assembler's own `references/output-format.md`
derives from this with the skill-specific structure (e.g. the CRC 3.1345 columns, the
MC-350/MC-351 fields, the binder index). Fix the shared shape here first.

## The principle

An assembler **collates authored components into the mechanical structure a tool or
court requires**; it never authors the substance. Its output is a Word document filed
in the matter for an attorney to finalize, plus one file note that names it. Every
value in the artifact comes from a matter read (a figure, a request, a response, an
exhibit), never invented.

**The artifact is a Word document, never a note.** Shape A down to its File note is
the `draft_markdown` of `mcp_smokeball_render_docx_draft(matter_id, file_name,
draft_markdown, folder_id)`, where tables and form structure belong (omit
`document_class` unless the skill names one). Name it "<today> <Artifact> (draft) -
<Client>" (the tool adds `.docx`) and confirm it with `get_file` and a
`read_document` spot check. Never write the artifact into a `create_memo`, and never
file it as text with `add_file`. A `refusals` list back means nothing was filed: fix
what it names and call again. The File note is the separate `create_memo`: the
header line and two plain lines, naming the file (see `_shared-write-posture.md`).

## Shape A: Assembled artifact (a Word document staged for attorney finalization)

```markdown
# <Artifact> (draft)

Matter <matter number>, <matter descriptor>. Prepared <today, the firm's way> for <attorney> to finalize and file.

**Source components:** <list each component + where it was read from: request, response, exhibit, figure>

## <The mechanical structure the tool/court requires>

<the collated table / packet index / statement: every cell traceable to a source read>

## Gaps / needs a human

<anything missing, ambiguous, or requiring judgment: surfaced, not guessed>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
<Artifact> assembled on matter <matter number> from <N> documents and filed as <file name>. Gaps: <gaps, or none>.
<Attorney> to review and finalize <file name>.
```

## Shape B: Cannot assemble (missing components)

```markdown
# ⚠ <Artifact> - cannot assemble - matter <matter number> - <date>

**Situation:** <which required components are missing / unreadable>
**Decision:** surfaced for a person; not assembled from partial or invented data.
```

## Rules

1. **No substance is authored**: no legal argument, no valuation, no computed figure
   the incumbent owns (Smokeball computes settlement math; the drafting engines draft).
2. **Every value is traceable** to a matter read; a value that cannot be sourced is a
   gap to surface (Shape B), never a fill-in.
3. The artifact is always **staged for attorney finalization** as a Word document in
   the matter, never filed with a court or sent.
4. The mechanical structure (columns, form fields, index order) is the skill-specific
   part; author it precisely in the skill's own `references/output-format.md`.
5. **The document reads like a paralegal wrote it.** Its first line is its title.
   The next line says which matter it is for and who finalizes it; nothing says how
   the document was made. A date paragraph says "Trial is October 13, 2026, 9:00
   a.m., Dept. 47." and the deadlines paragraph names what is missing, in ordinary
   case: no capitals for emphasis. A court's own caption title keeps its capitals.
   Dates are written the firm's way (October 13, 2026). A record is named by its file
   name.

# Separate Statement Assembler - Output Format

Derives from `operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md`
with the CRC 3.1345 item structure as the mechanical shape. Every filled cell is a
verbatim quotation traceable to a document read. The reasons-to-compel cell is a
labeled blank for the attorney and is never filled.

## Shape A - Assembled separate statement (staged for attorney finalization)

```markdown
# Separate Statement (CRC 3.1345) - <discovery set, e.g. RFP Set One> - <matter descriptor> - matter <id> - YYYY-MM-DD

**Decision:** assembled from matter documents; staged for <attorney> to finalize and file.
**Motion:** motion to compel further responses (<interrogatories §2030.300 | production §2031.310 | admissions §2033.290>); one statement per set and method.
**Moving posture:** propounding party moving against the opposing party's responses as served.
**Source components:** served requests read from <file/doc + folder>; the opposing party's served responses read from <file/doc + folder>.
**Scope:** <items the attorney flagged | full served set, each marked keep/drop for the attorney>.
**Caption fields (attorney to supply, not composed):** court <TBD by attorney>; case no. <TBD by attorney>; dept <TBD by attorney>; title "SEPARATE STATEMENT IN SUPPORT OF MOTION TO COMPEL FURTHER RESPONSES TO <set>".

## Items

### Item <N>

**Request <N> (verbatim, from the served set):**

> <the request text, quoted exactly as served>

**Definitions / instructions needed to read Request <N> (verbatim, if any):**

> <the definition/instruction text the request depends on, quoted exactly; omit the block if none applies>

**Response to Request <N> (verbatim, from the opposing party's served responses - the responses served by the party the requests were propounded to; includes any further responses):**

> <the response / answer / objection text, quoted exactly as served>

**Factual and legal reasons for compelling a further response:**

> [ATTORNEY TO AUTHOR: CRC 3.1345(c). The assembler does not draft this.]

<repeat per item, aligned by request number>

## Attorney prompts (surfaced, not composed)

- Caption fields for the standalone document: court, case number, department, and the
  title "SEPARATE STATEMENT IN SUPPORT OF MOTION TO COMPEL FURTHER RESPONSES TO <set>"
  - surfaced for the attorney to supply; the skill never fabricates a court, case
    number, or department.
- Dependent requests/responses this item refers back to: <listed if the item's text
  points to another request/response; the attorney decides whether to inline them>.
- Relevant pleadings/documents: <flagged only if the matter clearly implicates them;
  the attorney decides relevance and inclusion>.

## Gaps / needs a human

<any request with no matching response; any response with no matching request; any
ambiguous request/response pairing; any unreadable document - listed, never guessed>

## Internal log (create_memo body)

> Separate statement (CRC 3.1345) assembled for <set> on matter <id> from <N> items
> (requests read from <doc>, responses from <doc>); reasons-to-compel left for
> <attorney>; staged to finalize/file. Gaps: <...>.
```

## Shape B - Cannot assemble (missing / unpairable components)

```markdown
# ⚠ Separate Statement - cannot assemble - matter <id> - YYYY-MM-DD

**Situation:** <which required components are missing, unreadable, or cannot be paired -
e.g. "RFP Set One served (12 demands) but no opposing-party served-responses document
located"; "responses document present but the request/response numbering does not
align"; or "located responses appear to be the firm's own, not the opposing party's
served responses (wrong party)">
**Decision:** surfaced for a person; not assembled from partial or invented data. No
response, request, or reason was fabricated to fill the gap.
```

## Rules

1. **No substance is authored.** The reasons-to-compel cell is always the labeled
   blank `[ATTORNEY TO AUTHOR: CRC 3.1345(c)]`. The skill never drafts it, never
   characterizes a response, never draws a legal conclusion. This is the pack floor
   `separate-statement-assembly-no-argument`.
2. **The response paired is the opposing party's, as served.** The response cell holds
   the responses served by the party the requests were propounded to, verbatim as
   served - never a draft the firm authored, never the firm's own responses. A
   wrong-party response document is surfaced (Shape B), not collated. A motion to compel
   further is never built against your own responses.
3. **Every filled cell is a verbatim quotation** of a document read (request,
   response, definition). No paraphrase, no cleanup, no reconstruction. A value that
   cannot be sourced is a gap (Shape B), never a fill-in.
4. **Alignment is read, not inferred.** Item N pairs request N with the response served
   to N as written. A missing, extra, or misaligned item is surfaced, not guessed.
5. **The statement is standalone.** Per CRC 3.1345 nothing is incorporated by
   reference; the read text is inlined, not pointed at. Caption fields (court, case
   number, department, title) are surfaced as an attorney prompt, never fabricated.
6. **One statement per set and method.** Each written-discovery set and method
   (interrogatories, RFPs, RFAs) gets its own compel-further statement and run; the
   skill does not combine sets or methods into one table.
7. **Staged, never filed or served.** The output is a draft for the attorney to
   finalize and file. Placing it as a matter document is a gated write surfaced for
   confirm, not autonomous.
8. **Scope is the attorney's.** The skill assembles the flagged set, or the full
   served set with each item marked keep/drop; it never selects disputed items on its
   own.

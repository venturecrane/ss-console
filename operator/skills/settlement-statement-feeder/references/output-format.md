# Settlement Statement Feeder - Output Format

Derived from
`operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md`.
The assembler collates authored components into the mechanical structure a settlement
statement requires; it never authors substance. Every value comes from a matter read.
Smokeball runs the trust accounting and the authoritative math; the artifact is staged
for a person to verify and execute in Smokeball.

The situation determines the shape.

## Shape A - Statement assembled (all inputs read; staged for a person to execute)

The block below, down to its File note, is the `draft_markdown` of
`mcp_smokeball_render_docx_draft(matter_id, file_name, draft_markdown, folder_id)`: a Word
document filed in the matter (omit `document_class`; tables belong here, never in a
note), named "<today> Settlement Statement (draft) - <Client>" (the tool adds `.docx`) and confirmed
with `get_file` and a `read_document` spot check. It is never written into a note and
never filed as text with `add_file`. A `refusals` list back means nothing was filed: fix
what it names and call again. The File note is the separate `create_memo`, and it
names the file.

```markdown
# Settlement Statement - <plaintiff> - matter <id> - YYYY-MM-DD

**Decision:** assembled from the matter's recorded figures; staged for <responsible attorney> to verify and execute in Smokeball.
**Not done by this skill:** no money moved, no disbursement authorized, no figure computed by the Operator. Smokeball runs the trust accounting and the math.
**Source components:** each figure below is traceable to the read named in its row.

## Breakdown (every figure sourced to a matter read)

| Line                             | Amount        | Read from                                                                                      |
| -------------------------------- | ------------- | ---------------------------------------------------------------------------------------------- |
| Gross recovery                   | $<amount>     | <settlement task / matter memo>                                                                |
| Less: attorney fee (<basis>)     | ($<amount>)   | authored fee: <settlement task / matter memo / fee agreement> (contingency; NOT `get_fees` AR) |
| Less: case costs                 | ($<amount>)   | `get_expenses` (Smokeball AR)                                                                  |
| Less: lien - <holder> (<status>) | ($<amount>)   | lien task, `list_tasks`                                                                        |
| Less: lien - <holder> (<status>) | ($<amount>)   | lien task, `list_tasks`                                                                        |
| **Net to <plaintiff>**           | **$<amount>** | arithmetic laydown of the rows above                                                           |

**Net note:** the net is the plain sum of the read figures above, shown for a person
to verify and execute in Smokeball. Smokeball's trust accounting is authoritative.

**Scope caveat (what this net does NOT capture):** this net reflects only the gross,
fee, costs, and liens recorded on the matter. A real CA PI settlement can carry
deductions this four-bucket laydown does not include - a co-counsel or referral fee
split (Cal. Rule of Prof. Conduct 1.5.1), a prior client advance or case-lien held by
the firm, and non-lien outstanding provider balances. Any of these must be confirmed
against Smokeball's trust accounting before disbursement. The absence of such a line
here is not evidence there is none; it means none was recorded on the matter for this
skill to read. A person verifies these before executing.

## Trust context (read-only)

> Trust for the matter (`get_matter_balances`, read-only): `balance` $<amount> ·
> `protectedBalance` $<amount> · `availableBalance` $<amount> (= balance − protected).
> The "funds in trust" check compares the gross against `balance`, not
> `availableBalance` (protected funds are still in trust).
> <"Funds are in trust (`balance` ≥ gross)." | "⚠ `balance` is below the gross; the settlement funds may not be in trust yet.">
> <if protectedBalance > 0: "Note: $<amount> of the matter's trust is currently protected.">

## Deductions this net does not capture (confirm before disbursement)

> This four-line net (gross − fee − costs − liens) does not include any co-counsel /
> referral split (Rule 1.5.1), prior client advance / firm case-lien, or non-lien
> provider balance. None was recorded on the matter for this skill to read; a person
> confirms against Smokeball's trust accounting before executing.

## Gaps / needs a human

<anything missing, unconfirmed, or requiring judgment - surfaced, never guessed. If empty, say "none.">

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Settlement statement on matter <matter number> assembled from the recorded figures and filed as <file name>; the net is the recorded figures laid out, and Smokeball runs the math. No funds moved. Gaps: <gaps, or none>.
<Attorney> to verify the figures and finalize <file name>.
```

## Shape B - Cannot finalize the net (a core figure is missing or unconfirmed)

```markdown
# ⚠ Settlement Statement - cannot finalize the net - matter <id> - YYYY-MM-DD

**Situation:** <which figure is missing or unconfirmed - e.g. no recorded gross; the Medi-Cal lien payoff is not confirmed>
**Decision:** surfaced for a person; the net is not produced from partial or invented data. No money moved.

## Partial breakdown (what was readable)

| Line                             | Amount                                                            | Read from                                                                                                      |
| -------------------------------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| Gross recovery                   | $<amount or "not recorded - GAP">                                 | <read>                                                                                                         |
| Less: attorney fee (<basis>)     | ($<amount or "authored fee not recorded / basis unstated - GAP">) | authored fee (settlement task / memo / fee agreement); a `get_fees` AR return is a gap-to-confirm, not the fee |
| Less: case costs                 | ($<amount or "GAP">)                                              | `get_expenses`                                                                                                 |
| Less: lien - <holder> (<status>) | ($<amount or "payoff not confirmed - GAP">)                       | lien task                                                                                                      |
| **Net to <plaintiff>**           | **not computed - one or more inputs is a gap**                    | -                                                                                                              |

## Gaps to resolve (never filled in by the skill)

<each gap, what read it should come from, and who resolves it>
```

## Shape C - Refuse a fund-movement request (surface and hold)

```markdown
# ⚠ Settlement - fund movement refused - matter <id> - YYYY-MM-DD

**Request received:** <"disburse the net" / "pay the liens" / "cut the check" / "move the funds" - quote the source>
**Decision:** refused. This skill never moves trust money and never authorizes a disbursement. Smokeball plus a person execute the disbursement.
**What it can do instead:** assemble and surface the breakdown (Shape A) or the gaps (Shape B) for a person to execute in Smokeball.
```

## Rules

1. **No figure is authored** - no fee percentage computed, no lien estimated, no gross
   inferred from a trust balance. Every value is a read; a value that cannot be sourced
   is a gap (Shape B), never a fill-in.
2. **The net is an arithmetic laydown of read figures**, labeled for a person to verify
   and execute in Smokeball. The Operator never presents it as authoritative and never
   authorizes a disbursement.
3. **A fund-movement request is Shape C** - refused and surfaced, never performed. The
   trust-account write tools are hard-banned.
4. **The artifact is always staged for a person to execute in Smokeball**, never
   executed, sent, or relied on as final.
5. The decision and its reason are always stated, so the assembly is auditable.

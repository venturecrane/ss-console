# Minor's Compromise Packet - Output Format

Derives from `operator/verticals/law-firm/addons/pi/references/_shared-assembler-output-format.md`.
An assembler collates authored figures into the structure the Judicial Council forms
require; it authors no substance and computes no figure. Every value in a form field
comes from a matter read. A figure that cannot be sourced is a gap, never a fill-in.

The decision determines the shape.

## Shape A - Packet assembled (staged for attorney finalization)

```markdown
# Minor's Compromise Packet - <minor> - matter <id> - YYYY-MM-DD

**Decision:** assembled from authored matter figures; staged for <attorney> to finalize and file.
**Petitioner (from roles):** <GAL name> as Guardian ad Litem for <minor>
**Source figures:** <each figure + the file/read it came from - gross, fee, costs, medical/liens, net>

## MC-350 - Petition for Approval of Compromise (fields filled from authored figures)

| Field (by meaning)                                             | Value                                      | Source                          |
| -------------------------------------------------------------- | ------------------------------------------ | ------------------------------- |
| Minor / claimant                                               | <name, DOB if authored>                    | get_roles_on_matter             |
| Petitioner (GAL)                                               | <name>                                     | get_roles_on_matter             |
| Gross settlement / proceeds                                    | <authored figure>                          | <file read>                     |
| Attorney fee (per CRC 7.955 / §3601; disclosure per CRC 7.951) | <authored figure>                          | <file read>                     |
| Costs / expenses                                               | <authored figure>                          | <file read>                     |
| Medical / lien payoffs                                         | <authored figures>                         | <file read> / lien chase        |
| Net to the minor                                               | <authored net, placed as read>             | <file read> - NOT computed here |
| Fund handling (if authored)                                    | <blocked account / trust / annuity / UTMA> | <attorney-authored>             |

> Fields whose figure is not authored are labeled `[GAP - figure not authored on the matter]`.
> Item/attachment numbers not certain against the current revision are labeled
> `[confirm item number against current MC-350]`.

## MC-351 - Order Approving Compromise (prepared where the firm prepares the order with the petition)

> <the order fields the court signs on approval, mirroring the petition's authored figures>

## Fund handling after approval (surfaced, not chosen)

> A post-approval disposition must be decided by the court. If a blocked account is
> the authored disposition: MC-355 (Order to Deposit) + MC-356 (Receipt/Acknowledgment)
> prepared for finalization (CRC 7.953; Prob. Code §3611(b), §3413(a)).

## Dates tracked

> GAL appointment: <present / NOT appointed - gating>. Hearing: <date, surfaced for
> attorney confirm> (CRC 7.952 attendance).

## Gaps / needs a human

> <missing figures, missing net, no GAL, outstanding lien payoff, undecided fund handling>

## Internal log (create_memo body)

> Minor's compromise packet assembled for matter <id> from <N> authored figures;
> staged for <attorney>. Net placed as authored, not computed. Gaps: <...>.
```

## Shape B - Track / chase (open item, cadence due)

```markdown
# Minor's Compromise Track - <minor> - matter <id> - YYYY-MM-DD

**Status:** <GAL appointment pending | hearing <date> | lien payoff outstanding>
**Decision:** <the single next step - surface the gating item / draft the lien chase>

## Lien payoff chase (DRAFT - reviewer/firm sends)

> <short, professional request to <lienholder> for a current payoff figure for <minor>;
> per \_shared-chase-voice; states no legal consequence, estimates no figure>

## Internal log (create_memo body)

> Track pass on matter <id>: <what is outstanding and what was drafted/surfaced>.
```

## Shape C - Cannot assemble (missing required figures or no GAL)

```markdown
# ⚠ Minor's Compromise Packet - cannot assemble - matter <id> - YYYY-MM-DD

**Situation:** <no GAL appointed | authored net missing | required figures unauthored>
**Decision:** surfaced for a person; not assembled from partial or invented data, and
no figure computed to fill a gap.
```

## Rules

1. **No figure is computed here.** The net to the minor is placed as the authored net;
   if it is absent it is a `[GAP]`, never a subtraction the skill performs.
2. **The fee is placed, never judged.** No cell characterizes the fee as reasonable,
   fair, standard, or within a percentage (CRC 7.955 / §3601 is the court's call).
3. **Every filled field is traceable** to a matter read; an unsourced field is a gap
   (Shape C), never a fill-in.
4. **Only Shape B contains an outbound draft** (the lien chase), drafted and surfaced for review; sending follows the firm's authored ceiling.
5. **A form number or code section not on the verified list in SKILL.md is flagged**
   ("confirm the form/section"), never asserted.
6. The packet is always **staged for attorney finalization**, never filed or served.

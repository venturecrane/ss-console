# Lien Ledger Tracker - Output Format

The decision determines the shape. Every ledger entry is keyed to
`(matter, lienholder, lien-type)`. No figure the skill computed itself ever appears
in any shape; only holder-stated, attorney-provided, or document-observed amounts.

## Shape A - Log / update a ledger entry (internal write, confirm by read)

```markdown
# Lien Ledger - <lienholder> (<lien-type>) - matter <matter number> - YYYY-MM-DD

**Holder:** <name> - <health plan | Medi-Cal (DHCS) | Medicare (BCRC) | ERISA plan | hospital/provider>
**Asserted amount:** <amount as stated by <source>> (never a computed figure)
**Status:** <open | payoff requested | payoff figure received | reduction requested | reduction agreed | resolved (pending disbursement)>
**Source of figure:** <adjuster email | DHCS lien letter in matter | responsible attorney>
**Decision:** logged as a tracked task (confirm-by <near-term admin date>); confirmed by read.

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
<Lien type> lien for <holder> recorded at <amount>, per <source>; status <status>. The amount is recorded as stated, not computed; no funds moved.
Nothing to do.
```

## Shape B - Chase (open payoff or reduction, cadence due)

```markdown
# Lien Chase - <lienholder> (<lien-type>) - matter <matter number> - YYYY-MM-DD

**Status:** <payoff requested <date>> / <reduction requested <date>>, outstanding (<N> days); nudge <#> of <max>
**Decision:** cadence due - follow-up drafted to the holder. Surfaced for the firm to send.

## Follow-up (DRAFT - reviewer/firm sends; the skill does not send)

> <short, professional request for the outstanding payoff figure or reduction
> response per voice.md - no number stated, no reduction proposed, no consequence invented>

## File note (create_memo)

[Operator] <Routine name> as of <localDate>
Follow-up <#> to <holder> on the <lien type> lien drafted; the payoff or reduction is still outstanding.
Review and send the follow-up to <holder>.
```

## Shape C - Ledger snapshot (read only, no write)

```markdown
# Lien Ledger - matter <matter number> - YYYY-MM-DD

| Holder | Type | Asserted amount (as stated) | Status | Source |
| ------ | ---- | --------------------------- | ------ | ------ |
| ...    | ...  | ...                         | ...    | ...    |

**Note:** amounts are as stated by each source; none is computed by the Operator.
Final payoffs and any reductions are the attorney's determination; disbursement is
performed in Smokeball by a person.
```

## Shape D - Surface to the attorney (refuse computation / refuse fund movement / ambiguous / say-so)

```markdown
# ⚠ Lien - needs the attorney - <lienholder> - matter <matter number> - YYYY-MM-DD

**Situation:** <asked to compute a reduction (e.g. the §14124.78 cap) | asked to disburse or move money | amount disputed or two sources conflict | holder/client reports resolution with no supporting document | payoff stalling near settlement>
**Decision:** surfaced for the attorney. **The computation was not performed and no money was moved.**
The factual part (the asserted amount / the reported status) is logged; the item stays open.
This is a judgment the skill does not make on its own.
```

## A matter with nothing to report, and the facts line

When this routine writes a note on a matter where it found nothing (no lien),
the note is the header and exactly two lines. No rule sentence, no request to
confirm when there is nothing to confirm, and no word about an earlier run
(`_shared-write-posture.md`, section 5):

```
[Operator] <Routine name> as of <localDate>
No liens on file.
Nothing to do.
```

When the wake line gives the matter a `facts_digest`, the note's last line is
`facts <digest>`, copied exactly from the wake line and alone on its line. With no
`facts_digest` on the wake line, write no facts line.

## Rules

1. **Only Shape B contains an outbound draft** (a blockquote, drafted for review; the firm sends per its authored ceiling). Shapes A and C are internal; Shape D is a surface.
2. **The skill never computes a reduction or payoff** - Medi-Cal §14124.78 /
   §14124.72(d), hospital/provider reductions, "net after fees": all Shape D, routed
   to the attorney, never a number the skill produces.
3. **The skill never moves money** - `create_transaction` / `protect_funds` /
   `unprotect_funds` are never called; a "and disburse it" request is Shape D for the
   disbursement half while the logging/chase half proceeds.
4. **A status only advances on an observed fact or a provided figure.** A "say-so"
   resolution with no supporting document is Shape D (surface), never a Shape A that
   marks the lien resolved.
5. Every amount carries its **source**; a figure with no source is not logged as fact.
6. The decision and its reason are always stated, so the ledger is auditable.

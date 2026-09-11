# Medical Records Chaser — Output Format

The decision determines the shape. Every tracked item is keyed to
`(matter, provider, request)`.

## Shape A — Chase (outstanding provider records, cadence due)

```markdown
# Records Chase — <provider> — <patient / plaintiff> — matter <id> — YYYY-MM-DD

**Requested:** <date records were requested, from the authored roster>
**Status:** outstanding; <N> days past the confirm-by date on the tracking task
(the plan's `days_past_confirm_by`, copied verbatim); chase <#> (the plan's
`attempt`) — or, when the plan's `last_chased` is null, "first chase recorded
in the tracking ledger" with no numerator (ss #2404)
**Last chase staged:** <the plan's `last_chased` date, or the null-history
sentence — never a recalled date>
**Decision:** cadence due — chase <sent | drafted, per the authored external_send
ceiling> to <provider | records vendor>.

## Chase (sent as-is under an authored autonomous ceiling; otherwise a DRAFT the

reviewer/firm sends by its method)

> <short, businesslike follow-up to the provider/vendor per voice.md that names the
> patient, the request/date, and that the records are still outstanding, asks for
> status or an expected date, offers to resend the authorization; characterizes no treatment>

## Internal log (create_memo body)

> Records chase <#> for <patient> / <provider>; still outstanding. Requested <date>.
> Confirm-by task set for <responsible staff>, due <near-term admin date>.
```

## Shape B — Received, logged & closed (ONLY on a confident match)

```markdown
# Records Received — <provider> — <patient> — matter <id> — YYYY-MM-DD

**Decision:** a matching record for <provider> observed in the matter and matched
with confidence to the request; item closed; cadence stopped.

## Internal log (create_memo body)

> <provider> records for <patient> observed in the matter <date>; request closed.
> (No read of clinical content; matched on document metadata only.)
```

## Shape C — Surface to a human (say-so / ambiguous / unconfirmed / no roster)

```markdown
# ⚠ Records — needs a human — <patient> — matter <id> — YYYY-MM-DD

**Situation:** <provider/vendor says records were sent but no matching document is in
the matter | landed record cannot be matched to a request with confidence | firm
file-naming convention not yet confirmed | no authored records-request roster on the
matter | records still outstanding as demand prep / SOL nears | provider refusing to
produce>
**Decision:** surfaced for a person. Not marked received / not closed. This is a
judgment the skill does not make on its own.
```

## Rules

1. **Only Shape A contains an outbound chase** (blockquote). Whether it is sent or
   drafted follows the firm's authored `external_send` ceiling (see SKILL.md "The
   send seam"); the recipient is always the authored roster/vendor contact, and the
   decision line states which happened.
2. **The skill never decides which providers to request** — it acts on the authored
   roster, and never infers a provider from a record's content.
3. **Shape B is reachable ONLY on a confident document match** — the firm's
   file-naming convention is unknown until confirmed on real matters; until then, an
   observed record is Shape C (surface), never Shape B (auto-close).
4. **A say-so is never receipt.** "We already sent everything" with no matching
   document is Shape C, never Shape B.
5. **No treatment content, diagnosis, chronology, or demand language appears
   anywhere** — the only read of a record is the metadata match.
6. Every write is confirmed by a read before it is reported done (see
   `_shared-write-posture.md`); an unconfirmed write is surfaced, not asserted.
7. The decision and its reason are always stated, so the cadence is auditable.

# Client Verification Tracker — Output Format

The decision determines the shape. Every verification is keyed to
`(plaintiff, response-set, version)`.

**SHAPES B AND D ARE RENDERED IN CODE (WS-RENDER).** The chase reminder
(Shape B) and the surface-to-a-human alert (Shape D, including the ceiling
hand-off, the config-missing note, and the hold surfaces) render
deterministically in `pre_run` (`render.py`, whose closed situation map holds
the authored phrases) and dispatch out of turn; their ledger writes are
appended by the seat post-send. The model composes neither. Shape B is
fail-closed while `settings.return_link` is unauthored: every due chase
degrades to a throttled Shape-D surface and no `chased` event is written.
Shapes A and C remain the turn's own work, under the templates below.

## Shape A — Prepare & route for authenticated approval

```markdown
# Verification — <plaintiff> — <response-set> (v<version>) — matter <id> — YYYY-MM-DD

**Signer:** <party | GAL for minor <name> | successor-in-interest <name>>
**Response type:** <interrogatories | RFP | RFA> — attorney-flagged for verification
**Decision:** prepared; routed to <responsible attorney> for approve-and-send.

## Verification request (DRAFT — sent only on authenticated attorney approval, by the firm's method)

> <plain-language request to the signer per voice.md — asks them to sign the
> attached verification; interprets nothing; states no legal consequence>

## Approve-and-send to <attorney> (internal)

> The verification for <plaintiff> / <response-set> is ready. Approve to send it to
> <signer> by <firm method>. <one-time approval token / bound to this verification>

## Internal log (create_memo body)

> Verification for <plaintiff>/<response-set> v<version> prepared, routed to <attorney>
> for approval; not yet sent. Response deadline <date, from the deadline lane>.
```

## Shape B — Chase (open, unsigned, authored cadence due, attempt ceiling not yet reached)

The chase count is filled from **real ledger state**, never left as a
placeholder. The numerator is the attempt this chase carries — the pre_run's
`next_attempt(state)` (the count of prior `chased` raises on this item, plus
one). The denominator is the authored `escalate_after_attempts`. So the third
unanswered chase on a ceiling of 3 reads `nudge 3 of 3`.

```markdown
# Verification Chase — <plaintiff> — <response-set> — matter <id> — YYYY-MM-DD

**Status:** still open, not yet received (<N> days since last touch); nudge <this attempt> of <escalate_after_attempts>
**Cadence:** authored `chase_cadence_days` = <days>; last chase <date from ledger>, so this one is due
**Decision:** cadence due, attempt ceiling not reached — chase the signer via `send_message` (never `reply_to_message`).

## Reminder (proactive send to the signer per voice.md / verification-request.md — subject to the authored exposure)

> <short, warm reminder to the signer — points to where to complete and return it;
> floor-clean per #1878: "complete and return" not "sign", "due date" not
> "deadline", "the team" not "attorney">

## Internal log (create_memo body)

> Verification chase nudge <this attempt> of <escalate_after_attempts> for <plaintiff>/<response-set>; still open. Sent via send_message.

## Ledger write (after the send succeeds — see SKILL.md "The escalation ledger")

> Append one `chased` event (attempt = the numerator above) through the broker's
> `escalation_event_append` verb. Only after both the send AND the ledger write
> succeed is the item counted as chased.
```

State-read note: this turn reads matter metadata only (`list_tasks`, `get_files_on_matter`, `get_memos_on_matter`) — no message body — so the proactive chase send is not fenced by the taint gate.

## Shape C — Signed, logged & closed (ONLY on a confident match)

```markdown
# Verification Signed — <plaintiff> — <response-set> — matter <id> — YYYY-MM-DD

**Decision:** signed verification observed in the matter and matched with confidence
to <response-set> v<version>; item closed; cadence stopped.

## Internal log (create_memo body)

> Verification for <plaintiff>/<response-set> v<version> signed <date>; item closed.

## Ledger write (on close)

> Append one `resolved` event for this item_key. `resolved` is terminal: the
> pre_run never re-opens a resolved item. Only a confident signed-document match
> reaches Shape C, so only a confident match writes `resolved`.
```

## Shape D — Surface to a human (ambiguous / unauthenticated / say-so / unconfirmed)

```markdown
# ⚠ Verification — needs a human — <plaintiff> — matter <id> — YYYY-MM-DD

**Situation:** <signer ambiguous | approval not authenticated | client says-signed but no
matching document | firm file-naming convention not yet confirmed | RFA near deadline unsigned |
chase attempts reached `escalate_after_attempts` (stop chasing the client) | chase cadence or
escalation attempt-count not authored>
**Decision:** surfaced for a person. Not sent / not closed. When the situation is the attempt
ceiling, the client chase is stopped and the open item is handed to the responsible attorney;
the skill does not send another nudge. This is a judgment the skill does not make on its own.
```

**Ledger writes for Shape D (so the surface fires once, not on every wake):**

- **Attempt ceiling reached** — after the hand-off alert to the attorney sends,
  append one `handed_off` event for the item_key. `handed_off` is terminal for
  autonomous wakes: the pre_run will not re-raise this item, so the hand-off email
  does not repeat daily.
- **Chase cadence / attempt-count not authored** — append one `fired` event on the
  config sentinel (`item_key(matter_id="", source_id="__chase_config__",
label="chase-config-missing", authored_date="")`) after the single surface. The
  pre_run reads that sentinel and stays quiet on later wakes, so the missing-config
  note surfaces once, not every morning.

## Dedup — the chase's internal escalation points, it does not duplicate

Before the ceiling hand-off (or any internal red-flag) restates an item that the
daily digest or the deadline lane is already escalating, render a one-line
pointer instead of a full re-listing, so the same verification does not produce
overlapping morning emails. "Under active escalation" is read from the shared
`escalation_ledger.py` state (a `fired`/`chased` from another skill on the same
item within its `escalation.refire_days` window), never from same-day prediction:

> <matter> (<matter id>) — verification for <plaintiff>/<response-set>: under active escalation by <owning skill> (last raised <date>).

Deadline-proximity on an unsigned verification is owned by
`deadline-miss-escalator` (it pulls verification response deadlines with the rest
of the firm's authored dates). The chase does not re-escalate a nearing deadline;
it points to the deadline lane. The chase's OWN trigger is the attempt ceiling.

## Rules

1. **Only Shapes A and B contain outbound drafts** (blockquotes, drafted for review; sending follows the firm's authored ceiling).
2. **The skill never decides whether a response needs verification** — it acts on the
   attorney's flag; objections-only responses are excluded (the attorney signs those).
3. **Shape C is reachable ONLY on a confident document match** — the firm's file-naming
   convention is unknown until confirmed on real matters; until then, an observed signed
   document is Shape D (surface), never Shape C (auto-close).
4. No verification term, response, or legal consequence appears interpreted anywhere.
5. The decision and its reason are always stated, so the cadence is auditable.
6. **The chase cadence is the authored `chase_cadence_days`; the attempt ceiling is the
   authored `escalate_after_attempts`.** Both are fail-closed when unauthored (no chase;
   surface the missing-setting note). A chase is sent with `send_message`, never
   `reply_to_message`, and the chase-send turn reads matter metadata only (no message
   body), so the send is not fenced by the taint gate.
7. **Attempt-count and deadline-proximity escalation are independent** — either sends the
   open item to the attorney; reaching `escalate_after_attempts` also stops the client
   chase (Shape D), it does not draft another Shape B nudge.

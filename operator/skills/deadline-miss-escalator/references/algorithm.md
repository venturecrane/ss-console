# deadline-miss-escalator algorithm

The escalation decision and ladder, with the never-computes line held in code,
plus the escalation ledger that makes an alert fire once (not daily) and lets a
person acknowledge one item without silencing the rest.

## The authored-only rule (identical to the tracker's)

Every date is read from the firm's authored records: a task `due_date` from
Smokeball, or an authored calendar entry via the mail/calendar binding. The only
arithmetic is comparing an authored date to today. There is no arithmetic that
_produces_ a deadline. "Overdue" means an authored date has passed today; it
never means a date this skill computed.

## The in-range test (pre-run)

```
for each authored_date d on matter m:
    in_range = m.open and not suppressed(d) and d.authored_date <= today + escalation_window
```

`escalation_window`, `near`, and `notify` windows are firm-authored (pack
defaults 14 / 7 / 3 days, overridable via `escalation.*` in `customer.yaml`).
`suppressed(d)` is the ledger join below: an item that already fired inside its
re-fire window, or was acked and is still snoozed, or was handed off, is not
re-surfaced. The pre-run wakes iff any deadline is in-range AND not suppressed;
otherwise it writes a `SUPPRESSED_WAKE` heartbeat and suppresses.

## The escalation ledger (fire-once, per-item ack)

`pre_run.py` loads the vendored `escalation_ledger.py` (byte-identical to
`operator/workspace_broker/escalation_ledger.py`) and joins each pulled item
against the ledger:

- **item identity** — `item_key = sha256(matter_id, Smokeball task/event id,
authored_date)`. The task id is the anti-collision half: two same-day tasks on
  one matter differ only by it. `label` is accepted and IGNORED (ss #2151 — it is
  model-composed, and hashing it made one deadline two items). Every component is
  normalized before hashing (ss #2289): ids stripped and case-folded, the date
  canonicalized to `YYYY-MM-DD`. `2026-08-11` and `2026-08-11T00:00:00Z` are the
  same item; a date the module cannot parse is REJECTED, not hashed verbatim.
- **token** — `token_for(item_key)` is a short human-typable `ACK-XXXXXX` a
  reader types back off the email. It is deterministic, so any reader recomputes
  it; no lookup table. An item only gets one if its identity tuple is built
  entirely from values READ off the record (`has_stable_identity`): no stable
  task id, or a sentinel like `unknown-matter` in the tuple, means the key moves
  the moment the real value arrives, so the item renders in the
  blanket-ack-only group instead of printing a code that will name nothing.
- **state** — `derive_state(events)` folds the ledger into per-item
  `last_raised`, `attempts`, `acked`, `handed_off`, `resolved`.

### The fire policy (`should_fire`)

```
never raised            -> fire (attempt 1)
resolved / handed_off   -> never (terminal until a later raise re-opens it)
acked, still open       -> re-surface only after ack_snooze_days (ack = snooze)
raised, not acked       -> re-fire only after refire_days (fire once, not daily)
```

`refire_days` (pack default 3) and `ack_snooze_days` (pack default 7) come from
`escalation.*` in the trusted volume `customer.yaml` (the applier live-updates
it, so a value change reaches pre_run without a rebuild). Missing config, or no
PyYAML, falls back to the pack defaults: a repetitive deadline watcher is worse
than a silent one, so `refire_days` ships a default rather than fail-closing to
quiet.

"Resolved" is normally implicit: a completed task drops out of the Smokeball
pull, so it never fires again. An acked item is a **snooze, not a tombstone** —
a one-keystroke ack on a deemed-admission deadline must not permanently mute the
exact failure the skill exists to prevent.

## Rung by proximity (internal ordering)

```
days_out = (authored_date - today).days        # comparison arithmetic, not a produced date

if matter.conflict_hold:        rung = clearance     # human conflict clearance, never client-facing
elif days_out <= notify:        rung = notify        # top of the alert; ESCALATION_FIRED to red_flag_recipients
elif days_out <= near:          rung = near          # firm-internal, responsible humans
else:                           rung = watch          # elevated flag on the internal surface
```

The rung is an **internal ordering signal**, not a reader-facing label. In the
delivered alert it maps to triage: notify-rung and any authored-high-signal item
lead the "Needs you today" block; near/watch routine items collapse into
"Admin confirms" (see `output-format.md`).

## Writing the ledger (through the validated broker seam)

The ledger file is broker-owned; the agent reads it but never writes it
directly. Every write goes through the **`escalation_append` tool** (the
`hermes-smd-escalation` plugin, ss #1915), which carries one event to the
broker's uid-gated `escalation_event_append` verb — the broker keeps all
validation and stamps `ts`/`id` server-side. To read state, use the
**`escalation_state` tool** (per-item attempts, last raise, acked/handed_off/
resolved, ACK token; optionally filtered by `skill`). Do NOT reach the broker
socket via `execute_code` — the `code_execution` action class is unauthored on
customer seats and the trust layer refuses it (that dead path is how ss #1915
was found).

The tool derives `item_key` and the ACK token ITSELF from the identity
components — never pass a hand-built key (the first live probe proved a
model-authored key forks the pre_run join). The components MUST be the exact
tuple this skill's pre_run computes: the matter id, the STABLE Smokeball
task/event id (`source_id`; null only for idless items, which get no token),
the fixed label, and the authored date per the skill's identity convention.

**Every non-`acked` write is TWO calls, and the identity components are typed
on the FIRST one only** (ss #2304). The derive returns an `append_handle`; the
write presents that handle and names no identity at all. Until this changed, the
derive and the write were two independent tuples, so the ACK code quoted to a
person came off call 1 while the ledger row was keyed off call 2 — a single
transposition (`task-42` -> `task-43`, one row off in a batch of nine) wrote an
item the quoted code does not name, and both calls were individually
well-formed, so nothing could see it. The handle makes that divergence
unrepresentable rather than merely unlikely: there is no second derivation.

```
# STEP 1 — identity: derive the real ACK code, write NOTHING
escalation_append(skill=..., matter_id=..., source_id=..., label=...,
                  authored_date=... or null, event="fired", attempt=N,
                  derive_only=true)
  -> {"ok": true, "written": false, "item_key": <derived>, "token": <derived>,
      "append_handle": "EDH-..."}
# STEP 2 — the write (fired / chased / handed_off / resolved): the handle ONLY.
# Passing matter_id / source_id / label / authored_date here is REFUSED.
escalation_append(skill=..., event=..., attempt=N, append_handle="EDH-...")
  -> {"ok": true, "id": "...", "item_key": <derived>, "token": <derived>}
# acked: identity by the quoted ACK code (resolved against prior raises;
# an alarm that never rang cannot be acked). No components, no handle.
escalation_append(skill=..., event="acked", attempt=N, ack_token="ACK-XXXXXX")
escalation_state(skill=...)
  -> {"event_count": N, "item_count": N,
      "items": {item_key: {..., "token": <ACK code or null>, "ackable": <bool>}}}
```

The handle is **single-use and short-lived**: one derive writes one row, `skill`
and `event` must match the derive that minted it, and a spent, unknown or
expired handle is refused rather than written. A refusal writes nothing, which
is the safe direction — derive again and the item re-fires next run. Never carry
a handle across runs, and never reuse one to write a second row (that would
inflate the attempt count the ceiling reads).

`escalation_state` reports the token the ledger actually RECORDED, or `null`
with `"ackable": false` (ss #2289). It used to synthesize `token_for(item_key)`
whenever a row carried none, which is exactly the blanket-ack-only items — the
ones the ack path refuses by design — so the code handed to the turn could not
be acked by anyone. Route an `ackable: false` item to the blanket group. Never
recompute a code for it.

- **fired** — three steps, in this order (ss #1935):
  1. For each firing item, call `escalation_append` with `derive_only=true` to
     get its real `item_key` + ACK token + `append_handle`. Nothing is written.
     Keep each item's handle next to the code you are about to print for it.
  2. Compose and send ONE alert **with `smd_send_message`**, quoting exactly
     those returned tokens. That tool is the delivery. A memo, a task or a
     draft is a log of the work, never a substitute for it, and
     `smd_deliver_draft` only AUTHORIZES a draft — it sends nothing. The broker
     now enforces this: it refuses step 3's `fired` unless it witnessed a
     dispatch to a person in this session, so a turn that logged instead of
     sending records nothing and the item re-fires next run. NEVER
     print a code the tool did not return this run — an invented code
     (`ACK-A1`, `ACK-PENDING`) resolves to nothing, and a code remembered from
     a prior alert belongs to a DIFFERENT item and would silently ack the
     wrong thing. No follow-up "codes confirmed" email; the first email is the
     only email.
  3. After the send succeeds, emit one `fired` event per item, presenting that
     item's `append_handle` and **no identity components** — the handle is the
     only thing that can name the row, so the code you just printed is
     necessarily the code of the row you write. The broker stamps `ts`/`id`. If
     the send did not happen, write nothing (the item re-fires next run:
     annoying, never dangerous). Never report an item as raised unless the send
     AND the ledger write both succeeded. This is no longer only a rule you
     follow: the broker refuses the write when it did not witness the send, so
     an unwitnessed raise is a refusal you must read and act on, not retry.
- **acked** — on a rostered internal reply (routed here by the inbox skill), emit
  one `acked` event per quoted token. The broker REJECTS an `acked` whose token
  has no prior `fired`, so a stray or forged code cannot silence an alarm that
  never rang.

## The per-item ack procedure (reply turn)

The inbound reply is routed to this procedure by the inbox skill (see
`matter-inbox-router`), never by the escalator itself. On a rostered internal
reply:

1. Extract every `ACK-XXXXXX` code present in the reply body (they survive quote
   trimming). A blanket `ESCALATION_ACKNOWLEDGED` with no codes acks exactly the
   items **quoted** in the message being replied to; items not quoted stay open.
2. For each code, emit an `acked` event via the broker seam above, passing the
   code as `ack_token` — the tool resolves it to its `item_key` against the
   ledger's prior raises. Do NOT recompute `token_for` over open items to find a
   match: that manufactures a code for items that were never issued one, and the
   append is refused anyway (an alarm that never rang cannot be acked).
3. Reply with the confirmation that **enumerates** the acked items and **counts**
   what remains un-acked, so an under-ack stays visible (`output-format.md`).

An ack is a snooze: the item goes quiet for `ack_snooze_days`, then re-surfaces
if still open. Only resolution in Smokeball is terminal.

## Dedup (do not double-count)

Before listing an item, check the ledger for an active raise by another skill:
a `fired`/`chased` event from a different `skill` on the same `item_key`, inside
that item's re-fire window. If present, render the item as a one-line pointer
under "Under active escalation elsewhere" (naming the owning skill and the last
raised date), not a full entry.

## Fail-closed notify (ADR 0035)

The notify path fires through the firm's authored red-flag channel. With no
authored `red_flag_recipients`, the alert has nowhere to go and does not fire;
the escalator still records the fire in the ledger for the account, and never
invents a recipient.

## Heartbeat / dead-man's-switch

The `SUPPRESSED_WAKE` row on every quiet tick is the heartbeat (ADR 0021). A
scheduled tick that produces no audit row is the alarm the watcher-health view
fires on. An audit-write failure forces `wakeAgent: true` so a broken heartbeat
surfaces as the agent waking, never as silence. The deadline-watch is advisory
and supplemental, never the firm's system of record
(`operator/verticals/law-firm/compliance-floor.md`).

## Why the line is absolute

A cron-driven watcher a firm trusts is more dangerous than no watcher if it can
fail silently or launder a legal judgment. So: it never computes a date (the
judgment stays human), it never goes dark without a signal (the heartbeat +
fail-to-wake), it never reaches a client (every rung is internal), and the state
that silences an alarm is not writable by an injectable surface without
validation (the broker seam). It is a loud, honest, de-duplicated alarm for
authored dates, that fires once and remembers being acked.

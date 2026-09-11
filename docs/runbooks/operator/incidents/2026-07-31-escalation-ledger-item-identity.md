# Post-incident note: the seven-day snooze promise fails because the item key hashes model-composed text

**Backfilled 2026-08-17 under #2391.** No note was written at the time. Every fact below is attributed to a source named in the Sources block; nothing is reconstructed.

| Field                   | Value                                                                                                                                                                                                            |
| ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-07-31 (the day the defect was established by audit). The behaviour predates that; the audit window runs to 2026-07-31 and the earliest ack events it cites are 2026-07-15.                                  |
| Seat / surface          | The deadline-miss escalator and the ACK escalation ledger on the pilot tenant                                                                                                                                    |
| Severity                | Not classified under ADR 0064 at the time. This note assigns none: the Operator was not down and did not exceed its entitlements. It is a **commitment** defect, which is why it is recorded.                    |
| Detected by             | The output-provenance audit, `docs/audits/operator-output-provenance-2026-07-31.md` §3.11, by tracing the chain end to end and probing the tenant                                                                |
| Detection lag           | `not recorded`. The audit does not state when the label-derived key was introduced.                                                                                                                              |
| Detection to resolution | Unresolved. [#2151](https://github.com/venturecrane/ss-console/issues/2151) is open with all four acceptance criteria unchecked as of 2026-08-17.                                                                |
| Client impact           | An acked item re-fires the next day. On the pilot tenant the audit records 86 fired events against 2 acks, so the promise was largely untested in practice rather than repeatedly violated in front of a client. |
| Status                  | Open, [#2151](https://github.com/venturecrane/ss-console/issues/2151), audit remediation item 15                                                                                                                 |

**Sources.** `docs/audits/operator-output-provenance-2026-07-31.md` §3.11 and the remediation list items 15 and 16; issue [#2151](https://github.com/venturecrane/ss-console/issues/2151) (opened 2026-08-02T16:05:53Z, open); the traced chain the audit names: `customer.yaml:611-614`, `matter-inbox-router/SKILL.md:57,90`, `deadline-miss-escalator/SKILL.md:68`, `plugins/hermes-smd-escalation/__init__.py:157-183`, `shared/escalation_ledger.py:59`.

## What broke

Three findings, and the audit is emphatic that they are different sizes.

**1. The identity defect (the one #2151 owns).** `item_key = sha256(matter_id, source_id, label, authored_date)` and **`label` is model-composed free text**. The audit's count: **86 fired events produced 83 distinct item_keys, and only 5 keys ever recur.** The same deadline therefore arrives as a new identity every day. Acking `K` snoozes `K`; tomorrow it is `K'`, unacked, and it fires. The seven-day snooze arithmetic is implemented correctly (`shared/escalation_ledger.py:298-301`) and the ledger is genuinely read back (`plugins/hermes-smd-escalation/__init__.py:157-167` refuses a token no prior raise carries). The promise fails anyway, because it is keyed on something that is not stable.

Compounded by duplicate tasks: two `source_id`s for one obligation mint two keys and two codes, so acking the one you were shown leaves the other live.

**2. The token store is correct, and must not be rebuilt.** The audit says this in bold and this note repeats it, because the obvious remediation is the wrong one. `token_for() = sha256("ack-token:" + item_key)` folded to six Crockford characters; **88 token rows, 88 recomputed matches, zero true collisions**. `ACK-76FNK7` has one identity and was written once; on 07-25 it led the alert and on 07-28 the same item was folded into a collapsed group. The code was right on both days; only the prose was re-composed. The fix is to stop the label being model-authored and to stop the duplicate tasks, not to touch the store.

**3. The ACK write-back does not exist at all**, which is a larger finding than the identity defect and closed within the audit. Traced end to end through the chain named in Sources: **no `create_memo`, no `update_task`, no Smokeball call anywhere in it**. Negative probe: `ACK-` appears 30 times and `ESCALATION_ACKNOWLEDGED` 38 times in the tenant, **all on `3c191bed` (`2026-OPS-001`, the Operator's own notepad), zero on any client matter**. No D1 table; `/opt/data/memories/` empty.

**The worst part, and the reason a fast fix would have been worse than the absence.** Even with a write-back, the confirming attorney cannot be recorded. Three independent blockers: the `escalation_append` schema has nine properties under `"additionalProperties": False` with no actor field; the replying sender is tested only as a boolean roster gate and then discarded; and `auth_mode: authorization_code` on both seats means every write lands under whoever clicked Allow. A write-back built the obvious way would satisfy the **letter** of the client commitment while producing **an affirmative false record on a legal matter**, saying one attorney confirmed when another replied. A confirmation is precisely the fact a malpractice question turns on. The audit's ordering is therefore explicit: capture the verified replying sender on the ack event **first**, then write the confirmer as memo content, and never lean on Smokeball `createdBy`, which under `authorization_code` cannot be right for any multi-attorney firm.

## How it was detected

By audit, not by an instrument and not by a client. §3.11 traced the chain from `customer.yaml` through the skills to the ledger, ran a negative probe over the tenant for `ACK-` and `ESCALATION_ACKNOWLEDGED` strings, and recomputed all 88 tokens to confirm the store was sound before concluding that the identity input was not.

The audit also records that the live code was **never exercised**: 86 fired, 2 acked, and both acks landed on 2026-07-15 three minutes after a manual cron trigger recorded in `/opt/data/probe-1935-escalator.log`. Zero acks in the 16 days since. A path that is never taken generates no signal, which is why nothing fired.

## Timeline as recorded

| Time (UTC)          | Event                                                                                           | Source                                            |
| ------------------- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| 2026-07-15          | The only two ack events on the tenant, three minutes after a manual cron trigger                | Audit §3.11, `/opt/data/probe-1935-escalator.log` |
| 2026-07-25          | `ACK-76FNK7` leads the alert                                                                    | Audit §3.11                                       |
| 2026-07-28          | The same item is folded into a collapsed group, same code, re-composed prose                    | Audit §3.11                                       |
| 2026-07-31          | Audit published: 86 fired, 2 acked, 83 distinct keys, 88/88 token matches, zero acks in 16 days | Audit §3.11                                       |
| 2026-08-02 16:05:53 | #2151 filed as remediation item 15                                                              | Issue metadata                                    |
| 2026-08-17          | Still open, all four acceptance criteria unchecked                                              | Issue state                                       |

## What changed to prevent recurrence

**Landed:** nothing yet for the identity defect. Stated plainly because the alternative is a note that reads like a fix.

**Open, and the shape is decided.** #2151's four acceptance criteria: derive `item_key` only from stable record fields (`matter_id`, `source_id`, `authored_date`) with no model-composed input; collapse duplicate tasks so one obligation mints one key, deduped on the stable key **before** raising; and two runtime observations, that the same deadline raised on two consecutive days carries the same `item_key` both days, and that an acked item does not re-fire within its snooze window across a day boundary, with a `crane_verify` recorded.

**Related and sequenced before any write-back**, audit remediation item 16: capture the verified replying sender on the ack event and write the confirmer as memo content. The audit's instruction is "build this _before_ any write-back, never after".

**The general lesson that did land.** The class here is a model-composed value used as a key. The hardening epic's Track 5 (#2390) makes that structural: values are projected by code and the model composes prose around them, never the reverse. The audit states the same rule at remediation item 2.

## Shadow-firm scenario

Not in #2389's starting set. A fitting scenario is mechanical and cheap: raise the same deadline on two simulated consecutive days and assert one `item_key`, which is #2151's third acceptance criterion expressed as a standing rehearsal rather than a one-shot proof.

## Ladder consequence

None applied at the time. Retrospectively: the escalator's ack path is at **Rung 1**, and the audit's own observation is the reason. 86 fired against 2 acked means the path had never been exercised under review, so there is no rung-2 observation window to rest anything on.

## Not recorded

- When the label-derived key was introduced, so the defect's age is unknown.
- Whether any client ever acked an item and saw it re-fire. The tenant shows two acks, both on 2026-07-15 following a manual trigger, so the client-facing occurrence count is effectively unobserved.
- Which duplicate tasks mint twin keys, by name. The audit states the pattern, not an inventory.

# Post-incident note: the Operator's only send tool raised on every call

**Backfilled 2026-08-17 under #2391.** No note was written at the time. Every fact below is attributed to [#2348](https://github.com/venturecrane/ss-console/issues/2348); nothing is reconstructed.

| Field                   | Value                                                                                                                                                                                                                                                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Incident date           | 2026-08-13 (first observed error). The defect shipped 2026-08-11 in overlay `53a3aab`.                                                                                                                                                                                                                             |
| Seat / surface          | `pilot-smokeball`, release `ec3fb713`; the `smd_send_message` tool, which is the single send path for both transports                                                                                                                                                                                              |
| Severity                | SEV2 by the ADR 0064 ladder as this note reads it: degraded, a capability failing, not client-visible on the affected seat. No severity was recorded at the time.                                                                                                                                                  |
| Detected by             | Sentry, issue `SMD-OPERATOR-1B`, first seen 2026-08-13T14:06Z, tenant `pilot-smokeball`                                                                                                                                                                                                                            |
| Detection lag           | The handler shipped broken in overlay `53a3aab` on 2026-08-11; pilot adopted `ec3fb713` at 2026-08-12T19:09Z; the first error came from the next morning's 07:00 cron and Sentry saw it at 2026-08-13T14:06Z. So about 19 hours from adoption to first signal, and the error could not have fired before adoption. |
| Detection to resolution | Unresolved. Four repo-layer acceptance criteria are met, three runtime criteria are unchecked as of 2026-08-17.                                                                                                                                                                                                    |
| Client impact           | None. #2348 records `ashton-price` and `smd` as authoring no Email connector, so neither could send regardless; only `pilot-smokeball` was both broken and restored by the fix.                                                                                                                                    |
| Status                  | Open, [#2348](https://github.com/venturecrane/ss-console/issues/2348)                                                                                                                                                                                                                                              |

**Sources.** Issue [#2348](https://github.com/venturecrane/ss-console/issues/2348) in full, opened 2026-08-13T16:03:26Z; Sentry `SMD-OPERATOR-1B`; the traceback and file references quoted in that issue (`tools/registry.py:588`); issue [#2258](https://github.com/venturecrane/ss-console/issues/2258), whose broker rework introduced the handler and pulled the alternative send tools off the menu.

## What broke

**The Operator's only send tool had never delivered a message.**

Hermes dispatches every tool as `entry.handler(args, **kwargs)`, so the tool arguments arrive as a dict in the first **positional** slot. `_smd_send_message` shipped as `def _smd_send_message(**kwargs)`, so every invocation raised `TypeError` inside `registry.dispatch` before any transport ran:

```
TypeError: _smd_send_message() takes 0 positional arguments but 1 was given
  File "tools/registry.py", line 588, in dispatch
    return entry.handler(args, **kwargs)
```

Not a regression. The handler arrived already broken in overlay `53a3aab` on 2026-08-11, the ss#2258 broker rework.

**Three defects, not one**, as #2348 enumerates them:

1. the signature takes no positional argument;
2. the body reads `payload = dict(kwargs)`, so a signature-only fix would send an **empty payload and still report `"Sent (message ...)"`**;
3. once it stops crashing, a seat authoring no Email connector falls through `_seat_email_adapter()`'s `agentmail` default to a broker call for a mailbox that was never provisioned, trading a loud failure for a quiet one.

Defect 2 is the one that matters most for this venture's failure taxonomy: the naive fix produces a confident success string over an empty send.

**Blast radius.** #2258 pulled the four AgentMail send tools and msgraph's `send_message` off the menu, making `smd_send_message` the single send path for both transports. Seats authoring a live **autonomous** external send take the direct tool path and were broken. `draft_for_review` and `confirm` seats route around it via `_dispatch_approved_send`, which calls `outbound_send.*` directly, so they were unaffected. The exposure ladder is why this was a SEV2 and not worse: every live routine was drafting.

**Why CI could not catch it.** `tests/test_msgraph_transports.py` called the handler **by keyword**, which is the shape the handler was mistakenly written for, and no test anywhere in the overlay asserted anything about handler callables. The test agreed with the bug.

## How it was detected

Sentry, `SMD-OPERATOR-1B`, first seen 2026-08-13T14:06Z on tenant `pilot-smokeball`, release `ec3fb713`. The error surfaced only when the seat next tried to send: pilot adopted `ec3fb713` at 2026-08-12T19:09Z and the first error came from the next morning's 07:00 cron.

A corroborating negative signal is recorded in the issue and is worth keeping: the pilot deadline-escalator mails visible in ss#2344 **stop at 2026-08-09**. Absence of expected output was available as evidence and was not what raised the alarm. That is the same silence-is-not-loud class as #2367.

## Timeline as recorded

| Time (UTC)              | Event                                                                          | Source         |
| ----------------------- | ------------------------------------------------------------------------------ | -------------- |
| 2026-08-11              | Handler arrives already broken in overlay `53a3aab`, the ss#2258 broker rework | #2348          |
| 2026-08-09              | Last pilot deadline-escalator mail visible in ss#2344                          | #2348          |
| 2026-08-12 19:09        | Pilot adopts release `ec3fb713`                                                | #2348          |
| 2026-08-13 ~07:00 local | First failing cron run                                                         | #2348          |
| 2026-08-13 14:06        | Sentry `SMD-OPERATOR-1B` first seen                                            | #2348          |
| 2026-08-13 16:03:26     | #2348 filed                                                                    | Issue metadata |

## What changed to prevent recurrence

**Landed (repo layer, four acceptance criteria marked met on #2348).** A guard asserts every registered tool handler accepts Hermes' dispatch shape, and **was shown to fail against the unfixed handler before it passed**, which is the property that makes it an instrument rather than a decoration. `smd_send_message` takes the positional args dict and reads its payload from it. The agentmail transport test asserts the message **body**, not only the call count, which is what closes defect 2. A send from a seat authoring no Email connector refuses by name instead of defaulting to agentmail, while an unreadable config still sends, which closes defect 3 without turning a config read failure into an outage.

**Open (runtime layer, three acceptance criteria unchecked).** `pilot-smokeball` running an overlay containing the fix, with `overlay-ref-drift.py` exit 0 observed; an autonomous internal send driven on the pilot seat arriving in the recipient's mailbox, **observed at the far end** rather than read from the tool's return value; and the broker's transmit row for that send present in `audit_log`, queried directly. #2348 words the last two that way deliberately: both transports return a string on success and `send_via_msgraph` always returns `"(sent via msgraph, id unavailable)"`, so a transcript saying "Sent" is compatible with nothing arriving, which is the exact failure being fixed.

**Not fixed by this, per #2348.** `ashton-price` and `smd` author no Email connector, so the fix keeps their failure loud but neither seat can send until Track E wires Graph. The general question of whether an unauthored connector should ever silently default, for every consumer rather than just this tool, is filed separately.

## Shadow-firm scenario

Not in #2389's starting set. The right scenario is the third runtime criterion turned continuous: drive a send and assert the audit row **and** far-end arrival, never the tool's return string. As #2348 shows, a scenario that scores on the return value would have passed throughout the outage.

## Ladder consequence

None applied at the time. Retrospectively the ladder is why this stayed contained: every live routine sat at `draft_for_review` and routed around the broken tool through `_dispatch_approved_send`. Any routine whose Rung 3 evidence had been recorded before 2026-08-11 would have been silently non-functional from that date, which is the argument for the demotion rule keying on incidents rather than on someone noticing.

## Not recorded

- Whether any send was attempted between the 2026-08-11 overlay ship and the 2026-08-12T19:09Z adoption. The error could not fire before adoption, but attempt counts are not stated.
- The exact local time of the 07:00 cron in UTC.
- How many send attempts failed in total before the fix.
- Why the escalator mails stop at 2026-08-09 when adoption was 2026-08-12; #2348 records both facts and does not reconcile them.

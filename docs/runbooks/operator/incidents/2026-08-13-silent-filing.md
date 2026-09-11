# Post-incident note: the demand letter was filed correctly and the reply that would have said so was held

**Backfilled 2026-08-17 under #2391.** No note was written at the time. Every fact below is attributed to [#2367](https://github.com/venturecrane/ss-console/issues/2367); nothing is reconstructed.

| Field                   | Value                                                                                                                                                                                                                                                                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Incident date           | 2026-08-13                                                                                                                                                                                                                                                                           |
| Seat / surface          | `pilot-smokeball`, the reply path; matter 2026-PI-104; the drafting lane                                                                                                                                                                                                             |
| Severity                | Not classified at the time. This note assigns none under ADR 0064: the Operator was not down and did not act outside its entitlements. It did the opposite, refusing to send. Recorded because from the firm's side it is indistinguishable from a total failure of the deliverable. |
| Detected by             | Found during #2258's runtime proof, by the session running that proof, not by an alert                                                                                                                                                                                               |
| Detection lag           | The hold was written at 2026-08-13T21:21:53.988Z and #2367 was filed at 2026-08-13T21:31:16Z, about 9 minutes. That is the lag for a session already watching the ledger, not for anyone else.                                                                                       |
| Detection to resolution | Unresolved. #2367 is open with all five acceptance criteria unchecked as of 2026-08-17.                                                                                                                                                                                              |
| Client impact           | None reached a client: the exchange was with a probe address, `ss-probe-admin@agentmail.to`. On a client seat the impact is stated in the issue as "asked for a demand letter, got silence", while the letter sits on the matter, complete, with every reservation properly marked.  |
| Status                  | Open, [#2367](https://github.com/venturecrane/ss-console/issues/2367)                                                                                                                                                                                                                |

**Sources.** Issue [#2367](https://github.com/venturecrane/ss-console/issues/2367) in full; the verify ids it cites (`vfy_01KZYFZH6QNR9H3XX8FYDHBWZQ` for the filed document, `vfy_01KZYFFV2RJRCDG2NP56CS01VD` for the Phase 3 six-leg proof); `operator/skills/demand-letter-drafter/SKILL.md:451-481` (the authored recovery); issue [#2258](https://github.com/venturecrane/ss-console/issues/2258), during whose runtime proof this was found.

## What broke

A firm admin emailed `pilot-smokeball` asking for a demand letter on 2026-PI-104. **The Operator did the work correctly**: it read all fifteen documents on the matter and filed a `.docx` through the checked seam (`vfy_01KZYFZH6QNR9H3XX8FYDHBWZQ`).

Then it told nobody. Its reply was drafted and held:

```
2026-08-13T21:21:53.988Z  REPLY_HELD
  reason      : fabrication:tier1_marker
  recipient   : ss-probe-admin@agentmail.to
  reply_channel: True
```

The held draft, read out of the seat's own inbox, names the filed document and lists the twelve markers the attorney must resolve. The two dollar figures in it are `$9,310.02`, the Kaiser lien read that session from the Kaiser Third-Party Liability Assertion on the matter, and `$12,500.00`, the MedFin payoff read that session from the MedFin Payoff Statement. **Both are on the record. Neither is fabricated. Both are cited to their source and date in the sentence that carries them.**

**The mechanism.** #2258's Phase 3 closed the case where the gate forbade what the skill permits, by giving `specific-dollar-amount` a provenance-scoped exemption keyed to the session's read register, proven on the shipped overlay across six legs (`vfy_01KZYFFV2RJRCDG2NP56CS01VD`). That plan said explicitly, "no change to any other seat or output path", and the scoping was deliberate. This is the consequence becoming visible one path over: **the reply path does not supply `allowed_money`**, so on that path the gate still forbids exactly what the skill permits, on figures the Operator had just read off the firm's own documents.

**The part the issue calls more worrying than the hold.** `operator/skills/demand-letter-drafter/SKILL.md:451-481` prescribes the recovery: on a content-gate refusal, "do not retry the same content, and do not drop the work. Redraft once... Strip only the flagged content class. If refused twice, deliver the minimal factual note." That did not happen. The ledger:

```
21:21:53.918  mcp_agentmail_create_draft   internal_write  ok
21:21:53.988  REPLY_HELD                   fabrication:tier1_marker
21:22:16.691  LLM_TURN_COMPLETED
```

No redraft, no minimal note, no second attempt; the turn ended 23 seconds later. #2367 states **two candidate causes and does not distinguish them**: (1) the hold is a delivery-path event the agent is never told about, so the authored recovery cannot fire regardless of how well it is written, or (2) the agent was told and did not act. The issue names (1) as the one to check first, and as a failure shape this venture has hit before: authored is not delivered, and the surface the channel actually reads is the only thing that counts.

## How it was detected

Incidentally, by a session running #2258's runtime proof and reading the audit ledger. **No alert fired**, and this is the finding underneath the finding: a correct deliverable that nobody is told about produces no error, no red state and no page. It is silence that looks exactly like success from the inside and exactly like failure from the client's side. Track 4 of the hardening epic (#2386, #2388) exists for this class, a terminal-state invariant over every routine run so silence becomes loud.

## Timeline as recorded

| Time (UTC)                | Event                                                                                                                                                            | Source                                  |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| 2026-08-13 (before 21:21) | Firm admin emails asking for a demand letter on 2026-PI-104; the Operator reads all fifteen documents on the matter and files a `.docx` through the checked seam | #2367, `vfy_01KZYFZH6QNR9H3XX8FYDHBWZQ` |
| 2026-08-13 21:21:53.918   | `mcp_agentmail_create_draft`, `internal_write`, `ok`                                                                                                             | #2367 ledger extract                    |
| 2026-08-13 21:21:53.988   | `REPLY_HELD`, `reason: fabrication:tier1_marker`, recipient `ss-probe-admin@agentmail.to`                                                                        | #2367                                   |
| 2026-08-13 21:22:16.691   | `LLM_TURN_COMPLETED`. No redraft, no minimal note                                                                                                                | #2367                                   |
| 2026-08-13 21:31:16       | #2367 filed rather than fixed, per scope discipline                                                                                                              | Issue metadata                          |

The exact time the admin's request arrived is `not recorded` in the issue.

## What changed to prevent recurrence

**Landed:** nothing. #2367 was filed rather than fixed, deliberately, under scope discipline. Saying otherwise would make this note the thing it documents.

**Open**, the five acceptance criteria on #2367: the reply and delivery path supplies the session's provenance register to `outbound_gate.evaluate`, so a figure read from the matter this session passes on that path exactly as it does on the drafting path; an invented figure in a reply still blocks, in both directions, or the narrowing is a hole; the two candidate causes are distinguished and the answer recorded either way; a `REPLY_HELD` reaches the agent as something it can act on and the authored redraft-once recovery is observed firing; and card 18 is re-run on 2026-PI-104 with the letter filed **and** the admin receiving the reply naming it, with the stated falsifier being a filed draft with no delivered reply, which is exactly what happened here.

## Shadow-firm scenario

Not in #2389's starting set as filed. It belongs there, and the mechanical observable is already written as #2367's last acceptance criterion: drive the request, then assert both the filed document **and** the delivered reply. A scenario that asserts only the filing would pass on this incident.

## Ladder consequence

None applied at the time. Retrospectively, the drafting lane's reply path on `pilot-smokeball` sits at **Rung 2 with a failed observation**: the routine drafted, a human reviewed nothing because nothing was delivered, and the review-period observation for that window records a hold rather than an output. Under the instrument that is not a rung-2 pass, so the routine cannot climb.

## Not recorded

- Which of the two candidate causes is real. The issue states both and declines to pick, and this note declines with it.
- When the admin's request arrived, so the total turnaround is unknown.
- Whether the same hold had occurred on earlier reply-path runs. Nothing in #2367 establishes a first occurrence.
- Whether the held draft was ever delivered by another route.

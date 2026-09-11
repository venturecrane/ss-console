# Post-incident note: <one line, what happened, in the client's terms>

**Filename convention:** `YYYY-MM-DD-short-slug.md`, dated by the day the incident began.

| Field                   | Value                                                                                                                                                                                 |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | `YYYY-MM-DD` (or a range)                                                                                                                                                             |
| Seat / surface          | e.g. `pilot-smokeball`, the admin console, the client portal                                                                                                                          |
| Severity                | SEV1 / SEV2 / SEV3 against the ADR 0064 ladder in `docs/handbook/incident-response.md`. If no severity was assigned at the time, say so and mark the assignment as made in this note. |
| Detected by             | The instrument, the person, or `not recorded`                                                                                                                                         |
| Detection lag           | Time from occurrence to detection, or `not recorded`                                                                                                                                  |
| Detection to resolution | Or `not recorded`. Do not compute this from issue timestamps unless the issue is the resolution.                                                                                      |
| Client impact           | What a client experienced, or `none observed` with the reason                                                                                                                         |
| Status                  | Open / closed, with the issue that owns it                                                                                                                                            |

**Sources.** Every fact below traces to one of these. List them explicitly: issue numbers, audit sections, doctrine laws, file paths, `vfy_` ids.

## What broke

The mechanism, not the symptom. State the defect precisely enough that someone could reproduce it. Where two candidate causes were never distinguished, say that rather than picking one.

## How it was detected

Which instrument fired, or which human noticed, and by what route. If it was found incidentally while doing something else, say so, because that means the instrument that should have caught it did not.

## Timeline as recorded

Only timestamps that exist in a source. Each line carries where it came from.

| Time (UTC) | Event | Source |
| ---------- | ----- | ------ |
|            |       |        |

If the timeline is thin, leave it thin. A short honest timeline is the finding.

## What changed to prevent recurrence

Cite the PR, the issue, the gate, or the doctrine law. Separate what has **landed** from what is **still open**, and mark runtime claims as runtime claims: a merged repo change is not a wired one (Law 9).

- **Landed:**
- **Open:**

## Shadow-firm scenario

The scenario that replays this incident (`#2389`), or `not yet written`. A scenario is only trusted once it has been observed to FAIL against the unfixed state.

## Ladder consequence

Which routines were demoted and to which rung, per `docs/runbooks/operator/enable-gate-checklist.md`, or `none` with the reason (for example: the routine was never above Rung 1).

## Not recorded

The facts a reader will reach for that no source establishes. Naming them here is what stops the next reader from inventing them.

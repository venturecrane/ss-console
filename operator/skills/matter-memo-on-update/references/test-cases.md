# Matter Memo on Update - Test Cases (v0.2.0)

The synthetic fixtures live in `operator/fixtures/law-firm/matter-memo-on-update/`. Each is a Smokeball `matter.updated` event + canned `get_memos_on_matter` / `get_staff` reads, with a grader's `fails` conditions. The set proves the two things that make or break this version: it logs the touch (who/when/how) once, and it stays loop-safe and fabrication-free.

> **Note (v0.2.0).** This version records who/when/how and does **not** compute a field-level diff (deferred - see `algorithm.md`). Fixtures that exercise the snapshot-diff design (`mmou-first-touch-02`, `mmou-empty-diff-selfwrite-03`, and the field-diff assertions in `mmou-clean-fieldchange-01`) describe the deferred enhancement, not this version's behavior. The active expectations are below.

| Fixture                        | Active expectation in v0.2.0                                                                                                       |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
| `mmou-clean-fieldchange-01`    | A matter change → exactly one memo: correct actor, calendar date, source, and the `op-mmou:…` tag. No field-diff lines (deferred). |
| `mmou-duplicate-delivery-04`   | A repeat whose `op-mmou:<matterId>:<timestamp>` tag is already in an existing memo → **no second memo**.                           |
| `mmou-userid-absent-05`        | `userId` missing or `get_staff` 404 → a correct memo attributed to "an unidentified user," never a name, no retry.                 |
| `mmou-first-touch-02`          | Deferred design (silent baseline). In v0.2.0 a first event simply writes the who/when/how memo.                                    |
| `mmou-empty-diff-selfwrite-03` | Deferred design (empty-diff stop). Loop-safety in v0.2.0 comes from subscription discipline + the idempotency tag, not a diff.     |

## The line every fixture holds

- **No em-dash, ever.** A memo body containing `-` (U+2014) is refused before it is written; a fixture whose correct output carries an em-dash is itself wrong.
- **No fabrication.** The `userId`-absent fixture fails if the change is attributed to any named person. The `.NET`-ticks timestamp must render as the correct calendar date, not a 1900s date. No invented field diff, reason, or clock time.
- **Facts, not analysis.** No fixture's correct output interprets the change ("status moved to Pending, likely settlement" fails).
- **Internal only.** Any fixture fails if the skill attempts an email/send, a matter patch, a task, a fund operation, or an `execute_code` / `write_file` / `memory` call. The only write is `create_memo`.
- **Tiny call budget.** A fixture fails if the skill probes extra reads or retries a failed `get_staff` / `get_memos` (it would risk tripping the connector breaker and losing the write).

# Post-incident note: the deadline digest arrived with every matter unnamed, and the fix that should have prevented it had been running dead for two days

| Field                   | Value                                                                                                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Incident date           | 2026-08-24 (defect present since the ss#2547 fix deployed, 2026-08-22)                                                                                             |
| Seat / surface          | `pilot-smokeball`, the deadline-miss-escalator digest to scott@smd.services                                                                                        |
| Severity                | SEV3, assigned in this note (pilot seat, internal recipient, no client exposure; the class it revealed is SEV2-shaped on a client seat)                            |
| Detected by             | The Captain, reading the mailbox. The ss#2547 pager fired correctly on the 3 refusals, but no instrument judged the 4th send, the degraded artifact, as a failure. |
| Detection lag           | ~3 hours (send 14:04Z, Captain surfaced it in session ~17:00Z)                                                                                                     |
| Detection to resolution | Repo layer same day (overlay #318, console #2571/#2572); the naming half proved on the runtime 2026-08-25 (see Closed)                                             |
| Client impact           | None observed, the pilot's recipient is the Captain. On a client seat the same chain delivers an unusable deadline digest to a firm principal.                     |
| Status                  | Naming half closed 2026-08-25 (vfy_01M0WMTQ5B3X8D92K3XQ2TJDEX). Still open: the withhold-and-page rehearsal, and a presentation defect the same run surfaced.      |

**Sources.** vfy_01M0THACG928CA9ANNVECFBW95 (both defects probed live), vfy_01M0T4WS3CFP19Q38N57012PFE (refusal rows), ss#2547, ss#2390, ss#2405, overlay PRs #299-era ss#2547 train and #318, console #2548, #2571, #2572; `shared/pre_run_handoff.py`, `plugins/hermes-smd-trust/outbound.py:584`, `operator/skills/deadline-miss-escalator/pre_run.py`.

## What broke

Three independent defects, stacked:

1. **The ss#2547 handoff never bound in production, path half.** The scheduler runs `pre_run.py` with `HERMES_HOME` set to the persona home (`/opt/data/profiles/operator`), so the writer landed its file at `/opt/data/profiles/operator/.smd/pre_run/deadline-miss-escalator.json`. The reader (agent process, `HERMES_HOME=/opt/data`) searched `/opt/data/.smd/pre_run/`, a directory that has never existed on the seat. Every morning's handoff was written perfectly and read by nothing.
2. **Clock half (latent behind 1).** The cron session id stamps the fire time in the routine's cron timezone (Phoenix: `…_070026` for a 14:00Z fire) while the container's local clock is UTC (TZ unset). `_in_window` tried the naive stamp "as UTC" and "as seat-local", in a UTC container those are the same instant, 7 hours outside the 20-minute window. The docstring's premise ("the two readings differ by exactly the seat's UTC offset") assumed the container clock was the seat's civil clock; the scheduler's cron timezone is a third clock the module never saw.
3. **The refusal text offered the degraded path.** With the register empty, the tier3 gate refused the send three times, correctly, and its own message offered "or remove the unverified value and state that it needs confirmation." The model complied, fifteen items at a time, and the fourth send passed the gate with every line reading "matter number unavailable." Separately, matter numbers were never fetched anywhere in the chain (the pull carries GUIDs; the connector's join ran only on the MCP surface), so the "unavailable" text was the projection's permanent output, not a transient failure.

The class, named: **merged green, inert in production** (gate-regression). Both handoff tests passed because writer and reader shared `tmp_path` and a fabricated session stamp; no test ran the two halves under the runtime's actual environment split. Same family as the parser-tests-must-use-the-runtime-prompt-shape lesson.

## How it was detected

A human. The Captain read the digest in the mailbox and said it was unacceptable. The ss#2547 pager did fire on the three refusals (instrument working as designed), but its scope is refusals, nothing measured the artifact that passed. The inertness of the handoff fix had been invisible for two days because its failure mode is the status quo ante (refusals continue), which is exactly the "inert control with no signal" its own docstring warned about.

## Timeline as recorded

| Time (UTC)           | Event                                                                                     | Source                            |
| -------------------- | ----------------------------------------------------------------------------------------- | --------------------------------- |
| 2026-08-22           | ss#2547 handoff seeding merged both repos (console #2548, overlay #309-era train)         | git log both repos                |
| 2026-08-23           | OVERLAY_REF 0f84a625 pinned; pilot reprovisioned (seat runs the pinned ref)               | vfy_01M0THACG928CA9ANNVECFBW95    |
| 2026-08-24 14:00:24Z | pre_run writes the handoff (18 dates, 7 matter GUIDs) to the persona home                 | file `started_at`, seat probe     |
| 14:03:05–14:03:51Z   | Three tier3 refusals, `register_was_empty=true`, 10 past dates                            | audit_export; ss#2547 pager email |
| 14:04Z               | Fourth send passes: digest with all items "matter number unavailable" delivered to scott@ | mailbox                           |
| ~17:00Z              | Captain surfaces the artifact; session probes both defects live                           | this session                      |

## What changed to prevent recurrence

- **Landed (repo layer):** overlay #318, reader resolves the persona home from the routine name; binding is file recency on the reader's own clock (naive stamps rejected); handoff widened to validated `(matterNumber, dates)` records seeded as associations; empty-register refusal offers read-or-report-failure, never value-removal; heartbeat gains the `degraded` send-refusal kind. Console #2571, matter numbers projected in code end to end (connector join, typed absence). Console #2572, a digest with zero resolved numbers is withheld and paged; audit-failure on the degraded path wakes stripped instead of re-shipping the artifact; partial failure ships and pages.
- **Closed 2026-08-25 (runtime layer, the observed-run half):** the pilot's 07:00 MST run (session `cron_6c073ab9b3fc_20260825_070034`, seat on the pinned overlay `f16d4920`) named all seven matters by the firm's own number (`2026-PI-101/102/103/104/105/106` and `PI-2026-0001`), with zero occurrences of "matter number unavailable", zero tier3 `IDENTIFIER_UNVERIFIED` rows in the session, and the handoff consumed fresh at the run timestamp (`/opt/data/profiles/operator/.smd/pre_run/deadline-miss-escalator.consumed.json`, `started_at` 14:00:31Z, seven validated `(matterNumber, dates)` records). The #2575 pair fix holds: the digest's own "last raised" lines were not refused. vfy_01M0WMTQ5B3X8D92K3XQ2TJDEX.
- **Open (runtime layer):** the staging budget-0 rehearsal proving the withhold-and-page path. The 08-25 run was not degraded, so that path never executed and remains a repo claim (Law 9).
- **Open (class):** ss#2573, the other three pre-run handoffs are dates-only and the law-seat boilerplate predates the wake-payload source.
- **Open (new, found by the same run):** the digest's identifiers are now correct and its **presentation is not**. The turn abandoned `references/output-format.md` and composed its own shape: `NEEDS YOU TODAY` in place of `## Needs you today (<count>)`, pipe-delimited rows in place of the prose templates, and an invented section title, "UNDER ACTIVE ESCALATION ELSEWHERE (no action required from you)", a string that has never existed in this repo (`git log --all -S`). Consequences, all downstream of that one deviation: the only markdown block marker in the 4,280-character body was a single `---`, so `looks_like_report()` returned true and the html part rendered, but with no `#` and no list markers every band collapsed into one run-on `<p>`; thirty-eight `elsewhere` rows rendered flat, twenty of them for one matter; the projection's field name leaked into the reader's copy as an English word, so a court date one day out reads "authored 2026-08-26"; and the top item's truthful item-scoped "No Operator raise on record" sits directly above twenty rows of the same matter marked "last raised 2026-08-24", which reads as a contradiction. Nothing measured it: `SPEC_GATE_TRIGGERED` recorded `output_class=staff`, `reason=no_spec`, `rules=["voice"]`. The gate stamps a `digest_sha256` and the send path never compares what leaves against it, a check that cannot fail (Law 12). vfy_01M0WM79FKAXEC3EJA3TNTMB3Y.

## Shadow-firm scenario

Not yet written. The candidate: a scheduler-shaped environment split (writer under the persona home, reader under the data root, cron stamps in a non-UTC zone) that the 2026-08-22 tests would have failed against.

## Ladder consequence

None, the escalator stays at its current rung. The digest recipient is the Captain (pilot), the refusal gate held three times, and the shipped fix makes the degraded output impossible rather than demoting the routine that produced it.

## Not recorded

Why the escalator's digest routes to scott@smd.services while client-verification-tracker routes to smdurgan@smdurgan.com, nothing in `customer.yaml` obviously selects between them; unprobed. Whether the 08-22 and 08-23 escalator runs also refused (only 08-24 was pulled in detail).

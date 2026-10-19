# ADR 0093: Seat policy profiles: code floors plus a reviewed role layer

- **Status:** Accepted (2026-10-19)
- **Decider:** Captain (asked 2026-10-19 that per-seat gate posture be fixed now, on the captain-mark couples seat)
- **Builds on:** ADR 0090 (hosted-agent roles, `seat.role`), ADR 0091 (privacy by space), the identifier gate and output checklist in `hermes-smd-overlay`
- **Related:** `tests/gate-coverage-snapshot.test.ts` (the snapshot-mirror pattern this reuses), `operator/contracts/runtime-controls.yaml` (the identifier gate's control row and its rollback lever)
- **Scope:** how a seat's outbound gates are loosened, who may loosen them, and how a loosening is reviewed. It does not change any law-firm seat's posture.

## Context

Every seat runs the same overlay gates, and those gates were written with law-firm assumptions. The identifier gate refuses a date the agent did not read this session; the output checklist enforces paralegal conventions (no exclamation marks, no unknown all-caps words). On the captain-mark couples seat (ADR 0090) that refused the job itself: a reminder of the couple's own recorded anniversary carries a date in a year nobody wrote down, and household wording ("RSVP", "Happy birthday!") reads as a checklist violation.

The only way out that existed was the operator-only rollback lever `SMD_IDENTIFIER_GATE_MODE=report`, set as a Fly secret on that one seat. It turns off ALL identifier blocking on the seat, SSNs and case numbers included, it is invisible to review (a secret has no diff), and nothing reported it. At one seat that is a bad trade; at a hundred seats it is the pattern by which nobody can say what any seat allows. The other way out, editing a gate globally for one client, changes every client.

## Decision

**1. Floors live in code and are never authorable.** SSN, A-number, receipt number, case number and PAIR (a case number and a date asserted together) block on every seat, whatever any profile says. The floor does not key on the free-text `vertical`, so `vertical: mixed` or a typo cannot lose it, and a seat with no role resolves to the strict platform default.

**2. The role layer carries the defaults, in `operator/roles/<role>/role.yaml` under `policy:`.** A role is a packaged job (ADR 0090), and what the job legitimately sends is part of the package. The block ships in the image (`COPY operator/roles/`) together with the overlay code that reads it, so config and the code that interprets it cannot skew: an older image ignores the key, and a newer image never meets a role file it predates. Unknown keys, unknown values, an unknown role or an unreadable file all resolve to the platform default, which is today's behavior exactly.

**3. A loosening must cite its reason.** Any `policy:` value looser than the platform default requires a non-empty `rationale` and an `adr` naming a record that exists in `docs/adr/`. `tests/role-policy.test.ts` enforces the keys, the enums and the citation. Knobs are narrow and sized to evidence (the audit rows of the seat that misfired), never a mode switch. The first ones are `identifier_gate.recurring_dates: verify_by_month_day` (a date verifies when its month and day match a date read this session from the seat's own record store, in this year or next: the computed occurrence of a recorded birthday, while an invented trip date stays blocked) and `output_checklist.profile: household` (drops only the checklist rules evidenced as misfiring on household sends).

**4. Every change to what a seat allows is a seat x gate diff in review.** `tests/policy-matrix.test.ts` projects each live seat's role and role policy into `operator/contracts/policy-inputs.overlay.json`, copied to the overlay's `tests/contract/policy_inputs.json` in the same change. The overlay resolves every seat with its real resolver against a hand-maintained, human-readable expected matrix and fails on any entry looser than the platform default that lacks the role's rationale and ADR. A loosening therefore cannot merge without someone editing a line that says, in words, which seat now passes what.

**5. The env lever is an incident-only, fleet-wide rollback, flagged by boot smoke.** `SMD_IDENTIFIER_GATE_MODE` keeps its documented semantics so an incident can still be rolled back in minutes. It is not a per-seat setting. Boot smoke warns, in its summary, on any seat where the variable is set (the name only, never the value), so a lever left behind after an incident is named on every run. It warns rather than fails because during an incident it is the right tool, and a red smoke would block the very reprovision that rolls back. The lever is removed from captain-mark, where the couples role now carries the reviewed equivalent.

## Deferred: seat and vertical layers

A per-seat override block in `customer.yaml` and a per-vertical layer are deliberately not built.

- **Config ahead of image.** `customer.yaml` is published to R2 on merge and re-fetched on every boot, ahead of the image that would read a new block (a seat booting an older image then dies or, worse, the config applier holds the unknown key). A role file ships inside the image with its reader; a seat file does not. The class has already taken seats down (A&P and the pilot, 2026-10-13).
- **Zero features ahead of need.** One seat needs a loosening today, and its need is the role's job, not the seat's. A seat layer is built when a second seat of the same role needs something its role must not grant to every seat of that role; a vertical layer when a vertical, not a role, is the thing that differs. Until then each would be an unreviewed surface with no user.

## Consequences

- captain-mark gets reminders of its own recorded dates and household wording, under a cited, reviewed role policy instead of a hidden secret, and keeps every identifier floor.
- Law-firm seats are unchanged: they select no role, so they resolve to the platform default.
- The seat x gate matrix is the one place to read what every seat allows; it is maintained in the overlay and fed from here.
- The overlay's `contracts/consumes.yaml` note for `SMD_IDENTIFIER_GATE_MODE` is amended to say incident-only fleet rollback, and `operator/contracts/runtime-controls.yaml` records the same.

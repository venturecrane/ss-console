# Hold-ledger remediation: releasing the pilot's black-holed holds

**Status:** active runbook, one-shot (retire after the convergence probe passes).
**Owner:** Operator platform. **Seat:** pilot-smokeball (staging tenant).
**Source:** the 2026-08-24..31 outbound-email review — the verification chase
contradicted its own verified hold-release four days later because
`derive_state` reset only `acked` on a raise, so a hold sentinel that went
`fired` (08-24) -> `resolved` (08-27) -> `fired` (08-31) folded to a
permanently-released hold while the re-raise went on re-firing.

This is **append-only, live-state remediation**. No ledger surgery, ever: the
ledger is derived state, and the fix to the fold re-derives the truth from the
rows already on disk. Every step's probe is recorded via `crane_verify`
(gone-means-gone rule 2: runtime layers are proven by probes, not by the diff).

## 0. Preconditions

The code fix ships in three runtime layers, and the loop is not fixed until all
three are live (a merged overlay fix is not a deployed one):

- **Seat image rebuild + redeploy** — the broker's `validate_append` /
  `derive_state` ship in the image (`operator/templates/Dockerfile`,
  `entrypoint.sh` runs `-m workspace_broker.server`).
- **Skill re-stage to the seat** — `pre_run.py`, the vendored
  `escalation_ledger.py`, and SKILL.md reach the seat via the skills sync.
  **This copy is what un-sticks the live hold loop**; the image alone does not
  fix it, because the wake gate folds state with the skill-dir copy.
- **Overlay pin bump + Machine update** — the escalation plugin (determination
  enforcement) and `shared/escalation_ledger.py` (the `escalation_state` /
  `escalation_append` fold) ship via `OVERLAY_REF`.

## 1. Verify the deploy reached every runtime layer

Before touching state:

1. Seat skill dir: `sha256sum` of each staged `escalation_ledger.py` equals the
   new canonical's hash (compare against
   `operator/contracts/overlay-pairs.json` `sha256`).
2. Broker answers from the new image: a `resolved` append against a raise-less
   test key MUST be refused with the "no prior fired/chased raise" message.
   (A check that cannot fail measures nothing; this one fails loudly on the old
   image, which accepted the bare release.)
3. Overlay pin on the Machine carries the restamped copy and the plugin: the
   `escalation_append` tool schema lists `resolution_note` /
   `role_snapshot_sha256` / `confirmed_via`.

Record each probe (`crane_verify`, method `live_state`).

## 2. Recover the full identifiers

Never operate on truncated identifiers (`f220c8e4...` / `404b292e...` are
prefixes, not names). Read `/opt/data/audit/escalation-ledger.jsonl` on the
seat and list every hold-sentinel item: the `item_key`s whose event sequences
include the hold flow, with full `matter_id`s and the full event sequence per
item. As of the 2026-08-31 probe the two hold matters are
`f220c8e4-eab5-4fd9-8f1d-0becf715b390` (Alvarez) and
`404b292e-ec0f-4c12-aa53-3ea27784cd0e` (Chen); re-derive the list from the
live ledger anyway — this file's snapshot expires.

## 3. Confirm the holds came back ACTIVE (no surgery needed)

Bug 1 needs no data change: the existing `fired`-after-`resolved` rows re-fold
under the fixed `derive_state` and both holds return to ACTIVE on the next
read. Confirm via the `escalation_state` tool (or agent-uid python over the
vendored module): each hold item shows `resolved: false` and the chase gate
treats the matter as held. Record the probe.

## 4. Release each hold properly (supervised one-shot)

For each held matter, in a supervised operator session (`claude -p operator`
per the seat runbook; the interactive escalator is a deliberate no-op
post-render — see "Manual firing" below):

1. Re-verify the signer facts LIVE: `get_matter` + `get_roles_on_matter` (the
   roles payload embeds relationships). The founding determination is the F13
   class: structural `Minor` / `Deceased` layout slots (role entries with no
   `id` under `matter.items`) read as live flags on a single-adult plaintiff.
2. Take the current snapshot hash from the wake line's
   `current_role_snapshot_sha256` on the hold-surface plan (or run the chase
   pre_run's snapshot helper directly on the seat and copy its output; never
   compute one by hand).
3. `escalation_append` with `derive_only: true` on the hold identity —
   `matter_id` = the matter, `source_id` = `__hold__`, `label` = `chase-hold`,
   `authored_date` = null — then append `resolved` with the handle AND the
   determination: `resolution_note` (e.g. "plaintiff is a single adult;
   Minor/Deceased tags are layout artifacts; verified against live roles
   <date>"), `confirmed_via: "matter_record"`, `role_snapshot_sha256` from
   step 2. `resolved` is non-raising, so no send witness is needed.
4. The tool refuses a bare hold release; a refusal here means a field is
   missing or malformed, not that the flow is broken. Do not retry the same
   content; fix the named field.

## 5. Confirm convergence

The next scheduled chase pre_run tick either suppresses (nothing due) or plans
a chase (one is due) — and the SUPPRESSED_WAKE / EMITTED_WAKE heartbeat shows
**no `surface_hold`** for the released matters. Record the probe. That probe
closes this runbook.

## 6. Tenant-side cleanup (noted, not code)

Delete probe task `28745d01` on 2026-PI-102 in Smokeball — a Captain or
supervised seat action (the connector has no task delete; complete it via
`update_task(is_completed=True)` per the probe-artifact contract in
`operator/CLAUDE.md`). `_is_probe_subject` already excludes it from tracking;
deletion is hygiene, not remediation.

## Manual firing (post-render note)

After the deterministic-render change lands (WS-RENDER), an interactive
escalator invocation is a deliberate no-op: routine composition happens in
`pre_run` and dispatches out-of-turn. Manual firing of a routine schedules a
one-off job on the live gateway's own cron store:
`hermes -p operator cron create "2m" "<prompt>" --repeat 1 --name <name>` via
the seat-probe path (`operator/bin/seat-probe.sh` header documents the
invocation). Never `hermes -p operator cron run <jobid>`, `-z`, or `chat`
through the probe: each starts a second hermes runtime beside the live gateway
(the 2026-09-01 crash-loop incident; rule in `operator/CLAUDE.md`).

/**
 * Gateway loop liveness and the part-1 supervisor's own state (ss#2488 part 2).
 *
 * The defect. On 2026-08-20 the paying client's gateway event loop wedged for 33
 * minutes and every liveness signal a human could see stayed green. Fly's
 * `/health` is a constant. The control-plane heartbeat this worker reads, and the
 * healthchecks.io ping, are emitted by the webhook gate -- a separate process on
 * the seat that never wedged. `work_overdue` would have caught it, but the
 * seat's crons were off for go-live (ss#2332). Three instruments blind by
 * construction, one by configuration.
 *
 * Part 1 (#2502) made a wedged gateway restart itself -- a root supervisor in the
 * Machine entrypoint watches the loop heartbeat Hermes writes and SIGKILLs the
 * container. Proven on hermes-scott: wedge -> new pid in 5m05s, nobody involved
 * (vfy_01M0HEM5VM26XFVD8W4XW19AQF). This module is the half that reaches a
 * person. The gate now ships four fields, and the five conditions below read
 * them.
 *
 * Why two signals and not one. The part-1 restart is ALSO what refreshes the
 * heartbeat. An age-only alert sampled by this worker's 2-minute cron can be
 * overwritten by the recovery before it is ever seen -- the success of part 1
 * would silence part 2. So:
 *
 *   gateway_loop_wedged          the pulse is stale. Catches a wedge the
 *                                supervisor cannot fix (inert, wrong pin, budget
 *                                spent) and the pre-restart window when it can.
 *   gateway_restarted            the supervisor killed the seat inside the last
 *                                hour. The field a restart cannot race: the
 *                                kill-ledger line is on the volume before the
 *                                container dies, so the first beat after reboot
 *                                carries it. Transition-based, so three restarts
 *                                in an hour are ONE page; it self-resolves when
 *                                the window empties.
 *   gateway_supervisor_refusing  the supervisor has spent its 3/hour budget and
 *                                stopped. The loudest state the seat has, and
 *                                before this it reached no inbox at all.
 *   gateway_supervisor_inert     the supervisor is running and nothing will
 *                                recover this seat: it could not resolve the
 *                                profile from argv, or this Hermes pin has no
 *                                loop heartbeat to watch, or the gateway wedged
 *                                DURING startup and never produced a first beat
 *                                (`never-healthy`, added after the 2026-09-01
 *                                pilot-smokeball crash loop). A seat in any of
 *                                the three is indistinguishable from a healthy
 *                                one by every other signal. `supervisorConditions`
 *                                below says why they share one condition.
 *   gateway_loop_unprovable      the seat's own check could not look. OUR
 *                                blindness, paged on its own, never reported as a
 *                                wedge -- spec_control's split, for its reason.
 *
 * NULL-hold, per field, and with `== null` rather than `!== null`: a row whose
 * column does not exist yet (a pre-0107 read, a test fixture) yields `undefined`,
 * which `!== null` lets through, after which `=== 0` is false and the branch
 * pushes `active: false` -- a false RECOVERED email, the one thing this codebase
 * keeps saying must never happen.
 *
 * The threshold contract spans two repos and nothing enforces it: RED must stay
 * BELOW the seat's kill point (SMD_GATEWAY_LIVENESS_STALE_SECONDS x 2 samples +
 * the dump and TERM graces, ~270s at defaults) or the page lands after the
 * restart it was meant to precede. With a 60s heartbeat period and up to 120s of
 * cron lag, the D1 row must be red by ~T+150 for the worst-case sample to land in
 * time; 120s does that. The same sentence is in entrypoint.sh and wrangler.toml.
 */

import type { ConditionState, FleetStatusRow } from './index'

export const DEFAULT_GATEWAY_LOOP_RED_SECONDS = 120
/** Same floor as `redSeconds()`: an env typo must not set 0 and page the fleet. */
const MIN_GATEWAY_LOOP_RED_SECONDS = 60

export function gatewayLoopRedSeconds(raw: string | undefined): number {
  const n = Number(raw)
  return Number.isFinite(n) && n >= MIN_GATEWAY_LOOP_RED_SECONDS
    ? Math.floor(n)
    : DEFAULT_GATEWAY_LOOP_RED_SECONDS
}

/**
 * Conditions for one seat.
 *
 * Each is tri-state on its own source field. `== null` (loose) so `undefined`
 * holds exactly like NULL. The wedge condition additionally requires BOTH
 * `gateway_loop_ok` and `gateway_loop_age_seconds` to be present: `ok=1` with a
 * NULL age is the seat's arming latch or boot suppression, and `null > RED` is
 * false in JavaScript, which would resolve an open wedge on a number nobody
 * measured. Split into three helpers by source field so each stays under the
 * function-length ceiling and a reviewer can read one signal at a time.
 */
export function gatewayLoopConditions(
  row: FleetStatusRow,
  redThresholdSeconds: number = DEFAULT_GATEWAY_LOOP_RED_SECONDS
): ConditionState[] {
  return [
    ...loopPulseConditions(row, redThresholdSeconds),
    ...restartConditions(row),
    ...supervisorConditions(row),
  ]
}

/** gateway_loop_unprovable + gateway_loop_wedged, from ok + age. */
function loopPulseConditions(row: FleetStatusRow, red: number): ConditionState[] {
  const out: ConditionState[] = []
  const slug = row.customer_slug
  if (row.gateway_loop_ok != null) {
    out.push({
      customer_slug: slug,
      condition: 'gateway_loop_unprovable',
      active: row.gateway_loop_ok === 0,
      detail:
        row.gateway_loop_ok === 0
          ? 'The seat cannot read its own gateway loop heartbeat, so it cannot report ' +
            'whether the loop is alive. This is a seat fault, not a wedge: check ' +
            'HERMES_ACTIVE_PROFILE in the gate environment and the mode on ' +
            '$HERMES_HOME/profiles/<profile>/state/.'
          : 'The seat can read its gateway loop heartbeat again.',
    })
  }
  if (row.gateway_loop_ok === 1 && row.gateway_loop_age_seconds != null) {
    const age = row.gateway_loop_age_seconds
    const wedged = age > red
    out.push({
      customer_slug: slug,
      condition: 'gateway_loop_wedged',
      active: wedged,
      detail: wedged
        ? `The gateway event loop last beat ${age}s ago (threshold ${red}s). ` +
          'The Operator is not answering on any channel. The seat supervisor should ' +
          'restart it within ~5 minutes of the wedge; if no gateway_restarted follows, ' +
          'the supervisor did not act -- check gateway_supervisor_state.'
        : `The gateway event loop is beating (${age}s since last beat).`,
    })
  }
  return out
}

/** gateway_restarted, from the kill-ledger count. */
function restartConditions(row: FleetStatusRow): ConditionState[] {
  if (row.gateway_restarts_last_hour == null) return []
  const n = row.gateway_restarts_last_hour
  return [
    {
      customer_slug: row.customer_slug,
      condition: 'gateway_restarted',
      active: n >= 1,
      detail:
        n >= 1
          ? `The seat supervisor restarted the gateway ${n} time(s) in the last hour ` +
            'after its event loop stopped beating. The seat recovered on its own; this ' +
            'page is the record that it happened. Budget is 3 per hour, after which the ' +
            'supervisor stops and pages gateway_supervisor_refusing instead.'
          : 'No supervisor restarts in the last hour.',
    },
  ]
}

/** The three inert-class states, told apart where the on-call reads them. */
function inertDetail(state: string): string {
  if (state === 'not-watching') {
    return (
      'The seat supervisor is NOT watching: this Hermes pin has no loop heartbeat ' +
      'to read. A wedged gateway on this seat will not self-recover. Promote the pin ' +
      'or accept the gap explicitly.'
    )
  }
  if (state === 'never-healthy') {
    return (
      'The gateway NEVER CAME UP on this boot: its loop heartbeat has not gone fresh ' +
      'once since the seat started, past the startup grace. It is wedged during ' +
      'startup, not after it. The supervisor will NOT restart it — killing a ' +
      'slow-starting gateway is what sustained the 2026-09-01 crash loop — so nothing ' +
      'automatic will happen and this needs a human now. Check memory headroom first ' +
      '(1GB is below the floor for a full connector set) and then the gateway startup ' +
      'log for where it stopped.'
    )
  }
  return (
    'The seat supervisor is INERT: it cannot resolve the gateway profile from the ' +
    'container argv, so it will never act. A wedged gateway on this seat will not ' +
    'self-recover. Check /proc/<container-main>/cmdline on the Machine.'
  )
}

/**
 * gateway_supervisor_refusing + gateway_supervisor_inert, from the state word.
 *
 * Three words map onto gateway_supervisor_inert, because the condition's meaning
 * is "the supervisor is up and nothing will recover this seat", not "the argv
 * parse failed":
 *
 *   inert          argv never resolved to a profile, past the seat's startup
 *                  grace. The original meaning.
 *   not-watching   this Hermes pin writes no loop heartbeat to watch.
 *   never-healthy  the gateway wedged DURING startup and has never produced a
 *                  first beat (ss#2488 follow-up, 2026-09-01 crash loop). The
 *                  supervisor deliberately does not kill it — see the entrypoint
 *                  — so a person is the only recovery path, which is exactly
 *                  what this condition exists to produce.
 *
 * Sharing the condition rather than adding a fourth is deliberate. `condition`
 * carries a CHECK constraint (migrations 0107, 0109), so a new name needs a
 * migration on both sides or the row is REJECTED and the page silently never
 * lands. The three states are distinguished in `detail`, which is what the
 * on-call actually reads. Split it out later if the triage differs enough to
 * earn a migration.
 *
 * `starting` is the fourth new word and is NOT here on purpose: it is the normal
 * first minutes of every boot, when entrypoint has exec'd bootstrap.sh and
 * bootstrap has not yet exec'd the gateway. Before this change that window
 * reported `inert` and paged on every healthy boot.
 */
function supervisorConditions(row: FleetStatusRow): ConditionState[] {
  const state = row.gateway_supervisor_state
  if (state == null) return []
  const slug = row.customer_slug
  const inert = state === 'inert' || state === 'not-watching' || state === 'never-healthy'
  return [
    {
      customer_slug: slug,
      condition: 'gateway_supervisor_refusing',
      active: state === 'refusing',
      detail:
        state === 'refusing'
          ? 'The seat supervisor has spent its restart budget (3 per hour) and has STOPPED ' +
            'restarting the gateway. This seat is flapping on a cause a restart does not ' +
            'fix and needs a human now.'
          : `Supervisor state is "${state}".`,
    },
    {
      customer_slug: slug,
      condition: 'gateway_supervisor_inert',
      active: inert,
      detail: inert ? inertDetail(state) : `Supervisor state is "${state}".`,
    },
  ]
}

/**
 * Commitments contract gate for the pilot-smokeball seat (ADR 0075).
 *
 * The Ashton & Price 2026-07-09 letter makes per-routine autonomy commitments
 * (the routine-settings grid) and names permanent caps (deadline / money /
 * opposing counsel / court). Those commitments are authored across three
 * artifacts on this seat:
 *
 *   - operator/customers/pilot-smokeball/customer.yaml   (the live config)
 *   - operator/customers/pilot-smokeball/routine-grid.yaml (the traceability grid)
 *   - operator/customers/pilot-smokeball/commitments.json  (the pinned contract)
 *
 * This suite parses the LIVE customer.yaml + routine-grid.yaml and asserts the
 * five commitment invariants below. It is deliberately hermetic (no network):
 * everything it checks is a file in this repo.
 *
 * The tool-to-action-class map that pins payments_* / trust-ledger / create_matter
 * as COMMITMENT-class lives OVERLAY-side (shared/action_classes.py), outside this
 * repo, so there is no ss-side BANNED_TOOLS list. Test (e) asserts the ss-reachable
 * enforcement floor instead: trust_ceiling.enforce() never lets a COMMITMENT action
 * fire without explicit current-turn approval.
 */

import { describe, it, expect } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'fs'
import { resolve, join } from 'path'
import { parse as parseYaml } from 'yaml'
import { validate, type CustomerYaml } from '../src/lib/operator/customer-yaml'
import { validateRoutineGrid, type RoutineGridRow } from '../src/lib/operator/routine-grid'
import { isCeiling, restrictiveness } from '../src/lib/portal/operator/config-governance'

const SEAT_YAML_PATH = resolve('operator/customers/pilot-smokeball/customer.yaml')
const GRID_YAML_PATH = resolve('operator/customers/pilot-smokeball/routine-grid.yaml')
const COMMITMENTS_PATH = resolve('operator/customers/pilot-smokeball/commitments.json')
const TRUST_CEILING_PY = resolve('operator/adapter/trust_ceiling.py')
const AP_DIR = resolve('operator/customers/ashton-price')

const TIER_VOCAB = ['flag-only', 'prepare-and-route', 'auto-handle'] as const

/** Parse + validate the LIVE customer.yaml, throwing if it no longer validates. */
function seatValue(): CustomerYaml {
  const raw = parseYaml(readFileSync(SEAT_YAML_PATH, 'utf-8')) as Record<string, unknown>
  const result = validate(raw)
  if (!result.ok) {
    throw new Error(`customer.yaml no longer validates:\n${JSON.stringify(result.errors, null, 2)}`)
  }
  return result.value
}

/**
 * Parse + validate the LIVE routine-grid.yaml through the canonical parser
 * (src/lib/operator/routine-grid.ts), throwing if it no longer validates. The
 * parser owns the tier vocabulary + field shape; this suite asserts the
 * grid<->config drift invariants on top of the parsed rows.
 */
function gridRows(): RoutineGridRow[] {
  const raw = parseYaml(readFileSync(GRID_YAML_PATH, 'utf-8'))
  const result = validateRoutineGrid(raw)
  if (!result.ok) {
    throw new Error(
      `routine-grid.yaml no longer validates:\n${JSON.stringify(result.errors, null, 2)}`
    )
  }
  return result.value.rows
}

function commitments(): {
  invariants: {
    outbound_roster_classes_allowed: string[]
    tier_vocabulary: string[]
  }
  outbound_roster: Array<{ address: string; class: string }>
} {
  return JSON.parse(readFileSync(COMMITMENTS_PATH, 'utf-8'))
}

/** Parse + validate any seat's customer.yaml by path, throwing if it no longer validates. */
function validatedSeat(path: string): CustomerYaml {
  const result = validate(parseYaml(readFileSync(path, 'utf-8')))
  if (!result.ok) {
    throw new Error(`${path} no longer validates:\n${JSON.stringify(result.errors, null, 2)}`)
  }
  return result.value
}

/** Recursively list every file under `dir`. */
function walkFiles(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) out.push(...walkFiles(full))
    else out.push(full)
  }
  return out
}

describe('pilot-smokeball commitments contract (ADR 0075)', () => {
  it('the live customer.yaml still validates', () => {
    expect(() => seatValue()).not.toThrow()
  })

  it('the routine grid carries all 19 letter rows', () => {
    expect(gridRows()).toHaveLength(19)
  })

  // (a) The opposing-counsel / court permanent cap: no persona may EVER author
  // external_send: autonomous. draft_for_review is the seat's authored value.
  it('(a) external_send is never autonomous for any persona', () => {
    const seat = seatValue()
    for (const persona of seat.personas) {
      expect(
        persona.entitlements.exposure['external_send'],
        `persona ${persona.slug} must not author external_send: autonomous`
      ).not.toBe('autonomous')
    }
  })

  // (b) Every outbound_roster entry is a client / records_vendor, and the live
  // roster EXACTLY equals the pinned commitments.json roster (any roster change
  // forces a same-PR commitments bump = reviewed diff).
  it('(b) outbound_roster classes are {client, records_vendor} and match the pinned contract', () => {
    const seat = seatValue()
    const pinned = commitments()
    const allowed = new Set(pinned.invariants.outbound_roster_classes_allowed)

    for (const entry of seat.scope.outbound_roster) {
      expect(allowed.has(entry.class), `roster class "${entry.class}" not allowed`).toBe(true)
    }

    const norm = (list: Array<{ address: string; class: string }>) =>
      list.map((e) => `${e.address} ${e.class}`).sort()
    expect(norm(seat.scope.outbound_roster)).toEqual(norm(pinned.outbound_roster))
  })

  // (c) Grid <-> config traceability: every grid row's tier vocabulary is closed to
  // {flag-only, prepare-and-route, auto-handle}, its exposure-key values equal the
  // live customer.yaml values, and its skills reference real seat skills.
  it('(c) every grid row traces to the live config (tiers, exposure keys, skills)', () => {
    const seat = seatValue()
    const exposure = seat.personas.find((p) => p.slug === 'operator')?.entitlements.exposure
    expect(exposure, 'persona operator must exist').toBeTruthy()
    const seatSkills = new Set(seat.personas.flatMap((p) => p.skills.map((s) => s.name)))

    for (const row of gridRows()) {
      expect(TIER_VOCAB, `${row.routine} start_tier`).toContain(row.start_tier)
      expect(TIER_VOCAB, `${row.routine} ceiling_tier`).toContain(row.ceiling_tier)

      for (const [key, value] of Object.entries(row.enforcement.exposure_keys)) {
        expect(
          exposure![key as keyof typeof exposure],
          `${row.routine}: exposure_keys.${key} must match live customer.yaml`
        ).toBe(value)
      }

      for (const skill of row.skills) {
        expect(seatSkills.has(skill), `${row.routine}: skill "${skill}" not on the seat`).toBe(true)
      }
    }
  })

  // (d) Placeholder go-live gate: PLACEHOLDER markers are permitted on the
  // pilot-smokeball staging seat but must NEVER inherit to the real client seat.
  //
  // This walk used to cover the whole ashton-price directory. The engagement
  // documents moved to venturecrane/engagements, which now runs the same walk
  // over the material it holds; what remains here is the operational config,
  // and that is exactly where a PLACEHOLDER does the most damage, because it
  // reaches the running seat. Both halves of the gate are live, in the repo
  // that holds each half.
  it('(d) no PLACEHOLDER marker exists in the ashton-price operational config', () => {
    const files = walkFiles(AP_DIR)
    expect(files.length, 'ashton-price config must still be present to scan').toBeGreaterThan(0)

    const offenders = files.filter((f) => readFileSync(f, 'utf-8').includes('PLACEHOLDER'))
    expect(
      offenders,
      `PLACEHOLDER markers found under ashton-price (Christa's real numbers are a go-live gate):\n${offenders.join('\n')}`
    ).toEqual([])
  })

  // (e) Banned-tools sanity. There is no ss-side tool->class map (it lives overlay-
  // side in shared/action_classes.py), so we assert the ss-reachable enforcement
  // FLOOR: trust_ceiling.enforce() never lets a COMMITMENT action (which the overlay
  // classifies payments_* / trust-ledger / create_matter into) fire without an
  // explicit current-turn approval, and no persona authors commitment: autonomous.
  it('(e) COMMITMENT (fund movement) is never autonomous in the reachable enforcement floor', () => {
    const src = readFileSync(TRUST_CEILING_PY, 'utf-8')
    expect(src).toContain('COMMITMENT = "commitment"')
    expect(src).toContain('# COMMITMENT never autonomous')
    expect(src).toContain('commitment action requires explicit current-turn approval')

    const seat = seatValue()
    for (const persona of seat.personas) {
      expect(
        persona.entitlements.exposure['commitment'],
        `persona ${persona.slug} must not author commitment: autonomous`
      ).not.toBe('autonomous')
    }
  })

  // (f) Christa's confirmed settings (correspondence 09, 2026-07-23; #2005).
  // The diligence reply (10) states these as set — the ashton-price seat must
  // author exactly these values, and chase_cadence_days must stay UNAUTHORED
  // (the letter commits a cadence "you set per matter": a firm input at the
  // working session; authoring a guessed value here would be fabrication).
  it('(f) ashton-price authors the two confirmed settings and no invented cadence', () => {
    const raw = parseYaml(readFileSync(join(AP_DIR, 'customer.yaml'), 'utf-8')) as Record<
      string,
      unknown
    >
    const result = validate(raw)
    if (!result.ok) {
      throw new Error(
        `ashton-price customer.yaml no longer validates:\n${JSON.stringify(result.errors, null, 2)}`
      )
    }
    const skills = result.value.personas.flatMap((p) => p.skills)
    const byName = (name: string) => skills.find((s) => s.name === name)

    expect(
      byName('client-verification-tracker')?.settings?.['escalate_after_attempts'],
      'client-verification-tracker must author escalate_after_attempts: 3 (correspondence 09)'
    ).toBe(3)
    expect(
      byName('client-verification-tracker')?.settings?.['chase_cadence_days'],
      'chase_cadence_days must stay unauthored until the firm sets it (per-matter, letter 07)'
    ).toBeUndefined()
    expect(
      byName('medical-chronology-maintainer')?.settings?.['treatment_gap_flag_days'],
      'medical-chronology-maintainer must author treatment_gap_flag_days: 45 (correspondence 09)'
    ).toBe(45)
    // Agreement 2.8 / Exhibit A row 11 (engagements #89, 2026-08-28): the package
    // allowance is the ONE contract figure the seat authors for routine 11. The
    // per-matter gates, the per-job cap, and the behavioral defaults are SMD
    // runner posture and live in the runner's per-firm config, never here
    // (ADR 0087; a value authored here is agent-readable and world-readable).
    // Restated in PAGES (engagements #107, 2026-09-09): a document is not a
    // unit of work, and the cost of a package tracks its pages.
    expect(
      byName('medical-chronology-maintainer')?.settings?.[
        'chronology_package_page_allowance_per_month'
      ],
      'medical-chronology-maintainer must author the Exhibit A row 11 allowance: 15000 pages per month'
    ).toBe(15000)
    // The transitional document key is gone: both seats were reprovisioned
    // onto the page key on 2026-09-09 (vfy_01M23V6QKNRHN4SB1ZPRBWZ1V9), and a
    // broker that reads only the page key must never find the old one
    // authored beside it again.
    expect(
      byName('medical-chronology-maintainer')?.settings?.[
        'chronology_package_document_allowance_per_month'
      ],
      'the document allowance key was retired 2026-09-09; the allowance is in pages'
    ).toBeUndefined()
    for (const key of Object.keys(byName('medical-chronology-maintainer')?.settings ?? {})) {
      expect(
        ['treatment_gap_flag_days', 'chronology_package_page_allowance_per_month'],
        `medical-chronology-maintainer.settings.${key}: only contract-derived keys are authored on the client seat (ADR 0087)`
      ).toContain(key)
    }
  })

  // (h) A&P GRID TRACEABILITY. The (c) gate above checks the pilot seat
  // against the pilot grid; the CLIENT seat's own grid was checked by
  // nothing, and the gap it hid was real: ashton-price authored neither
  // external_send_client nor external_send_vendor, the two keys its grid
  // says enforce the letter's prepare-and-route tiers for client
  // verification (the firm's #1 named routine) and records chase.
  // resolve_ceiling does NO recipient-class fallback — unauthored is
  // REFUSED (ADR 0056), so those routines would have refused instead of
  // drafting. This gate makes the client seat's grid binding.
  it('(h) every ashton-price grid row traces to the ashton-price seat config', () => {
    const raw = parseYaml(readFileSync(join(AP_DIR, 'customer.yaml'), 'utf-8')) as Record<
      string,
      unknown
    >
    const seat = validate(raw)
    if (!seat.ok) {
      throw new Error(
        `ashton-price customer.yaml no longer validates:\n${JSON.stringify(seat.errors, null, 2)}`
      )
    }
    const gridResult = validateRoutineGrid(
      parseYaml(readFileSync(join(AP_DIR, 'routine-grid.yaml'), 'utf-8'))
    )
    if (!gridResult.ok) {
      throw new Error(
        `ashton-price routine-grid.yaml no longer validates:\n${JSON.stringify(gridResult.errors, null, 2)}`
      )
    }
    const exposure = seat.value.personas.find((p) => p.slug === gridResult.value.persona)
      ?.entitlements.exposure
    expect(exposure, `persona ${gridResult.value.persona} must exist on the seat`).toBeTruthy()
    const seatSkills = new Set(seat.value.personas.flatMap((p) => p.skills.map((s) => s.name)))

    // Direction matters. A grid-claimed key the seat does not author is a
    // DEFECT (unauthored = REFUSED, so the routine cannot do what the letter
    // says). A seat value MORE restrictive than the grid claims is the
    // client's own posture and is allowed — running tighter than committed is
    // always the firm's right (ADR 0035). Only absence, or a value LESS
    // restrictive than the grid claims, fails.
    for (const row of gridResult.value.rows) {
      for (const [key, claimed] of Object.entries(row.enforcement.exposure_keys)) {
        const authored = exposure![key as keyof typeof exposure] as string | undefined
        expect(
          authored,
          `${row.routine}: grid says ${key}=${claimed} enforces this row, but the seat authors no ${key} (unauthored = REFUSED, ADR 0056 — the routine would refuse instead of acting)`
        ).toBeTruthy()
        if (!authored || !isCeiling(authored) || !isCeiling(claimed)) continue
        expect(
          restrictiveness(authored) >= restrictiveness(claimed),
          `${row.routine}: seat authors ${key}=${authored}, LESS restrictive than the grid's ${claimed} — the seat exceeds what the letter committed`
        ).toBe(true)
      }
      for (const skill of row.skills) {
        expect(seatSkills.has(skill), `${row.routine}: skill "${skill}" not on the seat`).toBe(true)
      }
    }
  })

  // (i) EXPOSURE_CEILING DERIVATION (ss#2003 Q7 — the entitlement dial). The
  // seat's authored exposure_ceiling is the Machine-side clamp on portal-set
  // runtime overrides; it must equal the grid's own commitment: per send
  // class, the most autonomous TIER_SEND_CEILING across routines sharing the
  // class (auto-handle -> autonomous, prepare-and-route -> draft_for_review).
  // Too LOW a ceiling silently blocks a graduation the letter authorizes; too
  // HIGH permits a raise the letter never committed. Both directions fail.
  it('(i) ashton-price exposure_ceiling equals the grid-derived clamp per send class', () => {
    const seat = validate(
      parseYaml(readFileSync(join(AP_DIR, 'customer.yaml'), 'utf-8')) as Record<string, unknown>
    )
    if (!seat.ok) throw new Error('ashton-price customer.yaml no longer validates')
    const gridResult = validateRoutineGrid(
      parseYaml(readFileSync(join(AP_DIR, 'routine-grid.yaml'), 'utf-8'))
    )
    if (!gridResult.ok) throw new Error('ashton-price routine-grid.yaml no longer validates')
    const persona = seat.value.personas.find((p) => p.slug === gridResult.value.persona)
    const authoredCeiling = persona?.entitlements.exposure_ceiling
    expect(
      authoredCeiling,
      'the seat must author entitlements.exposure_ceiling (the entitlement dial has no bound without it)'
    ).toBeTruthy()

    const tierCeiling: Record<string, string | null> = {
      'flag-only': null,
      'prepare-and-route': 'draft_for_review',
      'auto-handle': 'autonomous',
    }
    const derived: Record<string, string> = {}
    for (const row of gridResult.value.rows) {
      const sendKeys = Object.keys(row.enforcement.exposure_keys).filter(
        (k) => k !== 'internal_write'
      )
      const ceiling = tierCeiling[row.ceiling_tier]
      if (sendKeys.length === 0 || ceiling === null || ceiling === undefined) continue
      const key = sendKeys[0]
      const prior = derived[key]
      if (
        prior === undefined ||
        (isCeiling(ceiling) &&
          isCeiling(prior) &&
          restrictiveness(ceiling) < restrictiveness(prior))
      ) {
        derived[key] = ceiling
      }
    }
    expect({ ...authoredCeiling }).toEqual(derived)
  })

  // (g) Case-alert routing. Two authored postures, both commitment-backed:
  //
  // BRING-UP (current, Captain 2026-08-09): central routing to exactly
  // Christa + Scott — the firm sees and judges real output from day one
  // (Christa), SMD sees exactly what she's seeing (Scott), and nothing reaches
  // any other firm staff until the firm says widen. This narrows, and is
  // consistent with, the 2026-07-29 call agreement that alerts start with
  // Chris + Christa only ("least disruptive").
  //
  // GRADUATION TARGET (#2004, correspondence 09, committed in 10): case-level
  // alerts route per matter to the assigned attorney/paralegal — never a
  // central firm inbox. When the firm says turn it up, mode flips back to
  // matter_staff and this test's pins move with it in the same PR.
  //
  // Constant across both: no invented fallback (who receives an unassigned
  // matter's alert is the firm's call; until authored, resolution failure
  // holds fail-closed), and external_send_internal authored autonomous so the
  // routed delivery is not refused at the gate.
  it('(g) ashton-price authors bring-up central routing (Christa + Scott), no invented fallback, and internal-send delivery', () => {
    const raw = parseYaml(readFileSync(join(AP_DIR, 'customer.yaml'), 'utf-8')) as Record<
      string,
      unknown
    >
    const result = validate(raw)
    if (!result.ok) {
      throw new Error(
        `ashton-price customer.yaml no longer validates:\n${JSON.stringify(result.errors, null, 2)}`
      )
    }
    const routing = result.value.escalation.case_alert_routing
    expect(
      routing?.mode,
      'case_alert_routing.mode must be central during bring-up (Captain 2026-08-09); matter_staff is the graduation target'
    ).toBe('central')
    // The client address is derived from the seat's own authored roster (the
    // staff-role user), never hardcoded here — the client-identity gate bans
    // the literal domain outside customer.yaml.
    const staffUser = result.value.users.find((u) => u.role === 'staff')
    expect(staffUser, 'the seat must author a staff-role user (Christa)').toBeTruthy()
    expect(
      result.value.escalation.red_flag_recipients,
      "bring-up central list is exactly the staff-role user + Scott (Captain 2026-08-09) — widening is the firm's call"
    ).toEqual([staffUser!.email, 'scott@smd.services'])
    expect(
      routing?.fallback_recipients,
      'fallback_recipients must stay unauthored until the firm names one (working-session input)'
    ).toEqual([])

    const operator = result.value.personas.find((p) => p.slug === 'operator')
    expect(
      operator?.entitlements.exposure['external_send_internal'],
      'external_send_internal must be authored autonomous — a routed alert that waits for SMD approval is not an alert'
    ).toBe('autonomous')
  })

  // (i) ADR 0083: the persona register is an AUTHORED artifact, and the proving
  // seat must carry the client seat's register verbatim. A rehearsal run against
  // a different persona proves nothing about the seat it rehearses for — the
  // same fixtures -> pilot-smokeball -> ashton-price discipline every other
  // authored value follows. This also catches the failure this gate was written
  // after: both seats carried three generic adjectives copied from each other,
  // describing no firm, no vertical, and no role.
  it('(j) both seats author an Operator-admin list, narrower than the roster (ADR 0085 §2)', () => {
    // Asserted structurally, never by literal address: client identities stay in
    // the authored config and out of tests (tests/client-identity-gate.test.ts).
    //
    // The client seat's admins are the two Named Administrators of letter 18;
    // the proving seat authors the rehearsal admin in the same shape. Three
    // invariants hold on both, and each is the property the ADR actually needs:
    //   - non-empty, or the seat is fail-closed and no establishment leg runs;
    //   - every admin is a person the seat already knows (a `users[]` entry),
    //     never a bare address the config mentions nowhere else;
    //   - strictly narrower than the roster, which is the whole point. The A&P
    //     roster is a domain-wide grant, so "rostered" cannot imply "admin"
    //     without the restriction being paper-only.
    const seats: Array<readonly [string, CustomerYaml]> = [
      ['ashton-price', validatedSeat(join(AP_DIR, 'customer.yaml'))],
      ['pilot-smokeball', seatValue()],
    ]

    for (const [label, cfg] of seats) {
      const admins = cfg.scope.admins
      expect(admins.length, `${label}: an unauthored admin list is fail-closed`).toBeGreaterThan(0)

      const knownPeople = new Set(cfg.users.map((u) => u.email.toLowerCase()))
      for (const admin of admins) {
        expect(admin.startsWith('@'), `${label}: an admin is a person, never a domain`).toBe(false)
        expect(knownPeople.has(admin), `${label}: admin ${admin} is not a authored user`).toBe(true)
      }

      // At least one rostered identity that is NOT an admin, or the refusal leg
      // (a rostered non-admin's identical instruction is declined) has no sender.
      const rosteredNonAdmins = cfg.scope.inbound_allow_from.filter((a) => !admins.includes(a))
      expect(
        rosteredNonAdmins.length,
        `${label}: admin authority must be narrower than the roster`
      ).toBeGreaterThan(0)
    }

    // The client seat's roster is a domain-wide grant; its admin list is not.
    // That contrast is the reason this key exists.
    //
    // Three admins, not two: letter 18's two Named Administrators (Chris,
    // Christa) plus the SMD operator, added PERMANENTLY by Captain decision
    // 2026-08-11 (the self-initiation decision set) so SMD can run initiation
    // and establishment on the firm's behalf. Disclosure of the third admin
    // to the firm is a tracked Captain follow-up (ss#2220 rescope).
    const [, ap] = seats[0]
    expect(ap.scope.inbound_allow_from.some((e) => e.startsWith('@'))).toBe(true)
    expect(ap.scope.admins.length).toBe(3)
  })

  // (k) ss-console#2546. The loop the admin list could not close on its own.
  // Every admin may apply a firm rule; this says whose inbox a NON-admin's
  // request lands in, so a partner is not paged every time a paralegal asks for
  // a different sign-off. Two invariants on both seats, and each is the property
  // the feature actually needs:
  //   - non-empty, or a non-admin's rule reaches nobody and the Operator has
  //     nothing true to say about who was asked;
  //   - a subset of the admin list, so a request can never be routed to somebody
  //     who could not act on it (the validator enforces this per-seat; asserted
  //     again here because the authored VALUES are what ship, not the schema).
  it('(k) both seats route rule requests to a subset of their admins (ss#2546)', () => {
    const seats: Array<readonly [string, CustomerYaml]> = [
      ['ashton-price', validatedSeat(join(AP_DIR, 'customer.yaml'))],
      ['pilot-smokeball', seatValue()],
    ]

    for (const [label, cfg] of seats) {
      const routing = cfg.scope.rule_requests_to
      expect(
        routing.length,
        `${label}: an unauthored routing list means a non-admin's rule reaches nobody`
      ).toBeGreaterThan(0)
      const admins = new Set(cfg.scope.admins)
      for (const to of routing) {
        expect(to.startsWith('@'), `${label}: a request goes to a person, never a domain`).toBe(
          false
        )
        expect(admins.has(to), `${label}: ${to} is routed a request it could not act on`).toBe(true)
      }
    }

    // The split is REAL on both seats, not decorative: each authors at least one
    // admin who is NOT paged. On A&P that is Chris by name (an admin who keeps
    // the authority and loses the traffic); on the proving seat it is scott@,
    // whose absence is what makes the "an admin not named receives nothing" leg
    // falsifiable there.
    for (const [label, cfg] of seats) {
      const unpaged = cfg.scope.admins.filter((a) => !cfg.scope.rule_requests_to.includes(a))
      expect(
        unpaged.length,
        `${label}: routing names every admin, so nothing proves the split is wired`
      ).toBeGreaterThan(0)
    }
  })

  // (l) ss-console#2546. An operations request (routine, schedule, channel,
  // memory, autonomy, on/off) is SMD's to make, and the Operator has to be able
  // to pass one on. That send needs a typed outbound class or it falls to the
  // outside external_send ceiling and sits as a held draft.
  //
  // The second half is the one worth a gate: the same address must NOT be on
  // the inbound roster. Rostering it would trust mail claiming to come FROM it,
  // which is a different and much larger grant than being able to write to it.
  it('(l) both seats can send to the SMD operations desk, and trust nothing from it', () => {
    const seats: Array<readonly [string, CustomerYaml]> = [
      ['ashton-price', validatedSeat(join(AP_DIR, 'customer.yaml'))],
      ['pilot-smokeball', seatValue()],
    ]
    const DESK = 'team@smd.services'

    for (const [label, cfg] of seats) {
      const entry = cfg.scope.outbound_roster.find((e) => e.address === DESK)
      expect(entry, `${label}: no outbound class for the operations desk`).toBeDefined()
      expect(entry!.class, `${label}: the desk is firm staff, not a client or a vendor`).toBe(
        'firm_staff'
      )
      expect(
        cfg.scope.inbound_allow_from.includes(DESK),
        `${label}: the operations desk must not be granted inbound trust`
      ).toBe(false)
    }
  })

  // (m) ss-console#2546, the operations half. `rule_requests_to` above routes a
  // firm rule to somebody who can APPLY it. This is the other direction: an
  // operations request (routine, schedule, channel, memory, autonomy, on/off) is
  // SMD's to make, so SMD is who ANSWERS it, and `ops_reply_from` is whose
  // answer counts.
  //
  // THE ASSERTION WORTH THE GATE is the last one, and it is (l)'s shape: being
  // able to answer a question the Operator asked must not become inbound trust.
  // A regression that quietly added team@ to inbound_allow_from to "make the
  // reply work" would pass every other assertion in this file, and would grant
  // any forged mail from that address the power to instruct the seat.
  //
  // It is pinned on the DESK specifically rather than on every entry, and the
  // reason is that scott@smd.services is on both seats' rosters and admin lists
  // already, under a separate and earlier Captain decision (the self-initiation
  // set, 2026-08-11 — see (j)). Asserting "no answering address is rostered"
  // would therefore fail on a grant this key did not make and cannot revoke.
  // team@ is the address this feature actually introduces, and it is the one
  // whose isolation this key could plausibly break.
  it('(m) both seats let SMD answer an operations request, and the desk stays untrusted', () => {
    const seats: Array<readonly [string, CustomerYaml]> = [
      ['ashton-price', validatedSeat(join(AP_DIR, 'customer.yaml'))],
      ['pilot-smokeball', seatValue()],
    ]
    const DESK = 'team@smd.services'
    const SMD_DOMAINS = ['smd.services', 'smdurgan.com']

    for (const [label, cfg] of seats) {
      const answering = cfg.scope.ops_reply_from
      expect(
        answering.length,
        `${label}: an unauthored answering list means every request lapses unanswered`
      ).toBeGreaterThan(0)

      // The desk is on it, because the desk is where the request is SENT and a
      // reply arrives from where it was sent. Without this the loop cannot close
      // at all: SMD would answer from an address the seat does not read.
      expect(answering, `${label}: the desk cannot answer its own mail`).toContain(DESK)

      for (const address of answering) {
        expect(address.startsWith('@'), `${label}: an answer comes from a person`).toBe(false)
        expect(
          SMD_DOMAINS.some((d) => address.endsWith(`@${d}`)),
          `${label}: ${address} is not SMD, and operations changes are SMD's`
        ).toBe(true)
      }

      expect(
        cfg.scope.inbound_allow_from.includes(DESK),
        `${label}: the desk may answer an operations request, never instruct the seat`
      ).toBe(false)
      expect(
        cfg.scope.admins.includes(DESK),
        `${label}: answering for SMD does not make the desk an administrator of the firm`
      ).toBe(false)
    }
  })

  it('(i) the authored persona register is real and identical across the client and proving seats', () => {
    const seats = [AP_DIR, resolve('operator/customers/pilot-smokeball')]
    const tones = seats.map((dir) => {
      const raw = parseYaml(readFileSync(join(dir, 'customer.yaml'), 'utf-8')) as Record<
        string,
        unknown
      >
      const result = validate(raw)
      if (!result.ok) throw new Error(`${dir} customer.yaml no longer validates`)
      const operator = result.value.personas.find((p) => p.slug === 'operator')
      expect(operator, `${dir}: operator persona must exist`).toBeDefined()
      return operator!.tone
    })

    expect(
      tones[0],
      'the client seat and the proving seat must carry the SAME authored register'
    ).toEqual(tones[1])

    // A register is instructions, not adjectives. The retired value averaged 11
    // characters per entry ("concise"); an authored rule states what to do.
    for (const entry of tones[0]) {
      expect(
        entry.length,
        `persona tone entry is too thin to be a register rule: ${JSON.stringify(entry)}`
      ).toBeGreaterThan(40)
    }
    expect(
      tones[0].some((t) => /never/i.test(t)),
      'an authored register names what the persona never does, not only what it is'
    ).toBe(true)
  })
})

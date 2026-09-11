/**
 * Client-language allowlist for Operator activity (portal IA rebuild,
 * Captain decision 7, 2026-07-07): clients see curated human-language
 * summaries only. Raw runtime vocabulary ("INVARIANT_VIOLATION",
 * "LLM_TURN_COMPLETED") never renders on a client surface.
 *
 * Every raw action string is either MAPPED (has authored client copy) or
 * SUPPRESSED (renders nothing, with a written reason). The exhaustiveness test in
 * activity-language.test.ts asserts that every member of
 * AUDIT_ACTION_TYPES appears in exactly one of the two sets, so a new
 * writer-side action forces a deliberate client-language decision at
 * merge time.
 *
 * THE THIRD STATE (ss#2316). An action string that is in NEITHER set is
 * UNDECLARED. It renders nothing, exactly like a suppressed one, which is the
 * hazard: "we decided the client should not see this" and "nobody added it" look
 * identical on the feed. Undeclared is therefore a defect state, not a design
 * state, and {@link activityDisposition} names it so callers and tests can tell
 * the three apart. It stays non-rendering because the alternative is inventing
 * client-facing copy, which the venture forbids outright.
 *
 * An earlier version of this comment cited LLM_TURN_COMPLETED as an example of a
 * type "beyond the enum" that the absent-key rule suppressed implicitly. Both
 * halves stopped being true on 2026-08-02: #2122 added it to AUDIT_ACTION_TYPES
 * and suppressed it explicitly below. As of ss#2316, every action type the
 * overlay runtime is known to emit is declared and dispositioned; the guard that
 * keeps it that way is tests/activity-language-producers.test.ts.
 *
 * Copy rules: authored template sentences describing SHIPPED system
 * behavior only; entry.skill / entry.target / entry.reason are real data
 * and may be interpolated; nothing is invented (anti-fabrication policy).
 * The raw SCREAMING_SNAKE vocabulary never reaches a client surface; this
 * module is the only client-facing renderer of an audit action.
 */

import type { AuditEntry } from './audit'

export interface ClientActivityCategory {
  /** Stable filter value used in URLs. */
  key: string
  /** Human label for the filter control. */
  label: string
  /** Raw action types this bucket absorbs. */
  actions: readonly string[]
}

export const CLIENT_ACTIVITY_CATEGORIES: readonly ClientActivityCategory[] = [
  {
    key: 'drafts',
    label: 'Drafts and replies',
    actions: [
      'DRAFT_CREATED',
      'DRAFT_APPROVED',
      'DRAFT_REJECTED',
      'DRAFT_EXPIRED',
      'REPLY_SENT',
      'REPLY_HELD',
      'REPLY_FAILED',
      'CONFIRM_SEND_DISPATCHED',
      'CONFIRM_SEND_FAILED',
    ],
  },
  {
    key: 'escalations',
    label: 'Escalations',
    actions: ['ESCALATION_FIRED', 'ESCALATION_ACKNOWLEDGED'],
  },
  {
    key: 'status',
    label: 'Operator status',
    actions: ['AGENT_STOPPED', 'AGENT_RESUMED'],
  },
  {
    key: 'access',
    label: 'Team and access',
    actions: ['PORTAL_LOGIN', 'TEAM_ROLE_GRANTED', 'TEAM_ROLE_REVOKED', 'TEAM_INVITE_SENT'],
  },
  {
    key: 'configuration',
    label: 'Configuration',
    actions: [
      'SKILL_ENABLED',
      'SKILL_DISABLED',
      'ROUTINE_ENABLED',
      'ROUTINE_DISABLED',
      'TRUST_PROMOTED',
      'TRUST_DEMOTED',
      'ENTITLEMENT_CHANGED',
      'SCOPE_CHANGED',
      'CONFIG_CHANGE_SUBMITTED',
      'CONFIG_CHANGE_REJECTED',
      'OUTPUT_SPEC_AUTHORED',
      'OUTPUT_SPEC_REJECTED',
      'CORRECTION_PROPOSED',
      'RULE_PROPOSED',
      'RULE_REQUEST_NOTIFIED',
      'RULE_DECLINED',
      'RULE_LAPSED',
      'ESTABLISHMENT_SUBMITTED',
      'ESTABLISHMENT_RESULT',
      'ACT_PROPOSED',
      'ACT_COMMITTED',
      'OPS_REQUEST_RECORDED',
      'OPS_REQUEST_RESOLVED',
      'OPS_REQUEST_LAPSED',
      'MEDCHRON_JOB_SUBMITTED',
      'MEDCHRON_JOB_RUNNING',
      'MEDCHRON_JOB_HELD',
      'MEDCHRON_JOB_DELIVERED',
      'MEDCHRON_JOB_FAILED',
    ],
  },
  {
    key: 'connections',
    label: 'Connections',
    actions: [
      'CONNECTOR_BOUND',
      'CONNECTOR_UNBOUND',
      'CONNECTOR_AUTH_EXPIRED',
      'CONNECTOR_AUTH_RESTORED',
      'CONNECTOR_RECONSENT_REQUESTED',
    ],
  },
  {
    key: 'compliance',
    label: 'Compliance',
    actions: ['COMPLIANCE_PACKET_EXPORTED', 'COMPLIANCE_RECORD_EXPORTED'],
  },
] as const

/**
 * Raw actions that deliberately render NOTHING on client surfaces, each with the
 * reason it is withheld (ss#2316).
 *
 * This was a bare `Set` of 43 strings under one blanket header. That made the
 * membership explicit but not the DECISION: for most entries there was no record
 * of why a client should not see it, so a deliberate withholding could not be
 * told apart from an oversight, and neither could be reviewed. Every entry now
 * carries a reason, and a test asserts none is empty.
 *
 * Reasons are engineering rationale for a reviewer, not client-facing copy.
 * Nothing here is rendered anywhere.
 *
 * Four grounds recur, and each entry names which one applies:
 *   TELEMETRY   volume-scaled machine bookkeeping; one row per tool call, turn,
 *               or route. Not an act the client asked for or would recognize.
 *   INSTRUMENT  a gate's own measurement. It fires on the ORDINARY case, so
 *               surfacing it reads as a near-miss on routine work. The honest
 *               client-facing event is the one where the gate actually held
 *               something, and that event is separately mapped.
 *   INTERNAL    our operations on our own infrastructure. Real, but ours.
 *   NEEDS COPY  plausibly client-visible, blocked only on authored wording.
 *               Tracked in ss#2320; these are the promotion candidates.
 */
export const SUPPRESSED_ACTION_REASONS: Readonly<Record<string, string>> = {
  // --- INSTRUMENT: safety-substrate gates measuring their own routine work ---
  INVARIANT_VIOLATION:
    'INSTRUMENT. Substrate invariant tripped, including every WARN/SOFT_STOP transition. Ours to act on, and a client cannot repair it.',
  INVARIANT_BOOT_CHECK_FAILED:
    'INSTRUMENT. Boot-time binding mismatch on the Machine. A provisioning fault, not an act taken on the client behalf.',
  FABRICATION_FILTER_TRIGGERED:
    'INSTRUMENT. The fabrication filter ran at skill-output time. The client-facing event is the draft that resulted, already mapped.',
  IDENTIFIER_UNVERIFIED:
    'INSTRUMENT. The identifier gate is report-only and non-blocking, so a row here means nothing changed for the client.',
  VOICE_GATE_TRIGGERED:
    'INSTRUMENT. Report-only voice-gate signal, fires on ordinary drafting turns.',
  VOICE_GATE_PASSED: 'INSTRUMENT. The gate passing is the unremarkable case, by design.',
  VOICE_GATE_NEAR_PASS:
    'INSTRUMENT. Internal scoring band used to tune the gate, not a client event.',
  VOICE_GATE_FAILED:
    'INSTRUMENT. A failed voice gate routes the work to a draft; that draft is the mapped, client-visible event.',
  SPEC_GATE_TRIGGERED:
    'INSTRUMENT. Output failed the client-authored format spec and was rerouted. The reroute surfaces through the draft, already mapped.',
  // ss#2167. Kept verbatim from the original annotation: the gate fires on the
  // ordinary case (the matter party list simply was not read that turn), so a
  // line here would read like a near-miss on most replies the firm sees. The
  // honest client-facing event is the one where we DID hold something, which is
  // REPLY_HELD. Promote only if the measurement shows these rows mean something
  // a client should act on.
  MATTER_UNRESOLVED:
    'INSTRUMENT. The outbound matter-identity gate could neither confirm nor deny party membership. Fires on the ordinary case; REPLY_HELD is the honest client-facing event.',

  // --- TELEMETRY: per-call, per-turn, per-route machine bookkeeping ---------
  TOOL_CALL_COMPLETED:
    'TELEMETRY. One row per tool invocation; the highest-volume type on both seats (about 69 percent of pilot rows, live-probed 2026-08-02).',
  LLM_TURN_COMPLETED: 'TELEMETRY. One row per completed agent turn. Machine bookkeeping.',
  WEBHOOK_ROUTED: 'TELEMETRY. One row per inbound webhook routed to a skill.',
  WEBHOOK_SUPPRESSED:
    'TELEMETRY. One row per suppressed inbound. Nothing reached the Operator, so nothing happened for the client to read.',
  BROKER_DECISION_ALLOWED:
    'TELEMETRY. Capability-broker decision row written before a mediated-connector grant is redeemed.',
  BROKER_EXECUTED: 'TELEMETRY. Capability-broker execution row paired with a signed receipt.',
  INBOUND_RECEIVED:
    'TELEMETRY. One row per untrusted inbound item as it lands in quarantine (ADR 0027). Arrival is not yet an act.',
  SUBAGENT_STOPPED: 'TELEMETRY. One row per child subagent completion (ADR 0021).',
  SUBAGENT_INCOMPLETE:
    'TELEMETRY. The parent skill refused to assemble an incomplete draft. Internal composition detail.',
  // #2253, the wake half of the cron gate. Both wake types are gate telemetry:
  // "the Operator scheduler decided to run" is not an act the client performed
  // or asked about.
  SUPPRESSED_WAKE: 'TELEMETRY. A gated cron declined to wake. Scheduler bookkeeping.',
  EMITTED_WAKE: 'TELEMETRY. A gated cron woke. Scheduler bookkeeping.',

  // --- INTERNAL: our operations on our own infrastructure -------------------
  MEMORY_RULE_ADDED:
    'INTERNAL. Memory rules live in the per-customer store on the Machine; the portal teach-a-rule producer was removed per ADR 0052.',
  MEMORY_RULE_EDITED: 'INTERNAL. Machine-side memory store edit; no client-initiated producer.',
  MEMORY_RULE_DELETED: 'INTERNAL. Machine-side memory store delete; no client-initiated producer.',
  CONNECTOR_TOKEN_REFRESHED:
    'INTERNAL. Routine Machine-local credential refresh. The client-visible connector events are bind, unbind, expiry and restore, all mapped.',
  CONNECTOR_HEALTH_PROBE_FAILED:
    'INTERNAL. A single probe failure is not an outage; sustained failure surfaces as CONNECTOR_AUTH_EXPIRED, which is mapped.',
  AGENT_SKILL_CREATED:
    'INTERNAL. Agent-authored skill created at runtime (ADR 0017). The client sees skills through the settings surface, not as feed events.',
  AGENT_SKILL_REMOVED: 'INTERNAL. Agent-authored skill removed at runtime (ADR 0017).',
  CUSTOMER_YAML_SYNCED:
    'INTERNAL. The config sidecar applied an R2-source customer.yaml change (ADR 0019). Client-initiated config changes surface as CONFIG_CHANGE_SUBMITTED, which is mapped.',
  CUSTOMER_YAML_STRUCTURAL_CHANGE_DEFERRED:
    'INTERNAL. The sidecar deferred a structural change for Captain re-provision (ADR 0019).',
  CONFIG_WRITE:
    'INTERNAL. One row per live customer.yaml apply. Digests carry provenance, never content.',
  HONCHO_CONCLUSION_DISMISSED:
    'INTERNAL. Captain dismissed a memory-mirror conclusion in the admin console (ADR 0016). An admin action on our tooling.',
  RBAC_EVENT:
    'INTERNAL. Access-control bookkeeping. Produced since ss#2429 by the overlay corrections plugin: a refusal row (subAction correction_capture_refused) when a non-admin message would have installed a standing correction. Suppressed from the client feed; the portal RBAC writer (rbac-audit.ts) remains gated on #821/#891.',
  DECOMMISSION_INITIATED: 'INTERNAL. Decommission pipeline boundary, run by us.',
  DECOMMISSION_DRAIN_COMPLETE: 'INTERNAL. Decommission pipeline boundary, run by us.',
  DECOMMISSION_STEP_BEGIN: 'INTERNAL. Per-step decommission marker for the compliance trail.',
  DECOMMISSION_STEP_COMPLETE: 'INTERNAL. Per-step decommission marker for the compliance trail.',
  DECOMMISSION_STEP_FAILED: 'INTERNAL. Per-step decommission marker for the compliance trail.',
  DECOMMISSION_FINAL: 'INTERNAL. Decommission pipeline boundary, run by us.',
}

/**
 * Raw actions that render nothing. Derived from {@link SUPPRESSED_ACTION_REASONS}
 * so membership and rationale cannot drift apart. Kept as a `Set` for callers.
 */
export const SUPPRESSED_ACTIONS: ReadonlySet<string> = new Set(
  Object.keys(SUPPRESSED_ACTION_REASONS)
)

type SummaryBuilder = (entry: AuditEntry) => string

const withSkill = (base: string) => (entry: AuditEntry) =>
  entry.skill ? `${base}: ${entry.skill}` : base

/**
 * Client-language names for the routine autonomy tiers (mirror of the settings
 * page's TIER_LABELS — the internal flag-only / prepare-and-route /
 * auto-handle vocabulary stays ours and never renders on a client surface).
 * Applied to the ENTITLEMENT_CHANGED target string, which the ledger stores
 * in internal vocabulary ("Client verification: prepare-and-route →
 * auto-handle"); any token without a mapping passes through unchanged.
 */
const CLIENT_TIER_LABELS: Record<string, string> = {
  'flag-only': 'Surfaces it for you',
  'prepare-and-route': 'Prepares it for someone to send',
  'auto-handle': 'Handles it end to end',
}

function clientTierPhrase(target: string): string {
  return target.replace(
    /flag-only|prepare-and-route|auto-handle/g,
    (tier) => CLIENT_TIER_LABELS[tier] ?? tier
  )
}

const CLIENT_LANGUAGE: Record<string, SummaryBuilder> = {
  DRAFT_CREATED: withSkill('Prepared a draft for your review'),
  DRAFT_APPROVED: () => 'A draft was approved and sent',
  DRAFT_REJECTED: () => 'A draft was declined',
  DRAFT_EXPIRED: () => 'A draft expired without review',
  REPLY_SENT: () => 'Replied to a message',
  REPLY_HELD: () => 'Held a reply for your review',
  // ss#2320, Captain-approved 2026-08-12. A client who sees REPLY_SENT and
  // REPLY_HELD but not REPLY_FAILED is shown a filtered version of their own
  // operation, and eventually notices a reply that never arrived with no record
  // of the attempt. Past tense, and deliberately no promise of a retry: the
  // system does not retry these, and copy implying it would is an uncontracted
  // outbound commitment (Pattern A).
  REPLY_FAILED: () => 'A reply could not be sent',
  CONFIRM_SEND_DISPATCHED: () => 'Sent a confirmed message',
  CONFIRM_SEND_FAILED: () => 'A confirmed message could not be sent',
  CORRECTION_PROPOSED: () => 'Captured your correction',
  // ADR 0085 / ss#2529. Three lines for three different things, because a
  // client reading their feed needs to tell them apart: a rule waiting on
  // someone, a rule that was agreed to, and the rule taking effect on the work.
  // Deliberately no timing promise on the middle one (Pattern A): "committed"
  // is a fact about the record, "applied" is the one that says the next
  // document of that kind is written to it.
  RULE_PROPOSED: () => 'Stated a rule back for confirmation',
  // ss#2546. Three more lines, for the three things that used to happen in
  // silence when the person who asked was not an administrator. Each is written
  // from the reader's side: what happened to the request they made, never which
  // verb ran. No timing promise on any of them (Pattern A).
  RULE_REQUEST_NOTIFIED: () => 'Asked an administrator to apply a rule',
  RULE_DECLINED: () => 'An administrator declined a rule',
  RULE_LAPSED: () => 'A rule request lapsed unanswered',
  ESTABLISHMENT_SUBMITTED: () => 'Committed a rule you confirmed',
  ESTABLISHMENT_RESULT: () => 'Applied a rule to how work is written',
  // ss#2536. Two lines for the two halves of an act, and the first one has to
  // read as a QUESTION: nothing happened, somebody was asked. The second says
  // the thing was done, which is a fact about the firm's own records and the
  // sentence a client should be able to find later.
  ACT_PROPOSED: () => 'Asked you to confirm something before doing it',
  ACT_COMMITTED: () => 'Did what you confirmed',
  // ss#2614 routine 11. Five lines for a chronology package's life on the
  // seat, in the reader's words: what was asked, that it is under way, that it
  // stopped and why, that it landed on the matter, or that it did not.
  MEDCHRON_JOB_SUBMITTED: () => 'Started a medical chronology package for a matter',
  MEDCHRON_JOB_RUNNING: () => 'Is building a medical chronology package',
  MEDCHRON_JOB_HELD: () => 'Paused a medical chronology package and surfaced why',
  MEDCHRON_JOB_DELIVERED: () => 'Filed a medical chronology package on the matter',
  MEDCHRON_JOB_FAILED: () => 'Could not finish a medical chronology package',
  // ss#2546 (the operations half). Three lines for a change the firm asked for
  // and SMD makes. Written from the reader's side, and the middle one stays
  // deliberately vague about WHICH answer: the outcome the client cares about
  // arrives in the email they get, and a feed line that said "declined" would
  // put a business decision on a row that carries no reason with it (Pattern A).
  OPS_REQUEST_RECORDED: () => 'Passed a setup request to SMD',
  OPS_REQUEST_RESOLVED: () => 'SMD answered a setup request',
  OPS_REQUEST_LAPSED: () => 'A setup request to SMD lapsed unanswered',
  ESCALATION_FIRED: (e) => e.reason ?? 'Flagged something for your attention',
  ESCALATION_ACKNOWLEDGED: () => 'An escalation was acknowledged',
  AGENT_STOPPED: () => 'Your operator was paused',
  AGENT_RESUMED: () => 'Your operator resumed work',
  ENTITLEMENT_CHANGED: (e) =>
    e.target
      ? `A routine's autonomy level was changed (${clientTierPhrase(e.target)})`
      : "A routine's autonomy level was changed",
  SKILL_ENABLED: withSkill('A skill was turned on'),
  SKILL_DISABLED: withSkill('A skill was turned off'),
  // #2498. Distinct from the two above in the client's terms too, not just
  // ours: a skill being on is permission, a routine being on is a schedule. A
  // Named Administrator reading a silent week needs to see which one changed,
  // and "no routines are scheduled" is the sentence the record could not say.
  ROUTINE_ENABLED: withSkill('A routine was scheduled'),
  ROUTINE_DISABLED: withSkill('A routine was unscheduled'),
  TRUST_PROMOTED: withSkill('An approval level was raised'),
  TRUST_DEMOTED: withSkill('An approval level was lowered'),
  CONNECTOR_BOUND: (e) => (e.target ? `Connected ${e.target}` : 'Connected a system'),
  CONNECTOR_UNBOUND: (e) => (e.target ? `Disconnected ${e.target}` : 'Disconnected a system'),
  CONNECTOR_AUTH_EXPIRED: () => 'A connection needs re-authorization',
  CONNECTOR_AUTH_RESTORED: () => 'A connection was restored',
  SCOPE_CHANGED: () => 'Working scope was updated',
  COMPLIANCE_PACKET_EXPORTED: () => 'A compliance export was produced',
  // Console-plane events (CONSOLE_ACTION_TYPES in audit.ts): sign-in history
  // and team/config actions recorded by the console itself, unioned into the
  // feed by activity-read.ts. Copy states only what durably happened; the
  // actor and target render in their own cells.
  PORTAL_LOGIN: () => 'Signed in to the client portal',
  TEAM_ROLE_GRANTED: () => 'A team member role was granted',
  TEAM_ROLE_REVOKED: () => 'A team member role was revoked',
  TEAM_INVITE_SENT: () => 'A team invitation was sent',
  CONFIG_CHANGE_SUBMITTED: () => 'An advanced configuration change was submitted',
  CONFIG_CHANGE_REJECTED: () => 'An advanced configuration change was not accepted',
  CONNECTOR_RECONSENT_REQUESTED: () => 'A connection re-authorization was requested',
  OUTPUT_SPEC_AUTHORED: () => 'An output spec was saved to the Operator',
  OUTPUT_SPEC_REJECTED: () => 'An output spec was not saved',
  COMPLIANCE_RECORD_EXPORTED: () => 'The audit record for a matter was downloaded',
}

/** Raw action strings with authored client copy. */
export const MAPPED_ACTIONS: readonly string[] = Object.keys(CLIENT_LANGUAGE)

/**
 * The action filter to push into the runtime read (SQL-side) so a chatty
 * agent's suppressed noise cannot starve client feeds to emptiness.
 */
export function mappedActionsForCategories(categoryKeys: readonly string[]): string[] {
  if (categoryKeys.length === 0) return [...MAPPED_ACTIONS]
  const wanted = new Set(categoryKeys)
  return CLIENT_ACTIVITY_CATEGORIES.filter((c) => wanted.has(c.key)).flatMap((c) => [...c.actions])
}

/** Client-language summary for one entry, or null when unmapped. */
export function clientSummaryFor(entry: AuditEntry): string | null {
  const build = CLIENT_LANGUAGE[entry.action]
  return build ? build(entry) : null
}

/** True when the entry has authored client language. */
export function isClientVisibleAction(action: string): boolean {
  return action in CLIENT_LANGUAGE
}

/**
 * How an action string is treated on client surfaces (ss#2316).
 *
 *   'mapped'     authored client copy exists; the line renders.
 *   'suppressed' deliberately withheld, with a reason in
 *                SUPPRESSED_ACTION_REASONS.
 *   'undeclared' NOT a design state. The action reached a client surface
 *                without anyone deciding what to do with it, so it renders as
 *                nothing indistinguishably from a suppressed one.
 */
export type ActivityDisposition = 'mapped' | 'suppressed' | 'undeclared'

export function activityDisposition(action: string): ActivityDisposition {
  if (action in CLIENT_LANGUAGE) return 'mapped'
  if (SUPPRESSED_ACTIONS.has(action)) return 'suppressed'
  return 'undeclared'
}

/**
 * Distinct undeclared action strings in a batch: emitted by the runtime, shown
 * to nobody, decided by no one. Exists so the state is observable instead of
 * being an absent-key accident. Callers may log or count it; the guard test
 * (tests/activity-language-producers.test.ts) asserts the set is empty for every
 * action type with a declared runtime producer.
 *
 * @public Producer guard. tests/activity-language-producers.test.ts imports it and asserts the
 * set is empty for every declared producer. No runtime caller, by design.
 */
export function undeclaredClientActions(entries: readonly AuditEntry[]): string[] {
  const seen = new Set<string>()
  for (const entry of entries) {
    if (activityDisposition(entry.action) === 'undeclared') seen.add(entry.action)
  }
  return [...seen].sort()
}

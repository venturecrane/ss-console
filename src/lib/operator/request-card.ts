/**
 * Operator request cards: the parse, the email, and the send ledger behind
 * POST /api/internal/operator-request-card (migration 0122).
 *
 * A seat posts one card per request a person emailed it: `replied` once the
 * first reply has settled, `no_reply` when nothing answered inside the alarm
 * window, `job_done` when a queued job the request started ends, and
 * `lane_unproven` the moment a request asks for a kind of job not proven on
 * that seat (the lane-proof gate, migration 0125; the job waits until SMD
 * proves the lane, runbook docs/runbooks/operator/lane-proof.md). ss-web turns
 * each card into one plain-text email to SMD ops. The wire contract is fixed
 * by the seat side (hermes-smd-overlay `shared/request_cards.py`); change it
 * in both repos or neither.
 *
 * ADR 0052 s5: the card's text fields (who, subject, reply_opening, matter)
 * reach the email and nothing else. `recordCard` takes the parsed card and
 * writes only its numbers, kinds and keys, and
 * tests/operator-request-card.test.ts asserts no text lands in D1.
 *
 * The body is signed by the seat, and is still read field by field: a body
 * that does not match the contract is a 400, never a partial card.
 */

export const CARD_KINDS = ['replied', 'no_reply', 'job_done', 'lane_unproven'] as const
export type CardKind = (typeof CARD_KINDS)[number]
export const JOB_LANES = ['demand', 'drafting', 'litigation', 'medchron', 'negotiation'] as const
export const JOB_STATES = ['delivered', 'failed', 'held'] as const

// `<sha256 of the request>:<kind>`. A job_done key may carry its job ENDING
// (`:<state>-<attempt>`): a resumed job ends again, and that ending is its own
// card under the same request. The request hash prefix is what groups them.
// A lane_unproven key carries its lane key: one card per request per lane (a
// scheduled job's hash is of its job id; an in-turn first use's, of its lane).
const CARD_KEY_RE =
  /^[0-9a-f]{64}:(?:(replied|no_reply)|(job_done)(?::(?:delivered|failed|held)-[1-9]\d{0,3})?|(lane_unproven):([a-z0-9][a-z0-9-]{0,63}(?:\.[a-z0-9_]{1,40})?))$/
const TOKEN_RE = /^[a-z0-9_:.-]{1,64}$/
/** A lane key: the skill that queues the job, plus the drafting class. */
const LANE_KEY_RE = /^[a-z0-9][a-z0-9-]{0,63}(?:\.[a-z0-9_]{1,40})?$/
const JOB_ID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/
const LANE_WHY = ['never_proven', 'revoked', 'unreadable'] as const
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/

const CAP_WHO = 160
const CAP_SUBJECT = 200
const CAP_OPENING = 300
const CAP_MATTER = 60
const MAX_TOOLS = 40
/** A year of minutes; anything larger is a seat clock bug, not a wait. */
const MAX_MINUTES = 525_600
const MAX_COUNT = 100_000

export interface CardJob {
  lane: (typeof JOB_LANES)[number]
  state: (typeof JOB_STATES)[number]
  reason: string | null
}

/**
 * The lane a lane_unproven card is about. `heldJob` is the queued job that
 * waits for the proof (null for an in-turn ask, which is paged, never held).
 */
export interface CardLane {
  key: string
  jobLane: (typeof JOB_LANES)[number] | null
  heldJob: string | null
  why: (typeof LANE_WHY)[number]
}

export interface RequestCard {
  cardKey: string
  kind: CardKind
  receivedAt: string
  eventAt: string
  minutes: number
  who: string
  subject: string
  replyOpening: string | null
  matter: string | null
  tools: string[]
  replies: number
  refused: number
  failed: number
  job: CardJob | null
  lane: CardLane | null
}

export type ParseResult = { ok: true; card: RequestCard } | { ok: false; field: string }

/** C0/C1 controls, DEL, and the bidi overrides that can reorder a subject line. */
const UNSAFE_CHARS_RE = new RegExp(
  // eslint-disable-next-line no-control-regex -- removing control characters is the point
  '[\\u0000-\\u001f\\u007f-\\u009f\\u200e\\u200f\\u202a-\\u202e\\u2066-\\u2069]',
  'g'
)

/**
 * Display text from the seat: controls replaced by spaces (so a header can
 * never carry a line break), whitespace collapsed, capped. Over-length text
 * is truncated, not refused: the cap protects the email, and refusing would
 * cost SMD the alert over a long subject line.
 */
export function cleanText(value: string, cap: number): string {
  const flat = value.replace(UNSAFE_CHARS_RE, ' ').replace(/\s+/g, ' ').trim()
  return flat.length > cap ? flat.slice(0, cap) : flat
}

function isCount(v: unknown, max: number): v is number {
  return typeof v === 'number' && Number.isInteger(v) && v >= 0 && v <= max
}

function parseIso(v: unknown): string | null {
  if (typeof v !== 'string' || !ISO_RE.test(v)) return null
  const ms = Date.parse(v)
  return Number.isNaN(ms) ? null : new Date(ms).toISOString()
}

function parseOptionalText(v: unknown, cap: number): string | null | undefined {
  if (v === null || v === undefined) return null
  if (typeof v !== 'string') return undefined
  const text = cleanText(v, cap)
  return text.length > 0 ? text : null
}

function parseTools(v: unknown): string[] | null {
  if (!Array.isArray(v) || v.length > MAX_TOOLS) return null
  const out: string[] = []
  for (const t of v) {
    if (typeof t !== 'string' || !TOKEN_RE.test(t)) return null
    if (!out.includes(t)) out.push(t)
  }
  return out
}

function parseJob(v: unknown): CardJob | null | undefined {
  if (v === null || v === undefined) return null
  if (typeof v !== 'object' || Array.isArray(v)) return undefined
  const job = v as Record<string, unknown>
  const lane = JOB_LANES.find((l) => l === job.lane)
  const state = JOB_STATES.find((s) => s === job.state)
  if (!lane || !state) return undefined
  const reason = job.reason ?? null
  if (reason !== null && (typeof reason !== 'string' || !TOKEN_RE.test(reason))) return undefined
  return { lane, state, reason }
}

/** null for none, the lane for a known one, undefined for anything else. */
function parseJobLane(v: unknown): CardLane['jobLane'] | undefined {
  return v === null || v === undefined ? null : JOB_LANES.find((l) => l === v)
}

function parseHeldJob(v: unknown): string | null | undefined {
  if (v === null || v === undefined) return null
  return typeof v === 'string' && JOB_ID_RE.test(v) ? v : undefined
}

function parseLane(v: unknown, keyLane: string | undefined): CardLane | null | undefined {
  if (v === null || v === undefined) return null
  if (typeof v !== 'object' || Array.isArray(v)) return undefined
  const lane = v as Record<string, unknown>
  if (typeof lane.key !== 'string' || !LANE_KEY_RE.test(lane.key) || lane.key !== keyLane)
    return undefined
  const why = LANE_WHY.find((w) => w === lane.why)
  const jobLane = parseJobLane(lane.job_lane)
  const heldJob = parseHeldJob(lane.held_job)
  if (!why || jobLane === undefined || heldJob === undefined) return undefined
  // A held job belongs to a job lane; an in-turn first use has neither.
  if ((heldJob === null) !== (jobLane === null)) return undefined
  return { key: lane.key, jobLane, heldJob, why }
}

interface Identity {
  cardKey: string
  kind: CardKind
  laneKey?: string
  receivedAt: string
  eventAt: string
  minutes: number
}

function parseIdentity(body: Record<string, unknown>): Identity | { field: string } {
  const kind = CARD_KINDS.find((k) => k === body.kind)
  if (!kind) return { field: 'kind' }
  const key = body.card_key
  if (typeof key !== 'string') return { field: 'card_key' }
  const keyMatch = CARD_KEY_RE.exec(key)
  if (!keyMatch || (keyMatch[1] ?? keyMatch[2] ?? keyMatch[3]) !== kind) {
    return { field: 'card_key' }
  }
  const laneKey = keyMatch[4]
  const receivedAt = parseIso(body.received_at)
  if (!receivedAt) return { field: 'received_at' }
  const eventAt = parseIso(body.event_at)
  if (!eventAt) return { field: 'event_at' }
  if (!isCount(body.minutes, MAX_MINUTES)) return { field: 'minutes' }
  return { cardKey: key, kind, receivedAt, eventAt, minutes: body.minutes, laneKey }
}

interface Content {
  who: string
  subject: string
  replyOpening: string | null
  matter: string | null
}

function parseContent(body: Record<string, unknown>): Content | { field: string } {
  if (typeof body.who !== 'string') return { field: 'who' }
  const who = cleanText(body.who, CAP_WHO)
  if (who.length === 0) return { field: 'who' }
  if (typeof body.subject !== 'string') return { field: 'subject' }
  const replyOpening = parseOptionalText(body.reply_opening, CAP_OPENING)
  if (replyOpening === undefined) return { field: 'reply_opening' }
  const matter = parseOptionalText(body.matter, CAP_MATTER)
  if (matter === undefined) return { field: 'matter' }
  return { who, subject: cleanText(body.subject, CAP_SUBJECT), replyOpening, matter }
}

/** Parse a card body against the wire contract. Any deviation names its field. */
export function parseRequestCard(body: Record<string, unknown>): ParseResult {
  const id = parseIdentity(body)
  if ('field' in id) return { ok: false, field: id.field }
  const content = parseContent(body)
  if ('field' in content) return { ok: false, field: content.field }
  const tools = parseTools(body.tools)
  if (!tools) return { ok: false, field: 'tools' }
  for (const f of ['replies', 'refused', 'failed'] as const) {
    if (!isCount(body[f], MAX_COUNT)) return { ok: false, field: f }
  }
  const job = parseJob(body.job)
  // A job card carries its job; a reply or alarm card carries none.
  if (job === undefined || (id.kind === 'job_done') !== (job !== null)) {
    return { ok: false, field: 'job' }
  }
  // A lane card carries its lane, the same lane its key names; no other does.
  const lane = parseLane(body.lane, id.laneKey)
  if (lane === undefined || (id.kind === 'lane_unproven') !== (lane !== null)) {
    return { ok: false, field: 'lane' }
  }
  const { laneKey: _laneKey, ...identity } = id
  return {
    ok: true,
    card: {
      ...identity,
      ...content,
      tools,
      replies: body.replies as number,
      refused: body.refused as number,
      failed: body.failed as number,
      job,
      lane,
    },
  }
}

// ---------------------------------------------------------------------------
// The email
// ---------------------------------------------------------------------------

const SUBJECT_EXCERPT = 70

function subjectExcerpt(subject: string): string {
  if (subject.length === 0) return '(no subject)'
  return subject.length > SUBJECT_EXCERPT ? `${subject.slice(0, SUBJECT_EXCERPT - 3)}...` : subject
}

export function cardSubject(slug: string, card: RequestCard): string {
  const s = subjectExcerpt(card.subject)
  switch (card.kind) {
    case 'replied':
      return `[SMD Ops] ${slug} request: first reply in ${card.minutes}m: ${s}`
    case 'no_reply':
      return `[SMD Ops] ${slug} NO REPLY after ${card.minutes}m: ${s}`
    case 'job_done':
      return `[SMD Ops] ${slug} ${card.job?.lane ?? 'job'} job ${card.job?.state ?? 'ended'}: ${s}`
    case 'lane_unproven':
      return card.lane?.heldJob
        ? `[SMD Ops] ${slug} NEW KIND OF ASK, job waiting: ${card.lane.key}: ${s}`
        : `[SMD Ops] ${slug} NEW KIND OF ASK, first use: ${card.lane?.key ?? 'unknown'}: ${s}`
  }
}

const LANE_WHY_TEXT: Record<(typeof LANE_WHY)[number], string> = {
  never_proven: 'it has never been proven on this seat',
  revoked: 'its proof was revoked',
  unreadable: "the seat could not read its proven-lane record (see the seat's lane-proof status)",
}

function laneLines(slug: string, card: RequestCard): string[] {
  const lane = card.lane
  if (!lane) return []
  const head = `Lane: ${lane.key} is not proven on this seat: ${LANE_WHY_TEXT[lane.why]}.`
  if (!lane.heldJob) {
    return [
      head,
      'An in-turn ask: the Operator answered it in the turn (nothing is held). Read that answer;',
      "once it is right, add the skill to the seat's lane-proofs.yaml with its evidence.",
    ]
  }
  return [
    head,
    `The ${lane.jobLane} job ${lane.heldJob} is QUEUED and will not run until the lane is proven.`,
    'The requester has the acknowledgement the Operator already sent; nothing more goes out.',
    `Prove it: operator/bin/lane-proof.sh ${slug} dry-run ${lane.heldJob} --wait 3600`,
    'Runbook: docs/runbooks/operator/lane-proof.md',
  ]
}

function outcomeLines(card: RequestCard): string[] {
  switch (card.kind) {
    case 'replied':
      return [`First reply: ${card.eventAt} (${card.minutes} min after the request)`]
    case 'no_reply':
      return [
        `NO REPLY: nothing has answered this request after ${card.minutes} min (checked ${card.eventAt}).`,
        'No reply went out, no queued job claims it, and the seat is not holding a reply for it.',
      ]
    case 'job_done': {
      const job = card.job
      const reason = job?.reason ? ` (${job.reason})` : ''
      return [
        `Job: ${job?.lane ?? 'unknown'} ${job?.state ?? 'ended'}${reason} at ${card.eventAt}, ${card.minutes} min after the request`,
      ]
    }
    case 'lane_unproven':
      return [`Noticed: ${card.eventAt} (${card.minutes} min after the request)`]
  }
}

/** The plain-text body. SMD ops only; this text never reaches a client or D1. */
export function cardText(slug: string, card: RequestCard): string {
  const lines = [
    `Seat: ${slug}`,
    `From: ${card.who}`,
    `Subject: ${card.subject.length > 0 ? card.subject : '(no subject)'}`,
    `Received: ${card.receivedAt}`,
    ...outcomeLines(card),
    ...laneLines(slug, card),
    // Only when the seat named one. "Matter: none named" is law-firm vocabulary
    // and read as noise on every non-legal seat (Brokery, 2026-10-16).
    ...(card.matter ? [`Matter: ${card.matter}`] : []),
  ]
  if (card.replyOpening) lines.push('', 'Reply opening:', `  ${card.replyOpening}`)
  lines.push(
    '',
    `Tools run (${card.tools.length}): ${card.tools.length > 0 ? card.tools.join(', ') : 'none'}`,
    `Replies sent: ${card.replies}. Refused: ${card.refused}. Failed: ${card.failed}.`,
    '',
    `Card: ${card.cardKey}`,
    'List recent requests from a terminal: .claude/bin/requests',
    ''
  )
  return lines.join('\n')
}

export interface CardMailEnv {
  RESEND_API_KEY?: string
  ALERT_TO_EMAIL?: string
  ALERT_FROM_EMAIL?: string
}

const DEFAULT_TO = 'team@smd.services'
const DEFAULT_FROM = 'SMD Services Ops <team@smd.services>'

/**
 * Send one card. `Idempotency-Key` is `<slug>:<card_key>`, so two racing
 * retries of the same card are one email at Resend. No key configured is a
 * failure, not a dev-mode success: a card reported sent that never left would
 * be the silent loss this table exists to prevent.
 */
export async function sendCardEmail(
  mailEnv: CardMailEnv,
  slug: string,
  card: RequestCard
): Promise<{ ok: boolean; detail?: string }> {
  if (!mailEnv.RESEND_API_KEY) return { ok: false, detail: 'RESEND_API_KEY unset' }
  try {
    const resp = await fetch('https://api.resend.com/emails', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${mailEnv.RESEND_API_KEY}`,
        'Content-Type': 'application/json',
        'Idempotency-Key': `${slug}:${card.cardKey}`,
      },
      body: JSON.stringify({
        from: mailEnv.ALERT_FROM_EMAIL || DEFAULT_FROM,
        to: [mailEnv.ALERT_TO_EMAIL || DEFAULT_TO],
        subject: cardSubject(slug, card),
        text: cardText(slug, card),
      }),
    })
    if (!resp.ok) return { ok: false, detail: `resend ${resp.status}` }
    return { ok: true }
  } catch (err) {
    return { ok: false, detail: err instanceof Error ? err.message : String(err) }
  }
}

// ---------------------------------------------------------------------------
// The send ledger (operator_request_cards). Numbers and keys only.
// ---------------------------------------------------------------------------

/**
 * Record the card if new and report whether it has already been sent. Insert
 * only: the first post's numbers stand, a re-post changes nothing.
 */
export async function recordCard(
  db: D1Database,
  slug: string,
  card: RequestCard
): Promise<{ alreadySent: boolean }> {
  await db
    .prepare(
      `INSERT INTO operator_request_cards
         (customer_slug, card_key, kind, received_at, event_at, minutes, tools_count,
          replies, refused, failed, job_lane, job_state, lane_key)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT (customer_slug, card_key) DO NOTHING`
    )
    .bind(
      slug,
      card.cardKey,
      card.kind,
      card.receivedAt,
      card.eventAt,
      card.minutes,
      card.tools.length,
      card.replies,
      card.refused,
      card.failed,
      card.job?.lane ?? card.lane?.jobLane ?? null,
      card.job?.state ?? null,
      card.lane?.key ?? null
    )
    .run()
  const row = await db
    .prepare('SELECT status FROM operator_request_cards WHERE customer_slug = ? AND card_key = ?')
    .bind(slug, card.cardKey)
    .first<{ status: string }>()
  return { alreadySent: row?.status === 'sent' }
}

export async function markCardAttempt(
  db: D1Database,
  slug: string,
  cardKey: string,
  sent: boolean
): Promise<void> {
  await db
    .prepare(
      sent
        ? `UPDATE operator_request_cards
              SET status = 'sent', sent_at = datetime('now'), attempts = attempts + 1
            WHERE customer_slug = ? AND card_key = ?`
        : `UPDATE operator_request_cards SET attempts = attempts + 1
            WHERE customer_slug = ? AND card_key = ?`
    )
    .bind(slug, cardKey)
    .run()
}

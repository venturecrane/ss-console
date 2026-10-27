/**
 * Operator request cards: the parse, the email, and the send ledger behind
 * POST /api/internal/operator-request-card (migration 0122).
 *
 * A seat posts one card per request a person emailed it: `replied` once the
 * first reply has settled, `no_reply` when nothing answered inside the alarm
 * window, `job_done` when a queued job the request started ends, and
 * `lane_unproven` the moment a request asks for a kind of job not proven on
 * that seat (the lane-proof gate, migration 0125; the job waits until SMD
 * proves the lane, runbook docs/runbooks/operator/lane-proof.md), and
 * `held_reply` when the requester writes again in the thread of a request
 * whose job is HELD (migration 0136): the firm's answer to the one question a
 * hold can ask it, or a chase, and in either case SMD's to act on, because the
 * Operator cannot record a decision on a held job. That card carries the
 * requester's own opening words (`request_opening`) and the held job's id.
 * `followup` is every reply SENT that no `replied` card counted: a later reply
 * in the same request, or one answering a message the seat has no request for
 * (migration 0141, 2026-10-26: an Operator corrected itself in a second reply
 * 50 minutes after its first answer, and the log said nothing).
 * ss-web turns each card into one plain-text email to SMD ops (rendered and
 * sent by `request-card-email.ts`). The wire contract is fixed
 * by the seat side (hermes-smd-overlay `shared/request_cards.py`); change it
 * in both repos or neither.
 *
 * ADR 0052 s5: the card's text fields (who, who_name, subject, request_text,
 * reply_opening, reply_text, matter) reach the email and nothing else. `recordCard` takes the parsed card and
 * writes only its numbers, kinds and keys, and
 * tests/operator-request-card.test.ts asserts no text lands in D1.
 *
 * The body is signed by the seat, and is still read field by field: a body
 * that does not match the contract is a 400, never a partial card.
 */

export const CARD_KINDS = [
  'replied',
  'no_reply',
  'job_done',
  'lane_unproven',
  'held_reply',
  'followup',
] as const
export type CardKind = (typeof CARD_KINDS)[number]
export const JOB_LANES = [
  'demand',
  'drafting',
  'litigation',
  'medchron',
  'negotiation',
  'casefile',
] as const
export const JOB_STATES = ['delivered', 'failed', 'held'] as const

// `<sha256 of the request>:<kind>`. A job_done key may carry its job ENDING
// (`:<state>-<attempt>`): a resumed job ends again, and that ending is its own
// card under the same request. The request hash prefix is what groups them.
// A lane_unproven key carries its lane key: one card per request per lane (a
// scheduled job's hash is of its job id; an in-turn first use's, of its lane).
// A held_reply key hashes the FOLLOW-UP message, so each message the requester
// writes on a held job is its own card. A followup key is the request hash plus
// a short hash of the reply SENT: each later reply is its own card, grouped
// under the request it answers.
const CARD_KEY_RE =
  /^[0-9a-f]{64}:(?:(replied|no_reply|held_reply)|(job_done)(?::(?:delivered|failed|held)-[1-9]\d{0,3})?|(lane_unproven):([a-z0-9][a-z0-9-]{0,63}(?:\.[a-z0-9_]{1,40})?)|(followup)-[0-9a-f]{16})$/
const TOKEN_RE = /^[a-z0-9_:.-]{1,64}$/
/** A lane key: the skill that queues the job, plus the drafting class. */
const LANE_KEY_RE = /^[a-z0-9][a-z0-9-]{0,63}(?:\.[a-z0-9_]{1,40})?$/
const JOB_ID_RE = /^[0-9A-HJKMNP-TV-Z]{26}$/
export const LANE_WHY = ['never_proven', 'revoked', 'unreadable'] as const
const ISO_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})$/

const CAP_WHO = 160
const CAP_SUBJECT = 200
const CAP_OPENING = 300
const CAP_MATTER = 60
/** The seat's request_index.TEXT_CHARS plus its ellipsis. */
const CAP_TEXT = 4003
const CAP_NAME = 120
const MAX_TOOLS = 40
/** A year of minutes; anything larger is a seat clock bug, not a wait. */
const MAX_MINUTES = 525_600
const MAX_COUNT = 100_000

export interface CardJob {
  lane: (typeof JOB_LANES)[number]
  state: (typeof JOB_STATES)[number]
  reason: string | null
  /** The job's id (a ULID), when the seat named it; a held_reply names it so SMD can act. */
  id: string | null
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

/**
 * The trust gate's action classes (overlay `shared/action_classes.py`), plus
 * `unmapped` for a tool the static map does not name. What the email groups
 * the Operator's work by: what it looked up apart from what it changed or sent.
 */
export const ACTION_CLASSES = [
  'read',
  'internal_write',
  'external_send',
  'external_send_internal',
  'external_send_client',
  'external_send_vendor',
  'external_send_as_staff',
  'commitment',
  'destructive',
  'code_execution',
  'refused',
  'unmapped',
] as const
export type ActionClassName = (typeof ACTION_CLASSES)[number]

export interface CardAction {
  tool: string
  actionClass: ActionClassName
  count: number
}

export interface RequestCard {
  cardKey: string
  kind: CardKind
  receivedAt: string
  eventAt: string
  minutes: number
  who: string
  /** The firm's authored name for `who` (users[].full_name); null when none is authored. */
  whoName: string | null
  subject: string
  /** The requester's own words in full, as the seat indexed them. */
  requestText: string | null
  replyOpening: string | null
  /** The reply in full; the email prefers it to the opening. */
  replyText: string | null
  /** The requester's own opening words; a held_reply card's reason to exist. */
  requestOpening: string | null
  matter: string | null
  tools: string[]
  /** Each tool with its action class and call count; null from a seat that sends none. */
  actions: CardAction[] | null
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

/**
 * A block of message text: the same unsafe characters removed, but line breaks
 * kept (runs of blank lines collapse to one), so a request or reply reads as
 * it was written. Capped like cleanText.
 */
export function cleanBlock(value: string, cap: number): string {
  const lines = value
    .replace(/\r\n?/g, '\n')
    .split('\n')
    .map((line) => line.replace(UNSAFE_CHARS_RE, ' ').replace(/\s+/g, ' ').trim())
  const kept: string[] = []
  for (const line of lines) {
    if (line.length > 0 || (kept.length > 0 && kept[kept.length - 1] !== '')) kept.push(line)
  }
  const flat = kept.join('\n').trim()
  return flat.length > cap ? flat.slice(0, cap) : flat
}

function parseOptionalBlock(v: unknown, cap: number): string | null | undefined {
  if (v === null || v === undefined) return null
  if (typeof v !== 'string') return undefined
  const text = cleanBlock(v, cap)
  return text.length > 0 ? text : null
}

/** null when the seat sent none (an older image), undefined for a malformed list. */
function parseActions(v: unknown): CardAction[] | null | undefined {
  if (v === null || v === undefined) return null
  if (!Array.isArray(v) || v.length > MAX_TOOLS) return undefined
  const out: CardAction[] = []
  for (const item of v) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) return undefined
    const a = item as Record<string, unknown>
    if (typeof a.tool !== 'string' || !TOKEN_RE.test(a.tool)) return undefined
    const actionClass = ACTION_CLASSES.find((c) => c === a.class)
    if (!actionClass || !isCount(a.count, MAX_COUNT) || a.count === 0) return undefined
    out.push({ tool: a.tool, actionClass, count: a.count })
  }
  return out
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
  const id = job.id ?? null
  if (id !== null && (typeof id !== 'string' || !JOB_ID_RE.test(id))) return undefined
  return { lane, state, reason, id }
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
  if (!keyMatch || (keyMatch[1] ?? keyMatch[2] ?? keyMatch[3] ?? keyMatch[5]) !== kind) {
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
  whoName: string | null
  subject: string
  requestText: string | null
  replyOpening: string | null
  replyText: string | null
  requestOpening: string | null
  matter: string | null
}

function parseContent(body: Record<string, unknown>): Content | { field: string } {
  if (typeof body.who !== 'string') return { field: 'who' }
  const who = cleanText(body.who, CAP_WHO)
  if (who.length === 0) return { field: 'who' }
  if (typeof body.subject !== 'string') return { field: 'subject' }
  const replyOpening = parseOptionalText(body.reply_opening, CAP_OPENING)
  if (replyOpening === undefined) return { field: 'reply_opening' }
  // Absent on every card a seat posted before 0136; null and absent read alike.
  const requestOpening = parseOptionalText(body.request_opening, CAP_OPENING)
  if (requestOpening === undefined) return { field: 'request_opening' }
  const matter = parseOptionalText(body.matter, CAP_MATTER)
  if (matter === undefined) return { field: 'matter' }
  // The three below are absent on every card a seat posted before 2026-10-27;
  // null and absent read alike, and the email falls back to the openings.
  const whoName = parseOptionalText(body.who_name, CAP_NAME)
  if (whoName === undefined) return { field: 'who_name' }
  const requestText = parseOptionalBlock(body.request_text, CAP_TEXT)
  if (requestText === undefined) return { field: 'request_text' }
  const replyText = parseOptionalBlock(body.reply_text, CAP_TEXT)
  if (replyText === undefined) return { field: 'reply_text' }
  return {
    who,
    whoName,
    subject: cleanText(body.subject, CAP_SUBJECT),
    requestText,
    replyOpening,
    replyText,
    requestOpening,
    matter,
  }
}

/** Parse a card body against the wire contract. Any deviation names its field. */
export function parseRequestCard(body: Record<string, unknown>): ParseResult {
  const id = parseIdentity(body)
  if ('field' in id) return { ok: false, field: id.field }
  const content = parseContent(body)
  if ('field' in content) return { ok: false, field: content.field }
  const tools = parseTools(body.tools)
  if (!tools) return { ok: false, field: 'tools' }
  const actions = parseActions(body.actions)
  if (actions === undefined) return { ok: false, field: 'actions' }
  for (const f of ['replies', 'refused', 'failed'] as const) {
    if (!isCount(body[f], MAX_COUNT)) return { ok: false, field: f }
  }
  const job = parseJob(body.job)
  // A job card carries its job, and so does a held_reply (the job the
  // requester wrote about, which must be held); a reply or alarm card carries none.
  const wantsJob = id.kind === 'job_done' || id.kind === 'held_reply'
  if (job === undefined || wantsJob !== (job !== null)) {
    return { ok: false, field: 'job' }
  }
  if (id.kind === 'held_reply' && job?.state !== 'held') return { ok: false, field: 'job' }
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
      actions,
      replies: body.replies as number,
      refused: body.refused as number,
      failed: body.failed as number,
      job,
      lane,
    },
  }
}

// ---------------------------------------------------------------------------
// The send ledger (operator_request_cards). Numbers and keys only.
// ---------------------------------------------------------------------------

const JOB_SUBMIT_RE = /^([a-z]+)_job_submit$/

/**
 * The job lanes a turn queued, read from its tool names (`medchron_job_submit`
 * -> `medchron`), sorted and comma-joined; '' when it queued none (migration
 * 0134). WHY: a `replied` card's job_lane stays null even when the request
 * started a job, so without this a queued chronology read as no job at all.
 */
export function jobsQueued(tools: readonly string[]): string {
  const lanes = new Set<string>()
  for (const tool of tools) {
    const lane = JOB_SUBMIT_RE.exec(tool)?.[1]
    if (lane && (JOB_LANES as readonly string[]).includes(lane)) lanes.add(lane)
  }
  return [...lanes].sort().join(',')
}

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
          replies, refused, failed, job_lane, job_state, lane_key, jobs_queued)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
      card.lane?.key ?? null,
      jobsQueued(card.tools)
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

/**
 * The request-card email: what SMD ops reads for every request a person sends
 * an Operator seat (the card is parsed and ledgered in `request-card.ts`).
 *
 * Four questions, each under its own heading (Captain, 2026-10-27): who asked
 * (the firm's authored name and the address), what they asked (the subject and
 * their own words), what the Operator replied, and what it did (each tool
 * grouped by what it can do, and any job it started). Times are Arizona time.
 * The subject lines are unchanged so mail filters keep matching.
 *
 * ADR 0052 s5: the text here exists in the email and nowhere else; nothing in
 * this module writes D1.
 */

import { jobsQueued, LANE_WHY, type ActionClassName, type RequestCard } from './request-card'

const SUBJECT_EXCERPT = 70

function subjectExcerpt(subject: string): string {
  if (subject.length === 0) return '(no subject)'
  return subject.length > SUBJECT_EXCERPT ? `${subject.slice(0, SUBJECT_EXCERPT - 3)}...` : subject
}

/** The subject's middle: what happened, without the seat or the request's subject. */
function cardHeadline(card: RequestCard): string {
  const lane = card.job?.lane ?? 'job'
  switch (card.kind) {
    case 'replied':
      return `request: first reply in ${card.minutes}m`
    case 'no_reply':
      return `NO REPLY after ${card.minutes}m`
    case 'job_done':
      return `${lane} job ${card.job?.state ?? 'ended'}`
    case 'lane_unproven':
      return card.lane?.heldJob
        ? `NEW KIND OF ASK, job waiting: ${card.lane.key}`
        : `NEW KIND OF ASK, first use: ${card.lane?.key ?? 'unknown'}`
    case 'held_reply':
      return `REQUESTER WROTE on held ${lane} job`
    case 'followup':
      return `later reply sent at +${card.minutes}m`
  }
}

export function cardSubject(slug: string, card: RequestCard): string {
  return `[SMD Ops] ${slug} ${cardHeadline(card)}: ${subjectExcerpt(card.subject)}`
}

/**
 * What a held_reply card asks of SMD. The Operator has already answered the
 * requester in plain words and cannot act on a held job (no email talks a seat
 * past a hold); the person reading this email is the one who records the
 * decision on the seat.
 */
function heldReplyLines(card: RequestCard): string[] {
  const job = card.job
  const reason = job?.reason ? ` (${job.reason})` : ''
  const id = job?.id ? ` ${job.id}` : ''
  return [
    `Written: ${phoenixTime(card.eventAt)} (${card.minutes} min after the request it answers)`,
    `Job: ${job?.lane ?? 'unknown'}${id} is HELD${reason}.`,
    'The requester wrote again in the thread of a request whose job is held. If the hold',
    "was the firm's question, their words above are its answer: read them, record the",
    'decision on the seat, and resume the job. The Operator has acknowledged the message',
    'and will not act on it; nothing goes to the requester until the job delivers.',
  ]
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
  const at = phoenixTime(card.eventAt)
  switch (card.kind) {
    case 'replied':
      return [`First reply: ${at} (${card.minutes} min after the request)`]
    case 'no_reply':
      return [
        `NO REPLY: nothing has answered this request after ${card.minutes} min (checked ${at}).`,
        'No reply went out, no queued job claims it, and the seat is not holding a reply for it.',
      ]
    case 'job_done': {
      const job = card.job
      const reason = job?.reason ? ` (${job.reason})` : ''
      return [
        `Job: ${job?.lane ?? 'unknown'} ${job?.state ?? 'ended'}${reason} at ${at}, ${card.minutes} min after the request`,
      ]
    }
    case 'lane_unproven':
      return [`Noticed: ${at} (${card.minutes} min after the request)`]
    case 'held_reply':
      return heldReplyLines(card)
    case 'followup':
      return [
        `Later reply sent: ${at} (${card.minutes} min after the request)`,
        'The Operator sent this after the first reply was carded, or in answer to a message',
        'with no request on record. It is what the requester was told; read it before saying',
        'what they have or have not been told.',
      ]
  }
}

const PHOENIX = new Intl.DateTimeFormat('en-US', {
  timeZone: 'America/Phoenix',
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

/** A card time in Arizona, which keeps MST all year: "Tue, Oct 27, 9:43 AM MST". */
export function phoenixTime(iso: string): string {
  const ms = Date.parse(iso)
  return Number.isNaN(ms) ? iso : `${PHOENIX.format(new Date(ms))} MST`
}

/** "Chris Price <chris@...>" when the firm authored a name, else the address alone. */
function requester(card: RequestCard): string {
  return card.whoName ? `${card.whoName} <${card.who}>` : card.who
}

/** Each line of a block, indented, so the message stands apart from the labels. */
function indented(text: string): string[] {
  return text.split('\n').map((line) => (line.length > 0 ? `  ${line}` : ''))
}

const SERVER_LABELS: Record<string, string> = {
  smokeball: 'Smokeball',
  msgraph: 'Microsoft 365',
  agentmail: 'Mailbox',
  clio: 'Clio',
  ga4: 'Google Analytics',
  wordpress: 'WordPress',
  youtube: 'YouTube',
  reference: 'Reference',
}

/**
 * A tool name as a reader says it: `mcp_msgraph_mail_create_draft` is
 * "Microsoft 365: mail create draft", `operations_request` is "operations
 * request". Mechanical and reversible on purpose: the raw name follows in
 * brackets, so nothing is renamed into a claim the tool does not make.
 */
export function toolLabel(tool: string): string {
  const mcp = /^mcp_([a-z0-9]+)_(.+)$/.exec(tool)
  const words = (s: string) => s.replace(/[_.:-]+/g, ' ').trim()
  if (mcp && SERVER_LABELS[mcp[1]]) return `${SERVER_LABELS[mcp[1]]}: ${words(mcp[2])}`
  return words(tool)
}

/** The headings the work is grouped under, in reading order. */
const ACTION_GROUPS: { heading: string; classes: readonly ActionClassName[] }[] = [
  {
    heading: 'Sent',
    classes: [
      'external_send',
      'external_send_internal',
      'external_send_client',
      'external_send_vendor',
      'external_send_as_staff',
    ],
  },
  { heading: 'Committed', classes: ['commitment'] },
  { heading: 'Deleted or irreversible', classes: ['destructive'] },
  { heading: 'Ran code', classes: ['code_execution'] },
  { heading: 'Recorded or drafted (nothing sent)', classes: ['internal_write'] },
  { heading: 'Looked up', classes: ['read'] },
  { heading: 'Not in the trust map', classes: ['refused', 'unmapped'] },
]

/** What the Operator did, grouped by what each tool can do; [] when nothing ran. */
function actionLines(card: RequestCard): string[] {
  const queued = jobsQueued(card.tools)
  const lines: string[] = []
  if (card.actions) {
    for (const group of ACTION_GROUPS) {
      const inGroup = card.actions.filter((a) => group.classes.includes(a.actionClass))
      if (inGroup.length === 0) continue
      lines.push(`${group.heading}:`)
      for (const a of inGroup) {
        const times = a.count > 1 ? ` x${a.count}` : ''
        lines.push(`  - ${toolLabel(a.tool)}${times} [${a.tool}]`)
      }
    }
  } else if (card.tools.length > 0) {
    lines.push(`Tools run (${card.tools.length}): ${card.tools.join(', ')}`)
  }
  if (queued) lines.push(`Started a job: ${queued.split(',').join(', ')}`)
  return lines
}

/**
 * The plain-text body. SMD ops only; this text never reaches a client or D1.
 *
 * Four questions, in this order, each under its own heading (Captain,
 * 2026-10-27): who asked, what they asked, what the Operator answered, and
 * what it did. A card from a seat that predates the full texts falls back to
 * the openings it carried.
 */
export function cardText(slug: string, card: RequestCard): string {
  const asked = card.requestText ?? card.requestOpening
  const answer = card.replyText ?? card.replyOpening
  const lines = [
    'WHO ASKED',
    `  ${requester(card)}`,
    `  Received ${phoenixTime(card.receivedAt)} on the ${slug} seat`,
    '',
    // A held_reply card is about a LATER message on a held job, not the ask.
    card.kind === 'held_reply' ? 'WHAT THEY WROTE (on the held job)' : 'WHAT THEY ASKED',
    `  Subject: ${card.subject.length > 0 ? card.subject : '(no subject)'}`,
  ]
  if (asked) lines.push('', ...indented(asked))
  else if (card.kind === 'held_reply') lines.push('  (no words in the message)')
  else lines.push('  (the seat did not record the words of this message)')
  lines.push('', 'STATUS', ...outcomeLines(card).map((l) => `  ${l}`))
  lines.push(...laneLines(slug, card).map((l) => `  ${l}`))
  // Only when the seat named one. "Matter: none named" is law-firm vocabulary
  // and read as noise on every non-legal seat (Brokery, 2026-10-16).
  if (card.matter) lines.push(`  Matter: ${card.matter}`)
  if (answer) {
    const label =
      card.kind === 'followup' ? 'THE OPERATOR WROTE (a later reply)' : "THE OPERATOR'S REPLY"
    lines.push('', label, ...indented(answer))
  } else if (card.kind === 'replied' || card.kind === 'followup') {
    lines.push('', "THE OPERATOR'S REPLY", '  (the seat did not record the words of this reply)')
  }
  const did = actionLines(card)
  lines.push('', 'WHAT THE OPERATOR DID')
  if (did.length > 0) lines.push(...did.map((l) => `  ${l}`))
  else lines.push('  No tools ran for this card.')
  lines.push(
    `  Replies sent: ${card.replies}. Refused: ${card.refused}. Failed: ${card.failed}.`,
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

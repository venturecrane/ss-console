/**
 * The Monday summary: what a check stopped the Operator from doing on a client's
 * seat this week. Same shape as every other ops email (Captain, 2026-11-02:
 * "mostly noise"): what happened and what to do, then the machine's table below a
 * fold. Until then it was a table of "plugin_block" by "mcp_msgraph_mail_create_draft",
 * test seats included.
 *
 * It does not say why a check stopped each call, because the ledger does not know:
 * `not_allowed` covers the trust gate, any plugin and a banned tool, and keeps only
 * which. So it must not claim each one is a scope question or a wrong setting, as it
 * used to. An agent finds out why on the seat, when something here looks wrong.
 */

import { seatIsQuiet } from './mail-policy'
import { escapeHtml } from './html'
import type { Env } from './index'
import {
  LEDGER_COLUMNS,
  markNotified,
  MAX_LISTED,
  phoenixTime,
  sendEmail,
  type LedgerRow,
  type ShortfallNotification,
} from './shortfalls'

const TOOL_WORDS: Record<string, string> = {
  mcp_agentmail_create_draft: 'Draft an email',
  mcp_msgraph_mail_create_draft: 'Draft an email',
  mcp_smokeball_create_memo: 'Write a memo in Smokeball',
  mcp_smokeball_create_task: 'Create a task in Smokeball',
  mcp_smokeball_place_records_order: 'Place a records order in Smokeball',
  smd_send_message: 'Send a message',
  calendar_invite_create: 'Send a calendar invite',
  partner_message_send: 'Send a message to a partner',
  record_store_write: 'Save a record',
  start_background_job: 'Start a background job',
  web_extract: 'Read a web page',
  read_file: 'Read a file',
}

/** A tool as something a person does. One with no entry still reads. */
export function toolWords(tool: string): string {
  const known = TOOL_WORDS[tool]
  if (known) return known
  const text = (tool.startsWith('mcp_') ? tool.slice(4) : tool).replace(/_/g, ' ').trim()
  return text ? text[0].toUpperCase() + text.slice(1) : 'Use a tool'
}

/** One line per thing tried, most attempted first, however many times it happened. */
function tryLines(rows: LedgerRow[]): string {
  const byWords = new Map<string, LedgerRow[]>()
  for (const r of rows) {
    const words = toolWords(r.tool)
    byWords.set(words, [...(byWords.get(words) ?? []), r])
  }
  const lines = [...byWords.entries()]
    .sort((a, b) => b[1].length - a[1].length)
    .map(([words, tries]) => {
      const times = tries.length === 1 ? 'once' : `${tries.length} times`
      const last = phoenixTime(tries[tries.length - 1].ts)
      return `<li>${escapeHtml(words)}: ${times}, last on ${escapeHtml(last)}</li>`
    })
  return `<ul>${lines.join('')}</ul>`
}

function rowsTable(rows: LedgerRow[]): string {
  const listed = rows.slice(0, MAX_LISTED)
  const body = listed
    .map(
      (r) =>
        `<tr><td>${escapeHtml(phoenixTime(r.ts))}</td><td>${escapeHtml(r.tool)}</td>` +
        `<td>${escapeHtml(r.routine || 'not recorded')}</td>` +
        `<td>${escapeHtml(r.request_at ? phoenixTime(r.request_at) : 'none linked')}</td>` +
        `<td>${escapeHtml(r.code)}</td></tr>`
    )
    .join('')
  const more =
    rows.length > listed.length ? `<p>${rows.length - listed.length} more not listed.</p>` : ''
  return (
    `<table border="1" cellpadding="4" cellspacing="0">` +
    `<tr><th>When (MST)</th><th>Tool</th><th>Routine</th><th>Request received</th><th>Code</th></tr>` +
    `${body}</table>${more}`
  )
}

function digestHtml(bySeat: Map<string, LedgerRow[]>): string {
  const seats = [...bySeat.entries()]
  const lists = seats
    .map(([slug, rows]) => `<h3>${escapeHtml(slug)}: ${rows.length}</h3>${tryLines(rows)}`)
    .join('')
  const tables = seats
    .map(([slug, rows]) => `<p><strong>${escapeHtml(slug)}</strong></p>${rowsTable(rows)}`)
    .join('')
  return (
    `<p><strong>WHAT HAPPENED:</strong> This week a check stopped the Operator from doing the ` +
    `things below. None of these attempts went through.</p>` +
    `<p><strong>WHAT YOU NEED TO DO:</strong> Nothing, unless something here is work the client ` +
    `should be getting. If it is, give this email to an agent to find out why it was stopped. ` +
    `Whether to change what the client gets is your decision.</p>` +
    lists +
    `<hr><p><strong>DETAILS, for the agent who picks this up</strong><br>` +
    `The ledger keeps which check stopped each call, not why: the reason is on the seat. ` +
    `Test seats are left out.</p>${tables}`
  )
}

/**
 * Every not-yet-reported not_allowed row on a client's seat, grouped by seat. An
 * empty week sends nothing. A test seat's rows are closed without an email, so a
 * seat that later becomes a customer does not bring its test history with it.
 */
export async function sendShortfallDigest(env: Env): Promise<ShortfallNotification | null> {
  const res = await env.DB.prepare(
    `SELECT customer_slug, ${LEDGER_COLUMNS} FROM operator_shortfalls
      WHERE class = 'not_allowed' AND notified_at IS NULL
      ORDER BY customer_slug, ts ASC`
  ).all<LedgerRow & { customer_slug: string }>()
  const bySeat = new Map<string, LedgerRow[]>()
  for (const r of res.results ?? []) {
    bySeat.set(r.customer_slug, [...(bySeat.get(r.customer_slug) ?? []), r])
  }
  for (const [slug, rows] of [...bySeat]) {
    if (await seatIsQuiet(env.DB, slug)) {
      await markNotified(
        env.DB,
        slug,
        rows.map((r) => r.event_key)
      )
      bySeat.delete(slug)
    }
  }
  const count = [...bySeat.values()].reduce((n, rows) => n + rows.length, 0)
  if (count === 0) return null
  const subject = `[SMD Ops] Weekly: what a check stopped the Operator from doing (${count})`
  const sent = await sendEmail(env, subject, digestHtml(bySeat))
  if (sent.ok) {
    for (const [slug, rows] of bySeat) {
      await markNotified(
        env.DB,
        slug,
        rows.map((r) => r.event_key)
      )
    }
  }
  return { customer_slug: '*', events: count, emailed: sent.ok, resendId: sent.resendId }
}

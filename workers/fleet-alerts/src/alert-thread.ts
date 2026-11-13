/**
 * Threading an alert's RECOVERED notice under the alert itself (2026-10-05).
 *
 * Every level condition mails twice: ALERT when it opens, RECOVERED when it
 * closes. Sent as two unrelated messages, a flapping seat fills the inbox with
 * pairs (about 200 alert mails in the 30 days to 2026-10-05, most of them
 * ALERT/RECOVERED pairs, most of them found in Trash). Gmail threads a message
 * into a conversation when it carries `In-Reply-To`/`References` naming an
 * earlier Message-ID AND its subject matches once a `Re:` prefix is removed,
 * so the recovery is sent as a reply: one inbox item per incident.
 *
 * The Message-ID is minted HERE, before the alert is sent, and stored with the
 * open row (`fleet_alert_state.alert_message_id`, migration 0120). It cannot be
 * rebuilt from row fields at recovery time: `opened_at` is written after the
 * send. A row opened before 0120 has no id, and its recovery falls back to the
 * old standalone RECOVERED subject.
 */

const MESSAGE_ID_DOMAIN = 'smd.services'

/** A fresh RFC 5322 Message-ID for an ALERT email. */
export function mintAlertMessageId(): string {
  return `<smd-alert.${crypto.randomUUID()}@${MESSAGE_ID_DOMAIN}>`
}

/**
 * Stamped on every RECOVERED email, threaded or standalone (2026-11-13). The
 * mailbox keeper on the SMD operations seat (operator/workspace_broker/
 * mailbox_keeper.py) trashes a settled alert thread only on code-checked
 * signals; once this worker change is live, a seat that authors
 * `require_alert_state_header: true` also requires this header on the newest
 * message. A reminder and an alert never carry it.
 */
const ALERT_STATE_HEADER = 'X-SMD-Alert-State'

/** Headers for the Resend `headers` field: the alert names itself; a recovery or reminder replies to it. */
export function threadHeaders(
  kind: 'opened' | 'resolved' | 'reminder',
  messageId: string | null
): Record<string, string> | undefined {
  const state: Record<string, string> =
    kind === 'resolved' ? { [ALERT_STATE_HEADER]: 'resolved' } : {}
  if (!messageId) return kind === 'resolved' ? state : undefined
  if (kind === 'opened') return { 'Message-ID': messageId }
  return { 'In-Reply-To': messageId, References: messageId, ...state }
}

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

/** Headers for the Resend `headers` field: the alert names itself, the recovery replies to it. */
export function threadHeaders(
  kind: 'opened' | 'resolved',
  messageId: string | null
): Record<string, string> | undefined {
  if (!messageId) return undefined
  if (kind === 'opened') return { 'Message-ID': messageId }
  return { 'In-Reply-To': messageId, References: messageId }
}

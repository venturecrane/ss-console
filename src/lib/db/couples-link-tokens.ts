/**
 * Issue and redeem couples Telegram deep-link tokens (migration 0127).
 * Token shape and rationale: src/lib/hosted-agent/couples/link-token.ts.
 *
 * Redemption is the only path by which a Telegram id reaches a partner row,
 * and it does NOT admit anyone to the agent: the seat's allowlist is authored
 * config, and the id reaches customer.yaml only through the generator's
 * reviewed PR (R4). This module records the binding; nothing more.
 */

import type { D1Database } from '@cloudflare/workers-types'
import {
  TOKEN_RE,
  TOKEN_TTL_MS,
  generateLinkToken,
  hashLinkToken,
  sqliteUtc,
} from '../hosted-agent/couples/link-token'
import { COUPLES_ROLE } from './couples-onboarding'

export interface IssuedToken {
  token: string
  expiresAt: string
}

/**
 * Mint a fresh token for one partner. Every earlier unused token for that
 * partner is burned first, so at most one link per person is live.
 */
export async function issueLinkToken(
  db: D1Database,
  personId: string,
  now: number = Date.now()
): Promise<IssuedToken> {
  const token = generateLinkToken()
  const hash = await hashLinkToken(token)
  const expiresAt = sqliteUtc(now + TOKEN_TTL_MS)
  const nowSql = sqliteUtc(now)
  await db.batch([
    db
      .prepare(
        `UPDATE hosted_agent_link_tokens SET used_at = ?
          WHERE person_id = ? AND used_at IS NULL`
      )
      .bind(nowSql, personId),
    db
      .prepare(
        `INSERT INTO hosted_agent_link_tokens (token_hash, person_id, purpose, expires_at, created_at)
         VALUES (?, ?, 'telegram_dm', ?, ?)`
      )
      .bind(hash, personId, expiresAt, nowSql),
  ])
  return { token, expiresAt }
}

export type RedeemResult =
  { result: 'bound'; personKey: string; personId: string } | { result: 'rejected' }

interface TokenRow {
  person_id: string
  expires_at: string
  used_at: string | null
  person_key: string
  intake_id: string
  customer_slug: string | null
}

/**
 * Redeem a token presented to a seat's bot.
 *
 * Refused (one undifferentiated `rejected`, so a caller learns nothing about
 * why) when: the token is malformed or unknown; it has expired; it was used;
 * the redeeming seat is not the instance the person belongs to; or the
 * Telegram id is already bound to the OTHER partner on that instance.
 *
 * Single use is enforced by the consuming UPDATE's guard, not by the read
 * above it: two concurrent redemptions both read `used_at IS NULL`, and only
 * one UPDATE changes a row.
 */
export async function redeemLinkToken(
  db: D1Database,
  input: { token: string; seatSlug: string; telegramUserId: string },
  now: number = Date.now()
): Promise<RedeemResult> {
  const rejected: RedeemResult = { result: 'rejected' }
  if (!TOKEN_RE.test(input.token) || !/^\d{1,20}$/.test(input.telegramUserId)) return rejected
  const hash = await hashLinkToken(input.token)
  const row = await db
    .prepare(
      `SELECT t.person_id, t.expires_at, t.used_at, p.person_key, p.intake_id, i.customer_slug
         FROM hosted_agent_link_tokens t
         JOIN hosted_agent_people p ON p.id = t.person_id
         JOIN hosted_agent_intake i ON i.id = p.intake_id
        WHERE t.token_hash = ? AND t.purpose = 'telegram_dm' AND i.role = '${COUPLES_ROLE}'`
    )
    .bind(hash)
    .first<TokenRow>()
  if (!row || row.used_at !== null) return rejected
  if (!row.customer_slug || row.customer_slug !== input.seatSlug) return rejected
  const nowSql = sqliteUtc(now)
  if (row.expires_at <= nowSql) return rejected

  const clash = await db
    .prepare(
      `SELECT 1 AS n FROM hosted_agent_people WHERE intake_id = ? AND id != ? AND telegram_id = ?`
    )
    .bind(row.intake_id, row.person_id, input.telegramUserId)
    .first<{ n: number }>()
  if (clash) return rejected

  const consumed = await db
    .prepare(
      `UPDATE hosted_agent_link_tokens SET used_at = ?, used_telegram_id = ?
        WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?`
    )
    .bind(nowSql, input.telegramUserId, hash, nowSql)
    .run()
  if ((consumed.meta?.changes ?? 0) !== 1) return rejected

  await db
    .prepare(
      `UPDATE hosted_agent_people SET telegram_id = ?, telegram_bound_at = ?, updated_at = ?
        WHERE id = ?`
    )
    .bind(input.telegramUserId, nowSql, nowSql, row.person_id)
    .run()
  return { result: 'bound', personKey: row.person_key, personId: row.person_id }
}

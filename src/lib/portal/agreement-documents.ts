/**
 * Portal read + download authorization for executed Operator agreements
 * (ss#2641).
 *
 * The Compliance surface lists a firm's own executed paper; this module
 * decides who may fetch the bytes behind it.
 *
 * Authorization is by ROW, not by key shape. The document's D1 row names the
 * entity that owns it, so a key belonging to another firm fails on identity
 * rather than on a prefix-string comparison that a future key-scheme change
 * could quietly loosen. The role requirement (principal or compliance)
 * mirrors the Compliance page that links the document, so the download door
 * is not wider than the page.
 *
 * `not_agreement` is deliberately distinct from `forbidden`: the download
 * endpoint serves engagement and quote documents too, and an agreement miss
 * must fall through to those checks rather than reject the request.
 */

import type { D1Database } from '@cloudflare/workers-types'
import { listProductRoles } from './product-access'
import {
  listOperatorAgreementDocuments,
  type OperatorAgreementDocument,
} from '../db/operator-agreements'

/** Roles that may read the firm's executed paper. Same set the Compliance
 * page admits, for the same reason: this is governance, not matter data. */
export const AGREEMENT_READER_ROLES = ['principal', 'compliance'] as const

export type AgreementKeyDecision =
  | { kind: 'allowed'; document: OperatorAgreementDocument }
  | { kind: 'forbidden' }
  | { kind: 'not_agreement' }

export async function getOperatorAgreementForKey(
  db: D1Database,
  args: { key: string; userId: string; orgId: string; entityId: string }
): Promise<AgreementKeyDecision> {
  const row = await db
    .prepare('SELECT * FROM operator_agreement_documents WHERE storage_key = ?')
    .bind(args.key)
    .first<OperatorAgreementDocument>()
  if (!row) return { kind: 'not_agreement' }

  // The row exists, so from here every failure is a refusal, never a
  // fall-through: an agreement key that is not this caller's must not get a
  // second chance at the engagement-document checks below it.
  if (row.org_id !== args.orgId || row.entity_id !== args.entityId) return { kind: 'forbidden' }

  const roles = await listProductRoles(db, args.userId, args.entityId, 'operator')
  const permitted = AGREEMENT_READER_ROLES.some((r) => roles.includes(r))
  if (!permitted) return { kind: 'forbidden' }

  return { kind: 'allowed', document: row }
}

export interface AgreementListItem {
  id: string
  title: string
  executedOn: string
  href: string
}

/**
 * The Compliance card's rows: this instance's executed documents, newest
 * first, each with the portal download href.
 *
 * Returns an empty array when the firm has none. The card renders nothing at
 * all in that case rather than promising the paper will appear later, which
 * would be a claim about future business behavior on a client surface
 * (CLAUDE.md Pattern A).
 */
export async function listAgreementsForInstance(
  db: D1Database,
  entityId: string,
  instanceSlug: string
): Promise<AgreementListItem[]> {
  const rows = await listOperatorAgreementDocuments(db, entityId, instanceSlug)
  return rows.map((row) => ({
    id: row.id,
    title: row.title,
    executedOn: row.executed_on,
    href: `/api/portal/documents/${row.storage_key}`,
  }))
}

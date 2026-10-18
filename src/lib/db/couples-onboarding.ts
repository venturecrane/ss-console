/**
 * Couples onboarding: instances and partners (ADR 0090 decision 4, migration 0127).
 *
 * A couples instance is a Hosted Agent work item (`hosted_agent_intake`) with
 * `role = 'couples'`, on an entity with a `hosted-agent` subscription at
 * `provisioning`. It is SMD-paid (#3264, Captain decision 1): no checkout, no
 * Stripe linkage, nothing billed. Each partner is a `hosted_agent_people` row
 * that SMD authors with a name and an email; the partner later claims it by
 * signing in with that verified email (see src/lib/portal/couples-access.ts).
 *
 * PRIVACY. A partner's private answers (email, consent, Telegram binding) are
 * read by that partner and by SMD's admin view only. Portal code reaches them
 * through {@link getPersonForUser}, which returns the caller's own row; the
 * other partner is reached only through {@link partnerPublicView}, which by
 * construction carries no private field (R7).
 */

import type { D1Database } from '@cloudflare/workers-types'
import { ORG_ID } from '../constants'
import { createEntity } from './entities'
import { EMAIL_IDENTITY_PREDICATE, normalizeEmail } from '../identity/email'
import { HOSTED_AGENT_PRODUCT_SLUG } from '../portal/hosted-agent-access'
import type { PersonKey } from '../hosted-agent/couples/joint-settings'

export const COUPLES_ROLE = 'couples'
/** The product role a partner holds on (entity, 'hosted-agent'). Not principal. */
export const PARTNER_PRODUCT_ROLE = 'partner'

/** Bumped when the consent wording on the portal changes. */
export const CONSENT_VERSION = 'couples-2026-10-17'

export interface CouplesPersonRow {
  id: string
  intake_id: string
  person_key: PersonKey
  name: string
  email: string
  user_id: string | null
  invited_at: string | null
  clerk_invitation_id: string | null
  consent_at: string | null
  consent_version: string | null
  telegram_id: string | null
  telegram_bound_at: string | null
  created_at: string
  updated_at: string
}

export interface CouplesInstanceRow {
  id: string
  entity_id: string
  subscription_id: string
  status: string
  agent_name: string | null
  customer_slug: string | null
  telegram_bot_username: string | null
  shared_telegram_chat: string | null
  entity_name: string
  clerk_org_id: string | null
  created_at: string
}

/** What one partner may see of the other: shared facts only (R7). */
export interface PartnerPublicView {
  person_key: PersonKey
  name: string
}

const PERSON_COLUMNS =
  'id, intake_id, person_key, name, email, user_id, invited_at, clerk_invitation_id, ' +
  'consent_at, consent_version, telegram_id, telegram_bound_at, created_at, updated_at'

const INSTANCE_SELECT = `SELECT i.id, i.entity_id, i.subscription_id, i.status, i.agent_name,
        i.customer_slug, i.telegram_bot_username, i.shared_telegram_chat, i.created_at,
        e.name AS entity_name, e.clerk_org_id
   FROM hosted_agent_intake i JOIN entities e ON e.id = i.entity_id
  WHERE i.role = '${COUPLES_ROLE}'`

export interface NewCouplesInstance {
  coupleName: string
  agentName: string
  partners: [{ name: string; email: string }, { name: string; email: string }]
}

/**
 * Create the entity, the SMD-paid subscription at `provisioning`, the intake
 * work item and both partner rows. Returns the intake id (the instance id).
 */
export async function createCouplesInstance(
  db: D1Database,
  input: NewCouplesInstance
): Promise<string> {
  const intakeId = crypto.randomUUID()
  const entity = await createEntity(db, ORG_ID, {
    // Entity slugs are unique per org and couple names are not; key the slug
    // to the instance so two couples with the same name never collide.
    slug: `couple-${intakeId.slice(0, 8)}`,
    name: input.coupleName,
    stage: 'engaged',
    source_pipeline: 'hosted-agent-couples',
  })
  const subscriptionId = crypto.randomUUID()
  const settings = JSON.stringify({ plan: 'smd-paid', role: COUPLES_ROLE })
  const statements = [
    db
      .prepare(
        `INSERT INTO subscriptions
           (id, org_id, entity_id, product_slug, status, started_at, settings_json, created_at, updated_at)
         VALUES (?, ?, ?, ?, 'provisioning', datetime('now'), ?, datetime('now'), datetime('now'))`
      )
      .bind(subscriptionId, ORG_ID, entity.id, HOSTED_AGENT_PRODUCT_SLUG, settings),
    db
      .prepare(
        `INSERT INTO hosted_agent_intake (id, org_id, entity_id, subscription_id, role, agent_name)
         VALUES (?, ?, ?, ?, ?, ?)`
      )
      .bind(intakeId, ORG_ID, entity.id, subscriptionId, COUPLES_ROLE, input.agentName),
    ...input.partners.map((p, i) =>
      db
        .prepare(
          `INSERT INTO hosted_agent_people (id, intake_id, person_key, name, email) VALUES (?, ?, ?, ?, ?)`
        )
        .bind(crypto.randomUUID(), intakeId, i === 0 ? 'p1' : 'p2', p.name, normalizeEmail(p.email))
    ),
  ]
  await db.batch(statements)
  return intakeId
}

export async function listCouplesInstances(db: D1Database): Promise<CouplesInstanceRow[]> {
  const r = await db
    .prepare(`${INSTANCE_SELECT} ORDER BY i.created_at DESC`)
    .all<CouplesInstanceRow>()
  return r.results ?? []
}

export async function getCouplesInstance(
  db: D1Database,
  intakeId: string
): Promise<CouplesInstanceRow | null> {
  return (
    (await db
      .prepare(`${INSTANCE_SELECT} AND i.id = ?`)
      .bind(intakeId)
      .first<CouplesInstanceRow>()) ?? null
  )
}

export async function getCouplesInstanceByEntity(
  db: D1Database,
  entityId: string
): Promise<CouplesInstanceRow | null> {
  return (
    (await db
      .prepare(`${INSTANCE_SELECT} AND i.entity_id = ? ORDER BY i.created_at DESC LIMIT 1`)
      .bind(entityId)
      .first<CouplesInstanceRow>()) ?? null
  )
}

/** Both partners, p1 first. SMD-side (admin, generator) only. */
export async function listPeople(db: D1Database, intakeId: string): Promise<CouplesPersonRow[]> {
  const r = await db
    .prepare(
      `SELECT ${PERSON_COLUMNS} FROM hosted_agent_people WHERE intake_id = ? ORDER BY person_key`
    )
    .bind(intakeId)
    .all<CouplesPersonRow>()
  return r.results ?? []
}

export async function getPersonById(
  db: D1Database,
  personId: string
): Promise<CouplesPersonRow | null> {
  return (
    (await db
      .prepare(`SELECT ${PERSON_COLUMNS} FROM hosted_agent_people WHERE id = ?`)
      .bind(personId)
      .first<CouplesPersonRow>()) ?? null
  )
}

/** The caller's own partner row on an instance, by the local user it is claimed by. */
export async function getPersonForUser(
  db: D1Database,
  intakeId: string,
  userId: string
): Promise<CouplesPersonRow | null> {
  return (
    (await db
      .prepare(
        `SELECT ${PERSON_COLUMNS} FROM hosted_agent_people WHERE intake_id = ? AND user_id = ?`
      )
      .bind(intakeId, userId)
      .first<CouplesPersonRow>()) ?? null
  )
}

/** The other partner, reduced to what may be shown to the caller (R7). */
export async function partnerPublicView(
  db: D1Database,
  intakeId: string,
  ownPersonId: string
): Promise<PartnerPublicView | null> {
  const row = await db
    .prepare(`SELECT person_key, name FROM hosted_agent_people WHERE intake_id = ? AND id != ?`)
    .bind(intakeId, ownPersonId)
    .first<{ person_key: PersonKey; name: string }>()
  return row ? { person_key: row.person_key, name: row.name } : null
}

/**
 * Claim the unclaimed partner row whose authored email matches the signed-in
 * user's verified email, and grant the `partner` product role. Idempotent.
 * Returns the claimed row, or null when nothing matches.
 *
 * The caller has already proven the user reaches this entity (Clerk
 * Organization membership or a direct binding); the email match proves the
 * user is the person SMD invited. Both are required.
 */
export async function claimPartnerByEmail(
  db: D1Database,
  intakeId: string,
  user: { id: string; email: string },
  entityId: string
): Promise<CouplesPersonRow | null> {
  const existing = await getPersonForUser(db, intakeId, user.id)
  if (existing) return existing
  const email = normalizeEmail(user.email)
  const result = await db
    .prepare(
      `UPDATE hosted_agent_people SET user_id = ?, updated_at = datetime('now')
        WHERE intake_id = ? AND ${EMAIL_IDENTITY_PREDICATE} AND user_id IS NULL`
    )
    .bind(user.id, intakeId, email)
    .run()
  if ((result.meta?.changes ?? 0) !== 1) return null
  const role = await db
    .prepare(
      `SELECT id FROM product_roles WHERE user_id = ? AND entity_id = ? AND product_slug = ?
          AND role = ? AND revoked_at IS NULL`
    )
    .bind(user.id, entityId, HOSTED_AGENT_PRODUCT_SLUG, PARTNER_PRODUCT_ROLE)
    .first<{ id: string }>()
  if (!role) {
    await db
      .prepare(
        `INSERT INTO product_roles
           (id, org_id, user_id, entity_id, product_slug, role, granted_by, granted_at)
         VALUES (?, ?, ?, ?, ?, ?, NULL, datetime('now'))`
      )
      .bind(
        crypto.randomUUID(),
        ORG_ID,
        user.id,
        entityId,
        HOSTED_AGENT_PRODUCT_SLUG,
        PARTNER_PRODUCT_ROLE
      )
      .run()
  }
  return getPersonForUser(db, intakeId, user.id)
}

export async function markPersonInvited(
  db: D1Database,
  personId: string,
  clerkInvitationId: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_people SET invited_at = datetime('now'), clerk_invitation_id = ?,
              updated_at = datetime('now') WHERE id = ?`
    )
    .bind(clerkInvitationId, personId)
    .run()
}

/** Record a partner's own name correction and consent. Never touches the other row. */
export async function recordPersonIntake(
  db: D1Database,
  personId: string,
  input: { name: string; consent: boolean }
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_people
          SET name = ?,
              consent_at = CASE WHEN ? = 1 THEN COALESCE(consent_at, datetime('now')) ELSE consent_at END,
              consent_version = CASE WHEN ? = 1 THEN COALESCE(consent_version, ?) ELSE consent_version END,
              updated_at = datetime('now')
        WHERE id = ?`
    )
    .bind(input.name, input.consent ? 1 : 0, input.consent ? 1 : 0, CONSENT_VERSION, personId)
    .run()
}

export async function setBotUsername(
  db: D1Database,
  intakeId: string,
  username: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_intake SET telegram_bot_username = ?, updated_at = datetime('now')
        WHERE id = ? AND role = '${COUPLES_ROLE}'`
    )
    .bind(username, intakeId)
    .run()
}

export async function setSharedTelegramChat(
  db: D1Database,
  intakeId: string,
  chatId: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE hosted_agent_intake SET shared_telegram_chat = ?, updated_at = datetime('now')
        WHERE id = ? AND role = '${COUPLES_ROLE}'`
    )
    .bind(chatId, intakeId)
    .run()
}

export async function setEntityClerkOrg(
  db: D1Database,
  entityId: string,
  clerkOrgId: string
): Promise<void> {
  await db
    .prepare(
      `UPDATE entities SET clerk_org_id = ?, updated_at = datetime('now')
        WHERE id = ? AND clerk_org_id IS NULL`
    )
    .bind(clerkOrgId, entityId)
    .run()
}

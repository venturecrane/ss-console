/**
 * Core intake processing — shared between the legacy POST /api/booking/intake
 * endpoint and the new POST /api/booking/reserve flow.
 *
 * Extracts the find-or-create entity + create contact + create assessment
 * pipeline into a single function so both code paths stay in sync.
 */

import { findOrCreateEntity } from '../db/entities'
import { createContact } from '../db/contacts'
import { createAssessment } from '../db/assessments'
import { appendContext } from '../db/context'

export interface IntakeInput {
  name: string
  email: string
  businessName: string
  vertical?: string | null
  employeeCount?: number | null
  yearsInBusiness?: number | null
  biggestChallenge?: string | null
  howHeard?: string | null
}

export interface IntakeResult {
  entityId: string
  contactId: string
  assessmentId: string
  /** Whether the entity was freshly created (vs. found by slug dedup). */
  entityCreated: boolean
  /** Formatted intake lines for use in admin notification emails. */
  intakeLines: string[]
}

/**
 * Process an intake submission: find-or-create entity, create contact (if
 * new email), create assessment, and append intake context.
 *
 * Does NOT send any emails — the caller decides what notifications to fire.
 *
 * @param scheduledAt - Optional ISO 8601 UTC string. When provided the
 *   assessment is created with `scheduled_at` set (used by /reserve).
 *   The legacy /intake flow passes undefined (assessment has no time yet).
 */
export async function processIntakeSubmission(
  db: D1Database,
  orgId: string,
  input: IntakeInput,
  scheduledAt?: string | null
): Promise<IntakeResult> {
  // 1. Find or create entity (dedup by business name slug)
  const { status, entity } = await findOrCreateEntity(db, orgId, {
    name: input.businessName,
    stage: 'prospect',
    source_pipeline: 'website_booking',
  })

  // 2. Create contact if one with this email doesn't already exist for this org
  const existingContact = await db
    .prepare('SELECT id FROM contacts WHERE org_id = ? AND email = ? LIMIT 1')
    .bind(orgId, input.email)
    .first<{ id: string }>()

  let contactId: string
  if (existingContact) {
    contactId = existingContact.id
  } else {
    const contact = await createContact(db, orgId, entity.id, {
      name: input.name,
      email: input.email,
    })
    contactId = contact.id
  }

  // 3. Create assessment
  const assessment = await createAssessment(db, orgId, entity.id, {
    scheduled_at: scheduledAt ?? null,
  })

  // 4. Append intake context
  const intakeLines: string[] = []
  if (input.vertical) intakeLines.push(`Vertical: ${input.vertical}`)
  if (input.employeeCount) intakeLines.push(`Employees: ${input.employeeCount}`)
  if (input.yearsInBusiness) intakeLines.push(`Years in business: ${input.yearsInBusiness}`)
  if (input.biggestChallenge)
    intakeLines.push(`What they're trying to accomplish: ${input.biggestChallenge}`)
  if (input.howHeard) intakeLines.push(`How they found us: ${input.howHeard}`)

  if (intakeLines.length > 0) {
    await appendContext(db, orgId, {
      entity_id: entity.id,
      type: 'intake',
      content: intakeLines.join('\n'),
      source: 'website_booking',
      metadata: {
        name: input.name,
        email: input.email,
        vertical: input.vertical,
        employee_count: input.employeeCount,
        years_in_business: input.yearsInBusiness,
        biggest_challenge: input.biggestChallenge,
        how_heard: input.howHeard,
      },
    })
  }

  return {
    entityId: entity.id,
    contactId,
    assessmentId: assessment.id,
    entityCreated: status === 'created',
    intakeLines,
  }
}

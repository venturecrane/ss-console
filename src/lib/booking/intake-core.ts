/**
 * Core intake logic shared between the public /book flow and admin booking.
 *
 * Extracted from POST /api/booking/intake to avoid duplicating the
 * entity + contact + assessment + context creation sequence.
 */

import { findOrCreateEntity } from '../db/entities.js'
import { createContact } from '../db/contacts.js'
import { appendContext } from '../db/context.js'
import { createAssessment } from '../db/assessments.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface IntakeInput {
  name: string
  email: string
  phone?: string | null
  company?: string | null
  role?: string | null
  notes?: string | null
  source?: string | null
}

export interface IntakeResult {
  entityId: string
  assessmentId: string
  contactId: string
}

// ---------------------------------------------------------------------------
// Core
// ---------------------------------------------------------------------------

/**
 * Process an intake submission: find or create entity + contact, create
 * assessment, and write a context entry. Returns the IDs of all created
 * or found records.
 *
 * The entity is deduped by company name slug. The contact is deduped by
 * email within the org. The assessment is always created fresh.
 */
export async function processIntakeSubmission(
  db: D1Database,
  orgId: string,
  input: IntakeInput
): Promise<IntakeResult> {
  // 1. Find or create entity from the company name
  const companyName = input.company || input.name
  const { entity } = await findOrCreateEntity(db, orgId, {
    name: companyName,
    stage: 'prospect',
    source_pipeline: input.source ?? 'website_booking',
  })

  // 2. Find existing contact by email or create a new one
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
      phone: input.phone ?? null,
      role: input.role ?? null,
    })
    contactId = contact.id
  }

  // 3. Create assessment (status defaults to 'scheduled')
  const assessment = await createAssessment(db, orgId, entity.id, {})

  // 4. Write context entry with intake details
  const contextParts: string[] = []
  contextParts.push(`Contact: ${input.name} <${input.email}>`)
  if (input.phone) contextParts.push(`Phone: ${input.phone}`)
  if (input.company) contextParts.push(`Company: ${input.company}`)
  if (input.role) contextParts.push(`Role: ${input.role}`)
  if (input.notes) contextParts.push(`Notes: ${input.notes}`)

  await appendContext(db, orgId, {
    entity_id: entity.id,
    type: 'intake',
    content: contextParts.join('\n'),
    source: input.source ?? 'website_booking',
    metadata: {
      name: input.name,
      email: input.email,
      phone: input.phone ?? null,
      company: input.company ?? null,
      role: input.role ?? null,
      notes: input.notes ?? null,
    },
  })

  return {
    entityId: entity.id,
    assessmentId: assessment.id,
    contactId,
  }
}

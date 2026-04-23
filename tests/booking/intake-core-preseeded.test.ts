/**
 * Tests for the pre-seeded intake path in processIntakeSubmission (#467).
 *
 * When the booking came from an admin-issued signed link, the intake must:
 *   - Anchor to the pre-existing entity (no slug dedup / fan-out to a new row)
 *   - Reuse the pre-created `scheduled` assessment (no duplicate row)
 *   - Still capture the guest's email/name as a contact if they differ
 */

import { describe, it, expect, beforeEach } from 'vitest'
import {
  createTestD1,
  runMigrations,
  discoverNumericMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'
import { processIntakeSubmission } from '../../src/lib/booking/intake-core'

const migrationsDir = resolve(process.cwd(), 'migrations')

const ORG_ID = 'org-ps'
const ENTITY_ID = 'ent-ps'
const ASSESSMENT_ID = 'asm-ps'
const CONTACT_ID = 'contact-ps'

describe('processIntakeSubmission — pre-seeded admin booking link flow (#467)', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })

    await db
      .prepare('INSERT INTO organizations (id, name, slug) VALUES (?, ?, ?)')
      .bind(ORG_ID, 'Org PS', 'org-ps')
      .run()

    // Admin's "Send booking link" action has already run: an entity exists in
    // `assessing` stage and a scheduled assessment row is waiting for its
    // schedule sidecar.
    await db
      .prepare(
        `INSERT INTO entities (id, org_id, name, slug, stage, stage_changed_at)
         VALUES (?, ?, ?, ?, 'meetings', datetime('now'))`
      )
      .bind(ENTITY_ID, ORG_ID, 'Scott Carpentry', 'scott-carpentry')
      .run()

    await db
      .prepare(
        `INSERT INTO contacts (id, org_id, entity_id, name, email)
         VALUES (?, ?, ?, ?, ?)`
      )
      .bind(CONTACT_ID, ORG_ID, ENTITY_ID, 'Scott Owner', 'owner@scottcarpentry.example')
      .run()

    await db
      .prepare(
        `INSERT INTO assessments (id, org_id, entity_id, status, scheduled_at)
         VALUES (?, ?, ?, 'scheduled', NULL)`
      )
      .bind(ASSESSMENT_ID, ORG_ID, ENTITY_ID)
      .run()
  })

  it('updates the pre-created assessment with the chosen slot instead of creating a new one', async () => {
    const slot = '2026-05-01T17:00:00.000Z'

    const result = await processIntakeSubmission(
      db,
      ORG_ID,
      {
        name: 'Scott Owner',
        email: 'owner@scottcarpentry.example',
        businessName: 'Scott Carpentry',
      },
      slot,
      'admin_booking_link',
      {
        entityId: ENTITY_ID,
        assessmentId: ASSESSMENT_ID,
        contactId: CONTACT_ID,
      }
    )

    expect(result.entityId).toBe(ENTITY_ID)
    expect(result.assessmentId).toBe(ASSESSMENT_ID)
    expect(result.entityCreated).toBe(false)

    // Only one assessment row for this entity
    const assessments = await db
      .prepare('SELECT id, scheduled_at FROM assessments WHERE entity_id = ?')
      .bind(ENTITY_ID)
      .all<{ id: string; scheduled_at: string }>()
    expect(assessments.results).toHaveLength(1)
    expect(assessments.results[0].id).toBe(ASSESSMENT_ID)
    expect(assessments.results[0].scheduled_at).toBe(slot)
  })

  it('anchors to the pre-seeded entity even when the guest types a different business name', async () => {
    const result = await processIntakeSubmission(
      db,
      ORG_ID,
      {
        // Guest mistyped the business name — we must NOT fan out to a new entity
        name: 'Scott Owner',
        email: 'owner@scottcarpentry.example',
        businessName: 'A DIFFERENT BUSINESS NAME',
      },
      '2026-05-01T17:00:00.000Z',
      'admin_booking_link',
      {
        entityId: ENTITY_ID,
        assessmentId: ASSESSMENT_ID,
      }
    )

    expect(result.entityId).toBe(ENTITY_ID)

    // Only one entity in this org
    const entities = await db
      .prepare('SELECT id FROM entities WHERE org_id = ?')
      .bind(ORG_ID)
      .all<{ id: string }>()
    expect(entities.results).toHaveLength(1)
    expect(entities.results[0].id).toBe(ENTITY_ID)
  })

  it('creates a new contact row when the guest books with a different email', async () => {
    await processIntakeSubmission(
      db,
      ORG_ID,
      {
        name: 'Other Person',
        email: 'someone-else@example.com',
        businessName: 'Scott Carpentry',
      },
      '2026-05-01T17:00:00.000Z',
      'admin_booking_link',
      {
        entityId: ENTITY_ID,
        assessmentId: ASSESSMENT_ID,
        contactId: CONTACT_ID,
      }
    )

    const contacts = await db
      .prepare('SELECT id, email FROM contacts WHERE entity_id = ? ORDER BY created_at')
      .bind(ENTITY_ID)
      .all<{ id: string; email: string }>()
    expect(contacts.results).toHaveLength(2)
    expect(contacts.results.map((c) => c.email).sort()).toEqual([
      'owner@scottcarpentry.example',
      'someone-else@example.com',
    ])
  })

  it('throws when the pre-seeded entity does not exist', async () => {
    await expect(
      processIntakeSubmission(
        db,
        ORG_ID,
        {
          name: 'X',
          email: 'x@example.com',
          businessName: 'Nonexistent',
        },
        '2026-05-01T17:00:00.000Z',
        'admin_booking_link',
        {
          entityId: 'nonexistent-entity',
          assessmentId: ASSESSMENT_ID,
        }
      )
    ).rejects.toThrow(/Pre-seeded entity not found/)
  })

  it('throws when the pre-seeded assessment does not exist', async () => {
    await expect(
      processIntakeSubmission(
        db,
        ORG_ID,
        {
          name: 'X',
          email: 'x@example.com',
          businessName: 'Scott Carpentry',
        },
        '2026-05-01T17:00:00.000Z',
        'admin_booking_link',
        {
          entityId: ENTITY_ID,
          assessmentId: 'nonexistent-assessment',
        }
      )
    ).rejects.toThrow(/Pre-seeded assessment not found/)
  })
})

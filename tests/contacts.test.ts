/**
 * Behavioural tests for the contact data layer (src/lib/db/contacts.ts).
 *
 * Until 2026-09-11 this file asserted on the module's SOURCE TEXT
 * (`expect(source()).toContain('org_id = ?')`), which stays green when a
 * predicate is dropped and goes red when an export is renamed (review
 * 2026-09-10, Testing 3). These run the real SQL against a migrated D1.
 * The route over this layer is covered in tests/admin-contacts-route.test.ts.
 */

import { describe, it, expect, beforeEach } from 'vitest'
import type { D1Database } from '@cloudflare/workers-types'
import {
  createContact,
  deleteContact,
  getContact,
  getFirstContactWithEmailForEntities,
  listContacts,
  updateContact,
} from '../src/lib/db/contacts'
import { migratedDb, seedEntity, seedOrg } from './_stubs/behavioural'

const ORG_A = 'org-a'
const ORG_B = 'org-b'
const ENT_A1 = 'ent-a1'
const ENT_A2 = 'ent-a2'
const ENT_B1 = 'ent-b1'

describe('contacts data layer against real D1', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await migratedDb()
    await seedOrg(db, ORG_A)
    await seedOrg(db, ORG_B)
    await seedEntity(db, { id: ENT_A1, orgId: ORG_A })
    await seedEntity(db, { id: ENT_A2, orgId: ORG_A })
    await seedEntity(db, { id: ENT_B1, orgId: ORG_B })
  })

  describe('createContact / getContact', () => {
    it('stores every field, including role, and reads the row back by id', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, {
        name: 'Dana Reyes',
        email: 'dana@example.com',
        phone: '602-555-0100',
        title: 'Owner',
        role: 'decision_maker',
      })
      expect(created).toMatchObject({
        org_id: ORG_A,
        entity_id: ENT_A1,
        name: 'Dana Reyes',
        email: 'dana@example.com',
        phone: '602-555-0100',
        title: 'Owner',
        role: 'decision_maker',
      })
      expect(created.id).toMatch(/^[0-9a-f-]{36}$/)
      expect(await getContact(db, ORG_A, created.id)).toEqual(created)
    })

    it('omitted optional fields are stored as NULL, not the string "undefined"', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Only Name' })
      expect(created.email).toBeNull()
      expect(created.phone).toBeNull()
      expect(created.title).toBeNull()
      expect(created.role).toBeNull()
    })

    it('org isolation: another org cannot read the contact by id', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Private' })
      expect(await getContact(db, ORG_B, created.id)).toBeNull()
    })
  })

  describe('listContacts', () => {
    it('lists the entity contacts alphabetically by name and nothing else', async () => {
      await createContact(db, ORG_A, ENT_A1, { name: 'Zed' })
      await createContact(db, ORG_A, ENT_A1, { name: 'Amy' })
      await createContact(db, ORG_A, ENT_A2, { name: 'Other Entity' })
      await createContact(db, ORG_B, ENT_B1, { name: 'Other Org' })

      const rows = await listContacts(db, ORG_A, ENT_A1)
      expect(rows.map((r) => r.name)).toEqual(['Amy', 'Zed'])
    })

    it('org isolation: the right entity under the wrong org lists nothing', async () => {
      await createContact(db, ORG_A, ENT_A1, { name: 'Amy' })
      expect(await listContacts(db, ORG_B, ENT_A1)).toEqual([])
    })
  })

  describe('updateContact', () => {
    it('changes only the fields given; an explicit null clears, an omitted field stays', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, {
        name: 'Dana',
        email: 'dana@example.com',
        title: 'Owner',
        role: 'decision_maker',
      })
      const updated = await updateContact(db, ORG_A, created.id, {
        email: null,
        role: 'champion',
      })
      expect(updated).toMatchObject({
        name: 'Dana',
        email: null,
        title: 'Owner',
        role: 'champion',
      })
    })

    it('no fields returns the existing row unchanged', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Dana' })
      expect(await updateContact(db, ORG_A, created.id, {})).toEqual(created)
    })

    it('org isolation: a write scoped to the wrong org changes nothing and returns null', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Dana' })
      expect(await updateContact(db, ORG_B, created.id, { name: 'Forged' })).toBeNull()
      expect((await getContact(db, ORG_A, created.id))?.name).toBe('Dana')
    })
  })

  describe('deleteContact', () => {
    it('hard-deletes the row and reports whether one was found', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Dana' })
      expect(await deleteContact(db, ORG_A, created.id)).toBe(true)
      expect(await getContact(db, ORG_A, created.id)).toBeNull()
      expect(await deleteContact(db, ORG_A, created.id)).toBe(false)
    })

    it('org isolation: the wrong org cannot delete, and the row survives', async () => {
      const created = await createContact(db, ORG_A, ENT_A1, { name: 'Dana' })
      expect(await deleteContact(db, ORG_B, created.id)).toBe(false)
      expect(await getContact(db, ORG_A, created.id)).not.toBeNull()
    })
  })

  describe('getFirstContactWithEmailForEntities', () => {
    it('picks the alphabetically first contact WITH an email per entity, skipping blanks', async () => {
      await createContact(db, ORG_A, ENT_A1, { name: 'Aaron', email: '' })
      await createContact(db, ORG_A, ENT_A1, { name: 'Bea', email: null })
      const chosen = await createContact(db, ORG_A, ENT_A1, {
        name: 'Cal',
        email: 'cal@example.com',
      })
      await createContact(db, ORG_A, ENT_A1, { name: 'Dee', email: 'dee@example.com' })
      await createContact(db, ORG_A, ENT_A2, { name: 'No Email' })

      const map = await getFirstContactWithEmailForEntities(db, ORG_A, [ENT_A1, ENT_A2])
      expect(map.get(ENT_A1)?.id).toBe(chosen.id)
      expect(map.has(ENT_A2)).toBe(false)
    })

    it('an empty id list returns an empty map', async () => {
      expect((await getFirstContactWithEmailForEntities(db, ORG_A, [])).size).toBe(0)
    })

    it('org isolation: another org entity never appears even when its id is asked for', async () => {
      await createContact(db, ORG_B, ENT_B1, { name: 'B', email: 'b@example.com' })
      const map = await getFirstContactWithEmailForEntities(db, ORG_A, [ENT_B1])
      expect(map.size).toBe(0)
    })
  })
})

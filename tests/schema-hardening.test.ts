import { beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import { resolve } from 'path'
import type { D1Database } from '@cloudflare/workers-types'

const migrationsDir = resolve(process.cwd(), 'migrations')

interface TableColumn {
  name: string
  type: string
  notnull: number
  dflt_value: string | null
}

interface ForeignKeyRow {
  table: string
  from: string
  to: string
}

interface IndexRow {
  name: string
}

describe('schema hardening migration 0027', () => {
  let db: D1Database

  beforeEach(async () => {
    db = createTestD1()
    await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  })

  it('binds magic links to org_id and user_id', async () => {
    const columns = await db.prepare(`PRAGMA table_info('magic_links')`).all<TableColumn>()
    const fks = await db.prepare(`PRAGMA foreign_key_list('magic_links')`).all<ForeignKeyRow>()
    const indexes = await db.prepare(`PRAGMA index_list('magic_links')`).all<IndexRow>()

    const columnMap = new Map(columns.results.map((column) => [column.name, column]))
    expect(columnMap.get('org_id')?.notnull).toBe(1)
    expect(columnMap.get('user_id')?.notnull).toBe(1)
    expect(columnMap.get('email')?.notnull).toBe(1)

    expect(
      fks.results.some(
        (fk) => fk.table === 'organizations' && fk.from === 'org_id' && fk.to === 'id'
      )
    ).toBe(true)
    expect(
      fks.results.some((fk) => fk.table === 'users' && fk.from === 'user_id' && fk.to === 'id')
    ).toBe(true)

    const indexNames = indexes.results.map((index) => index.name)
    expect(indexNames).toContain('idx_magic_links_org_email')
    expect(indexNames).toContain('idx_magic_links_expires')
    expect(indexNames).toContain('idx_magic_links_user_expires')
  })

  it('restores the context foreign key to entities', async () => {
    const fks = await db.prepare(`PRAGMA foreign_key_list('context')`).all<ForeignKeyRow>()

    expect(
      fks.results.some((fk) => fk.table === 'entities' && fk.from === 'entity_id' && fk.to === 'id')
    ).toBe(true)
    expect(
      fks.results.some(
        (fk) => fk.table === 'organizations' && fk.from === 'org_id' && fk.to === 'id'
      )
    ).toBe(true)
  })

  it('hardens milestones.org_id with a real FK and no sentinel default', async () => {
    const columns = await db.prepare(`PRAGMA table_info('milestones')`).all<TableColumn>()
    const fks = await db.prepare(`PRAGMA foreign_key_list('milestones')`).all<ForeignKeyRow>()
    const indexes = await db.prepare(`PRAGMA index_list('milestones')`).all<IndexRow>()

    const orgColumn = columns.results.find((column) => column.name === 'org_id')
    expect(orgColumn).toBeDefined()
    expect(orgColumn?.notnull).toBe(1)
    expect(orgColumn?.dflt_value).toBeNull()

    expect(
      fks.results.some(
        (fk) => fk.table === 'organizations' && fk.from === 'org_id' && fk.to === 'id'
      )
    ).toBe(true)

    const indexNames = indexes.results.map((index) => index.name)
    expect(indexNames).toContain('idx_milestones_org_engagement_order')
    expect(indexNames).toContain('idx_milestones_org_status')
  })
})

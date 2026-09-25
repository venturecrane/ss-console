import { describe, expect, it } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { requireAdminSession } from '../src/lib/auth/admin-session'

function makeLocals(session: App.Locals['session']): Pick<App.Locals, 'session'> {
  return { session }
}

function adminApiFiles(dir = resolve('src/pages/api/admin')): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const file = join(dir, entry)
    if (statSync(file).isDirectory()) {
      return adminApiFiles(file)
    }
    return file.endsWith('.ts') ? [file] : []
  })
}

describe('auth: admin session helper', () => {
  it('returns a JSON 401 for unauthenticated requests', async () => {
    const result = requireAdminSession(makeLocals(null))

    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('expected unauthorized result')
    expect(result.response.status).toBe(401)
    expect(result.response.headers.get('Content-Type')).toBe('application/json')
    await expect(result.response.json()).resolves.toEqual({
      error: 'unauthorized',
      message: 'Unauthorized.',
    })
  })

  it('returns a JSON 401 for non-admin requests', async () => {
    const result = requireAdminSession(
      makeLocals({
        userId: 'client-user',
        orgId: 'client-org',
        role: 'client',
        email: 'client@example.com',
        expiresAt: '2026-07-08T00:00:00.000Z',
      })
    )

    expect(result.ok).toBe(false)
    if (result.ok) throw new Error('expected unauthorized result')
    expect(result.response.status).toBe(401)
    await expect(result.response.json()).resolves.toEqual({
      error: 'unauthorized',
      message: 'Unauthorized.',
    })
  })

  it('narrows admin sessions for route handlers', () => {
    const result = requireAdminSession(
      makeLocals({
        userId: 'admin-user',
        orgId: 'admin-org',
        role: 'admin',
        email: 'admin@example.com',
        expiresAt: '2026-07-08T00:00:00.000Z',
      })
    )

    expect(result.ok).toBe(true)
    if (!result.ok) throw new Error('expected admin session')
    expect(result.session).toEqual({
      userId: 'admin-user',
      orgId: 'admin-org',
      role: 'admin',
      email: 'admin@example.com',
      expiresAt: '2026-07-08T00:00:00.000Z',
    })
  })

  it('keeps admin API routes on the shared guard', () => {
    const offenders = adminApiFiles()
      .map((file) => ({
        file,
        source: readFileSync(file, 'utf-8'),
      }))
      .filter(({ source }) => {
        return (
          source.includes('const session = locals.session') ||
          source.includes('const session = ctx.locals.session') ||
          source.includes("session.role !== 'admin'")
        )
      })
      .map(({ file }) => relative(process.cwd(), file))

    expect(offenders).toEqual([])
  })
})

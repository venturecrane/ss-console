import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import type { APIContext } from 'astro'
import { env as testEnv } from 'cloudflare:workers'

const { wrapSpy } = vi.hoisted(() => ({
  wrapSpy: vi.fn(async (_opts: unknown, h: () => Promise<Response>) => h()),
}))

vi.mock('@sentry/cloudflare', () => ({
  wrapRequestHandler: wrapSpy,
}))

import { withSentryRequestHandler } from '../src/lib/observability/sentry'

function resetEnv(): void {
  for (const key of Object.keys(testEnv)) delete testEnv[key as keyof typeof testEnv]
}

function makeApiContext(): APIContext {
  return {
    request: new Request('https://smd.services/'),
    locals: { cfContext: undefined, session: null },
  } as unknown as APIContext
}

describe('observability/sentry: gating', () => {
  beforeEach(() => {
    resetEnv()
    wrapSpy.mockClear()
  })

  afterEach(() => {
    resetEnv()
  })

  it('returns the bare handler result when SENTRY_DSN is unset (no-op)', async () => {
    const ctx = makeApiContext()
    const handler = vi.fn<() => Promise<Response>>().mockResolvedValue(new Response('ok'))

    const response = await withSentryRequestHandler(ctx, handler)

    expect(handler).toHaveBeenCalledTimes(1)
    expect(wrapSpy).not.toHaveBeenCalled()
    expect(await response.text()).toBe('ok')
  })

  it('delegates to wrapRequestHandler when SENTRY_DSN is set', async () => {
    Object.assign(testEnv, { SENTRY_DSN: 'https://example@o0.ingest.sentry.io/0' })
    const ctx = makeApiContext()
    const handler = vi.fn<() => Promise<Response>>().mockResolvedValue(new Response('wrapped'))

    const response = await withSentryRequestHandler(ctx, handler)

    expect(wrapSpy).toHaveBeenCalledTimes(1)
    const [opts] = wrapSpy.mock.calls[0]
    expect((opts as { options: { dsn: string } }).options.dsn).toBe(
      'https://example@o0.ingest.sentry.io/0'
    )
    expect(handler).toHaveBeenCalledTimes(1)
    expect(await response.text()).toBe('wrapped')
  })
})

describe('observability/sentry: source-level guarantees', () => {
  const source = readFileSync(resolve('src/lib/observability/sentry.ts'), 'utf-8')

  it('reads DSN from env, never hardcodes a fallback', () => {
    expect(source).toContain('env.SENTRY_DSN')
    expect(source).not.toMatch(/dsn:\s*['"]https:\/\//i)
  })
})

/**
 * Tests for the production console→Machine runtime read transport
 * (src/lib/operator/runtime-read-transport.ts) — ADR 0043 path A.
 *
 * Covers: the configured-gate truth table; the per-customer HMAC key derivation
 * (incl. a CROSS-SIDE match against the shell derivation the provision script
 * uses — the single most likely silent fail-closed cause); and the transport's
 * success / unauthorized / unreachable / not-configured / unknown-customer
 * behaviour, both directly and end-to-end through readMachineRuntime.
 */

import { describe, it, expect, vi, afterEach } from 'vitest'
import { scriptDerivation, runDerivation, provisionScriptSource } from './_support/provision-script'
import {
  createMachineWebhookTransport,
  type HandoffEnvelope,
} from '../src/lib/operator/mcp/webhook-transport'
import {
  createMachineRuntimeTransport,
  deriveRuntimeReadKey,
  isRuntimeReadConfigured,
  RuntimeReadNotConfiguredError,
  type RuntimeReadEnv,
} from '../src/lib/operator/runtime-read-transport'
import { readMachineRuntime, RuntimeReadUnauthorizedError } from '../src/lib/operator/runtime-read'

const ENV: RuntimeReadEnv = {
  OPERATOR_RUNTIME_READ_URL: 'https://{app}.fly.dev',
  OPERATOR_RUNTIME_READ_SECRET: 'test-master-key-1234567890',
}
const ACTOR = { actor: 'captain@example.com', actorRole: 'admin' }
const noopAudit = { record: () => Promise.resolve() }

afterEach(() => {
  vi.unstubAllGlobals()
})

function stubFetch(impl: (url: string, init: RequestInit) => Promise<Response> | Response): void {
  vi.stubGlobal('fetch', vi.fn(impl as unknown as typeof fetch))
}

// ---------------------------------------------------------------------------
// Config gate
// ---------------------------------------------------------------------------

describe('isRuntimeReadConfigured', () => {
  it('requires BOTH url and secret', () => {
    expect(isRuntimeReadConfigured(ENV)).toBe(true)
    expect(isRuntimeReadConfigured({ OPERATOR_RUNTIME_READ_URL: 'x' })).toBe(false)
    expect(isRuntimeReadConfigured({ OPERATOR_RUNTIME_READ_SECRET: 'x' })).toBe(false)
    expect(isRuntimeReadConfigured({})).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Key derivation + cross-side match
// ---------------------------------------------------------------------------

describe('deriveRuntimeReadKey', () => {
  it('is deterministic and produces a 64-hex key', async () => {
    const a = await deriveRuntimeReadKey('master', 'smd-staging')
    const b = await deriveRuntimeReadKey('master', 'smd-staging')
    expect(a).toBe(b)
    expect(a).toMatch(/^[0-9a-f]{64}$/)
    expect(await deriveRuntimeReadKey('master', 'other')).not.toBe(a)
  })

  it('matches the shell derivation the provision script uses (cross-side)', async () => {
    // This is the byte-identity check Captain flagged: the console derives over
    // the slug via WebCrypto; provision derives via openssl. They MUST agree or
    // every read silently 401s and the drill-in renders empty. Canonical input:
    // the slug string with NO trailing newline (printf %s).
    //
    // The pipeline below is READ OUT OF provision-customer.sh, not transcribed
    // here — see tests/_support/provision-script.ts for why (ss#2313). Editing
    // the script's derivation changes what this test executes, so drift is red.
    const master = 'test-master-key-1234567890'
    const slug = 'smith-pi-firm'

    const pipeline = scriptDerivation('_rt_key')
    expect(pipeline, 'the runtime-read derivation must still hash the slug').toContain('${SLUG}')
    expect(
      pipeline,
      'the runtime-read derivation must still key on OPERATOR_RUNTIME_READ_SECRET'
    ).toContain('${OPERATOR_RUNTIME_READ_SECRET}')

    const shell = runDerivation(pipeline, { SLUG: slug, OPERATOR_RUNTIME_READ_SECRET: master })
    expect(shell).toMatch(/^[0-9a-f]{64}$/)
    await expect(deriveRuntimeReadKey(master, slug)).resolves.toBe(shell)
  })

  it('provision stages the derived runtime-read value under the key the Machine reads', () => {
    // Deriving correctly and staging the wrong variable is the same outage.
    expect(provisionScriptSource()).toContain(
      'stage_secret_from_env OPERATOR_RUNTIME_READ_KEY "${_rt_key}"'
    )
  })
})

// ---------------------------------------------------------------------------
// MCP handoff master — cross-side parity
// ---------------------------------------------------------------------------

/**
 * The MCP webhook master had ZERO cross-side coverage before ss#2313. It uses a
 * DIFFERENT master (`OPERATOR_MCP_WEBHOOK_SECRET`) through the SAME derivation,
 * staged by a separate block in provision-customer.sh — so the runtime-read
 * parity check above could not observe a drift here.
 *
 * This exercises the real transport rather than `deriveRuntimeReadKey` directly:
 * what must match the Machine's `WEBHOOK_SECRET_MCP` is the bearer that actually
 * goes on the wire, not an intermediate the transport might stop calling.
 */
describe('MCP handoff bearer (cross-side)', () => {
  const ENVELOPE: HandoffEnvelope = {
    handoff_id: 'h-1',
    surface: 'mcp',
    trust_class: 'known_external',
    task: 'summarize the intake',
    from_email: 'captain@example.com',
    from_profile: 'operator',
    submitted_at: '2026-08-12T00:00:00Z',
  }

  it('matches the shell derivation the provision script uses', async () => {
    const master = 'test-mcp-master-key-0987654321'
    const slug = 'smd-staging'

    const pipeline = scriptDerivation('_mcp_key')
    expect(pipeline, 'the MCP handoff derivation must still hash the slug').toContain('${SLUG}')
    expect(
      pipeline,
      'the MCP handoff derivation must still key on OPERATOR_MCP_WEBHOOK_SECRET'
    ).toContain('${OPERATOR_MCP_WEBHOOK_SECRET}')

    const shell = runDerivation(pipeline, { SLUG: slug, OPERATOR_MCP_WEBHOOK_SECRET: master })
    expect(shell).toMatch(/^[0-9a-f]{64}$/)

    let seenAuth = ''
    stubFetch((_url, init) => {
      seenAuth = ((init.headers as Record<string, string>) ?? {})['Authorization'] ?? ''
      return new Response('', { status: 200 })
    })
    const transport = createMachineWebhookTransport({
      OPERATOR_RUNTIME_READ_URL: 'https://{app}.fly.dev',
      OPERATOR_MCP_WEBHOOK_SECRET: master,
    })
    await transport.send(slug, ENVELOPE)

    expect(seenAuth).toBe(`Bearer ${shell}`)
  })

  it('provision stages the derived value under BOTH keys the Machine reads', () => {
    // The gate verifies WEBHOOK_SECRET_MCP; the internal Hermes adapter
    // re-verifies the forwarded hop with WEBHOOK_SECRET_HANDOFF. Staging only
    // one leaves the handoff failing at the second hop, after a 2xx ack.
    const src = provisionScriptSource()
    expect(src).toContain('stage_secret_from_env WEBHOOK_SECRET_MCP     "${_mcp_key}"')
    expect(src).toContain('stage_secret_from_env WEBHOOK_SECRET_HANDOFF "${_mcp_key}"')
  })
})

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

describe('createMachineRuntimeTransport.read', () => {
  it('GETs the registry-resolved Machine with the derived bearer + tenant slug', async () => {
    let seenUrl = ''
    let seenHeaders: Record<string, string> = {}
    stubFetch((url, init) => {
      seenUrl = url
      seenHeaders = (init.headers as Record<string, string>) ?? {}
      return new Response(JSON.stringify({ entries: [], cursor: null }), { status: 200 })
    })
    const t = createMachineRuntimeTransport(ENV)
    const { data } = await t.read('smd-staging', { kind: 'audit_log', limit: 25 })
    expect(seenUrl).toBe('https://hermes-smd-staging.fly.dev/runtime/audit_log?limit=25')
    expect(seenHeaders['X-Tenant-Slug']).toBe('smd-staging')
    expect(seenHeaders['Authorization']).toBe(
      `Bearer ${await deriveRuntimeReadKey(ENV.OPERATOR_RUNTIME_READ_SECRET!, 'smd-staging')}`
    )
    expect(data).toEqual({ entries: [], cursor: null })
  })

  it('throws RuntimeReadUnauthorizedError on 401/403 (→ unauthorized e2e)', async () => {
    stubFetch(() => new Response('', { status: 401 }))
    const t = createMachineRuntimeTransport(ENV)
    await expect(t.read('smd-staging', { kind: 'audit_log' })).rejects.toBeInstanceOf(
      RuntimeReadUnauthorizedError
    )
    // End-to-end: readMachineRuntime maps it to reason 'unauthorized', not 'unreachable'.
    const res = await readMachineRuntime(
      { transport: t, audit: noopAudit },
      'smd-staging',
      { kind: 'audit_log' },
      ACTOR
    )
    expect(res).toEqual({ ok: false, kind: 'audit_log', reason: 'unauthorized' })
  })

  it('throws on a non-2xx (→ unreachable e2e, fail-closed)', async () => {
    stubFetch(() => new Response('', { status: 500 }))
    const t = createMachineRuntimeTransport(ENV)
    const res = await readMachineRuntime(
      { transport: t, audit: noopAudit },
      'smd-staging',
      { kind: 'audit_log' },
      ACTOR
    )
    expect(res).toEqual({ ok: false, kind: 'audit_log', reason: 'unreachable' })
  })

  it('throws on a network failure (→ unreachable)', async () => {
    stubFetch(() => Promise.reject(new Error('ECONNREFUSED')))
    const t = createMachineRuntimeTransport(ENV)
    await expect(t.read('smd-staging', { kind: 'audit_log' })).rejects.toThrow()
  })

  it('throws RuntimeReadNotConfiguredError when not configured', async () => {
    const t = createMachineRuntimeTransport({})
    await expect(t.read('smd-staging', { kind: 'audit_log' })).rejects.toBeInstanceOf(
      RuntimeReadNotConfiguredError
    )
  })

  it('throws (→ unreachable) for a customer absent from the registry', async () => {
    stubFetch(() => new Response('{}', { status: 200 }))
    const t = createMachineRuntimeTransport(ENV)
    await expect(t.read('ghost-firm', { kind: 'audit_log' })).rejects.toThrow(/unknown customer/)
    // A registry miss is a per-call failure, NOT a config failure.
    expect(isRuntimeReadConfigured(ENV)).toBe(true)
  })

  it('honors a host template without {app} by falling back to <app>.fly.dev', async () => {
    let seenUrl = ''
    stubFetch((url) => {
      seenUrl = url
      return new Response(JSON.stringify({ entries: [] }), { status: 200 })
    })
    const t = createMachineRuntimeTransport({
      OPERATOR_RUNTIME_READ_URL: 'enabled',
      OPERATOR_RUNTIME_READ_SECRET: 'm',
    })
    await t.read('smd-staging', { kind: 'audit_log' })
    expect(seenUrl).toBe('https://hermes-smd-staging.fly.dev/runtime/audit_log')
  })
})

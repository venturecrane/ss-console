/**
 * The durable-ledger write in customer-yaml-update must fail LOUDLY
 * (2026-08-14 code review, Code Quality #3): when the
 * portal_action_events insert throws, the failure reaches Sentry via
 * captureError — not just a console line — while the request still
 * redirects with its honest status (the tail-log compliance sink has
 * already recorded the event).
 *
 * The route runs real: form parse, projection reconstruction from a live
 * seat's customer.yaml (same path advanced-settings-surface.test.ts pins),
 * validation, merge, redirect. Faked seams: authorizeAdvancedSettings
 * (auth context), getCustomerConfigBySlug (serves the real projected row),
 * recordPortalActionEvent (forced throw), captureError (spy).
 */

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parse as parseYaml } from 'yaml'

import { validate } from '../src/lib/operator/customer-yaml'
import { projectCustomerYamlToConfigRow } from '../src/lib/portal/customer-config-projection'
import { projectRow } from '../src/lib/portal/customer-config'

const authorizeAdvancedSettings = vi.fn()
vi.mock('../src/lib/portal/operator/advanced-settings-auth', () => ({
  authorizeAdvancedSettings: (...args: unknown[]) => authorizeAdvancedSettings(...args),
}))

const getCustomerConfigBySlug = vi.fn()
vi.mock('../src/lib/portal/customer-config', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/lib/portal/customer-config')>()
  return {
    ...actual,
    getCustomerConfigBySlug: (...args: unknown[]) => getCustomerConfigBySlug(...args),
  }
})

const recordPortalActionEvent = vi.fn()
vi.mock('../src/lib/portal/operator/action-events', () => ({
  recordPortalActionEvent: (...args: unknown[]) => recordPortalActionEvent(...args),
}))

const captureError = vi.fn()
vi.mock('../src/lib/observability/sentry', () => ({
  captureError: (...args: unknown[]) => captureError(...args),
}))

// Import AFTER the mocks so the route binds them.
import { POST } from '../src/pages/api/portal/operator/settings/customer-yaml-update'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SLUG = 'pilot-smokeball'

/** git customer.yaml → the D1 row CI writes → the row shape the portal reads. */
function projectionForPilot(): ReturnType<typeof projectRow> {
  const raw: unknown = parseYaml(
    readFileSync(join(REPO_ROOT, 'operator', 'customers', SLUG, 'customer.yaml'), 'utf8')
  )
  const result = validate(raw)
  if (!result.ok) throw new Error(`${SLUG}/customer.yaml does not validate`)
  const dbRow = projectCustomerYamlToConfigRow(
    result.value,
    { entityId: 'ent_test', orgId: 'org_test', gitSha: 'test', syncedAt: '2026-07-31T00:00:00Z' },
    null
  )
  return projectRow(dbRow)
}

async function submit(): Promise<Response> {
  const form = new FormData()
  form.set('instance', SLUG)
  const request = new Request(
    'https://portal.smd.services/api/portal/operator/settings/customer-yaml-update',
    { method: 'POST', body: form }
  )
  return POST({
    request,
    locals: {} as App.Locals,
  } as unknown as Parameters<typeof POST>[0])
}

describe('customer-yaml-update: ledger-write failure is loud', () => {
  beforeEach(() => {
    authorizeAdvancedSettings.mockReset()
    authorizeAdvancedSettings.mockResolvedValue({
      userId: 'u-principal',
      userEmail: 'principal@example.com',
      entityId: 'ent_test',
      customerSlug: SLUG,
    })
    getCustomerConfigBySlug.mockReset()
    getCustomerConfigBySlug.mockResolvedValue(projectionForPilot())
    recordPortalActionEvent.mockReset()
    captureError.mockReset()
  })

  it('captures to Sentry when the portal_action_events insert throws, and still redirects honestly', async () => {
    recordPortalActionEvent.mockRejectedValue(new Error('D1 unavailable'))

    const res = await submit()

    // An empty form takes the validation-rejected path, which runs the SAME
    // shared emitAudit ("the attempt is itself a recorded compliance
    // event"). The client still gets the honest redirect — the tail-log
    // sink already carried the event for the compliance drain.
    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toContain('status=invalid')

    // ...but the lost primary record reached Sentry, tagged to this surface.
    expect(captureError).toHaveBeenCalledTimes(1)
    expect(captureError).toHaveBeenCalledWith(
      expect.any(Error),
      'portal.customer-yaml-update.audit'
    )
  })

  it('does not capture when the ledger write succeeds', async () => {
    recordPortalActionEvent.mockResolvedValue(undefined)

    const res = await submit()

    expect(res.status).toBe(303)
    expect(res.headers.get('Location')).toContain('status=invalid')
    expect(recordPortalActionEvent).toHaveBeenCalledTimes(1)
    expect(captureError).not.toHaveBeenCalled()
  })
})

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { env as testEnv } from 'cloudflare:workers'
import { GET } from '../src/pages/api/portal/consultants/photo/[...key]'
import { listEngagements } from '../src/lib/db/engagements'
import { getPortalClient, type PortalContext } from '../src/lib/portal/session'
import type { Engagement } from '../src/lib/db/engagements'

vi.mock('../src/lib/portal/session', () => ({
  getPortalClient: vi.fn(),
}))

vi.mock('../src/lib/db/engagements', () => ({
  listEngagements: vi.fn(),
}))

const ORG_ID = 'org-photo'
const CLIENT_ID = 'client-photo'
const ENGAGEMENT_ID = 'engagement-photo'
const PHOTO_KEY = `${ORG_ID}/engagements/${ENGAGEMENT_ID}/consultant/photo.webp`

function portalContext(clientId: string | null = CLIENT_ID): PortalContext {
  return {
    user: {
      id: 'user-photo',
      org_id: ORG_ID,
      email: 'client@example.com',
      name: 'Client User',
      role: 'client',
      entity_id: clientId,
      clerk_user_id: 'clerk-user-photo',
    },
    client: clientId
      ? {
          id: clientId,
          org_id: ORG_ID,
          name: 'Client Photo LLC',
          slug: 'client-photo',
          stage: 'client',
        }
      : null,
  } as PortalContext
}

function engagement(id: string = ENGAGEMENT_ID): Engagement {
  return {
    id,
    org_id: ORG_ID,
    entity_id: CLIENT_ID,
    quote_id: 'quote-photo',
    service_id: null,
    scope_summary: null,
    start_date: null,
    estimated_end: null,
    actual_end: null,
    handoff_date: null,
    safety_net_end: null,
    status: 'active',
    estimated_hours: null,
    actual_hours: 0,
    consultant_name: null,
    consultant_photo_url: null,
    consultant_role: null,
    consultant_phone: null,
    next_touchpoint_at: null,
    next_touchpoint_label: null,
    originating_signal_id: null,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
  }
}

function buildContext(key: string): Parameters<typeof GET>[0] {
  return {
    params: { key },
    locals: {
      auth: () => ({ userId: 'clerk-user-photo', orgId: 'clerk-org-photo' }),
      currentUser: async () => null,
    },
  } as unknown as Parameters<typeof GET>[0]
}

function installPhotoBucket(object: unknown): void {
  Object.assign(testEnv, {
    DB: { prepare: vi.fn() },
    CONSULTANT_PHOTOS: {
      get: vi.fn().mockResolvedValue(object),
    },
  })
}

async function readJson(res: Response): Promise<{ error: string; message: string }> {
  return await res.json()
}

describe('GET /api/portal/consultants/photo/[...key]', () => {
  beforeEach(() => {
    vi.mocked(getPortalClient).mockReset()
    vi.mocked(listEngagements).mockReset()
    for (const key of Object.keys(testEnv)) {
      delete (testEnv as unknown as Record<string, unknown>)[key]
    }
    installPhotoBucket({
      body: 'image-bytes',
      httpMetadata: { contentType: 'image/webp' },
    })
  })

  it('streams a Clerk-authenticated client photo without a legacy session', async () => {
    vi.mocked(getPortalClient).mockResolvedValue(portalContext())
    vi.mocked(listEngagements).mockResolvedValue([engagement()])

    const res = await GET(buildContext(PHOTO_KEY))

    expect(res.status).toBe(200)
    expect(res.headers.get('Content-Type')).toBe('image/webp')
    expect(res.headers.get('Cache-Control')).toBe('private, max-age=3600')
    expect(await res.text()).toBe('image-bytes')
    expect(getPortalClient).toHaveBeenCalledWith(
      testEnv.DB,
      expect.objectContaining({ auth: expect.any(Function) })
    )
    expect(listEngagements).toHaveBeenCalledWith(testEnv.DB, ORG_ID, CLIENT_ID)
  })

  it('returns 401 when there is no authenticated portal client', async () => {
    vi.mocked(getPortalClient).mockResolvedValue(null)

    const res = await GET(buildContext(PHOTO_KEY))

    expect(res.status).toBe(401)
    expect(await readJson(res)).toEqual({ error: 'unauthorized', message: 'Unauthorized.' })
    expect(listEngagements).not.toHaveBeenCalled()
  })

  it('returns 403 when the Clerk user is not bound to a client', async () => {
    vi.mocked(getPortalClient).mockResolvedValue(portalContext(null))

    const res = await GET(buildContext(PHOTO_KEY))

    expect(res.status).toBe(403)
    expect(await readJson(res)).toEqual({ error: 'forbidden', message: 'Forbidden.' })
    expect(listEngagements).not.toHaveBeenCalled()
  })

  it('returns 403 when the key targets a different org or engagement', async () => {
    vi.mocked(getPortalClient).mockResolvedValue(portalContext())
    vi.mocked(listEngagements).mockResolvedValue([engagement('different-engagement')])

    const res = await GET(buildContext(PHOTO_KEY))

    expect(res.status).toBe(403)
    expect(await readJson(res)).toEqual({ error: 'forbidden', message: 'Forbidden.' })
    expect(testEnv.CONSULTANT_PHOTOS.get).not.toHaveBeenCalled()
  })

  it('returns 404 when the authorized photo object is missing', async () => {
    installPhotoBucket(null)
    vi.mocked(getPortalClient).mockResolvedValue(portalContext())
    vi.mocked(listEngagements).mockResolvedValue([engagement()])

    const res = await GET(buildContext(PHOTO_KEY))

    expect(res.status).toBe(404)
    expect(await readJson(res)).toEqual({ error: 'not_found', message: 'Not found.' })
    expect(testEnv.CONSULTANT_PHOTOS.get).toHaveBeenCalledWith(PHOTO_KEY)
  })
})

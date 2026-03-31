import type { APIRoute } from 'astro'

/**
 * Health check endpoint — verifies SSR and Cloudflare bindings are available.
 *
 * Also serves as a D1 pre-warm route (PRD Risk 9 mitigation).
 * GET /api/health
 */
export const GET: APIRoute = async ({ locals }) => {
  const env = locals.runtime.env

  const bindings = {
    db: typeof env.DB !== 'undefined',
    storage: typeof env.STORAGE !== 'undefined',
    sessions: typeof env.SESSIONS !== 'undefined',
  }

  return new Response(
    JSON.stringify({
      status: 'ok',
      bindings,
      timestamp: new Date().toISOString(),
    }),
    {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }
  )
}

/**
 * Social Listening Worker — Pipeline 4
 *
 * Cloudflare Worker cron job that monitors Reddit for conversations where
 * business owners signal operational pain, deduplicates against D1, and
 * sends a daily email digest for human review.
 *
 * Schedule: Daily at 7:00 AM MST (14:00 UTC)
 * Trigger: Also via POST /run with Authorization: Bearer <LEAD_INGEST_API_KEY>
 * Flow: Reddit search → D1 dedup → Resend digest
 *
 * Key difference from other pipelines: No Claude AI qualification.
 * Pure discovery + routing. Keeps costs at $0 (beyond Resend).
 */

import { ORG_ID, SYSTEM_ENTITY_ID } from '../../../src/lib/constants.js'
import { appendContext } from '../../../src/lib/db/context.js'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Env {
  DB: D1Database
  REDDIT_CLIENT_ID: string
  REDDIT_CLIENT_SECRET: string
  REDDIT_USERNAME: string
  REDDIT_PASSWORD: string
  RESEND_API_KEY: string
  LEAD_INGEST_API_KEY: string
}

interface RedditPost {
  id: string
  title: string
  subreddit: string
  url: string
  selftext: string
  score: number
  created_utc: number
}

interface RedditSearchResponse {
  data: {
    children: Array<{
      data: {
        id: string
        title: string
        subreddit: string
        permalink: string
        selftext?: string
        score: number
        created_utc: number
      }
    }>
  }
}

interface RunSummary {
  queries: number
  totalPosts: number
  newPosts: number
  duplicates: number
  stored: number
  errors: number
  errorDetails: string[]
}

// ---------------------------------------------------------------------------
// Reddit API
// ---------------------------------------------------------------------------

const USER_AGENT = 'smd-services-social-listener/1.0'

const QUERIES = [
  'small business Phoenix operations',
  'business owner overwhelmed scheduling',
  'CRM recommendation small business',
  'hiring office manager Phoenix',
  'small business spreadsheet chaos',
]

async function getRedditToken(env: Env): Promise<string> {
  const auth = btoa(`${env.REDDIT_CLIENT_ID}:${env.REDDIT_CLIENT_SECRET}`)
  const res = await fetch('https://www.reddit.com/api/v1/access_token', {
    method: 'POST',
    headers: {
      Authorization: `Basic ${auth}`,
      'Content-Type': 'application/x-www-form-urlencoded',
      'User-Agent': USER_AGENT,
    },
    body: `grant_type=password&username=${encodeURIComponent(env.REDDIT_USERNAME)}&password=${encodeURIComponent(env.REDDIT_PASSWORD)}`,
  })

  if (!res.ok) {
    throw new Error(`Reddit OAuth failed: ${res.status} ${res.statusText}`)
  }

  const data = (await res.json()) as { access_token: string; error?: string }
  if (data.error) {
    throw new Error(`Reddit OAuth error: ${data.error}`)
  }
  return data.access_token
}

async function searchReddit(token: string, query: string): Promise<RedditPost[]> {
  const res = await fetch(
    `https://oauth.reddit.com/search?q=${encodeURIComponent(query)}&restrict_sr=false&type=link&t=day&limit=10&sort=new`,
    {
      headers: {
        Authorization: `Bearer ${token}`,
        'User-Agent': USER_AGENT,
      },
    }
  )

  if (!res.ok) {
    throw new Error(`Reddit search failed: ${res.status} ${res.statusText}`)
  }

  const data = (await res.json()) as RedditSearchResponse
  return data.data.children.map((c) => ({
    id: c.data.id,
    title: c.data.title,
    subreddit: c.data.subreddit,
    url: `https://reddit.com${c.data.permalink}`,
    selftext: c.data.selftext?.substring(0, 500) ?? '',
    score: c.data.score,
    created_utc: c.data.created_utc,
  }))
}

// ---------------------------------------------------------------------------
// Email
// ---------------------------------------------------------------------------

async function sendDigest(env: Env, posts: RedditPost[]): Promise<void> {
  if (posts.length === 0) return

  const date = new Date().toISOString().split('T')[0]
  const lines = posts.map(
    (p, i) =>
      `${i + 1}. [r/${p.subreddit}] ${p.title}\n   ${p.url}\n   "${p.selftext.substring(0, 200)}..."`
  )

  const body =
    `Social Listening Digest — ${date}\n\n` +
    `REDDIT (${posts.length} new posts)\n\n` +
    lines.join('\n\n') +
    `\n\nReview these conversations. Reply to relevant threads where SMD Services can add value.`

  const res = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.RESEND_API_KEY}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      from: 'SMD Services <notifications@smd.services>',
      to: ['scott@smd.services'],
      subject: `Social Digest: ${posts.length} new signals — ${date}`,
      text: body,
    }),
  })

  if (!res.ok) {
    console.error(`Resend digest failed: ${res.status}`)
  }
}

async function sendFailureAlert(summary: RunSummary, resendApiKey: string): Promise<void> {
  const body = [
    `Social Listening pipeline run completed with errors.`,
    ``,
    `Queries run: ${summary.queries}`,
    `Total posts found: ${summary.totalPosts}`,
    `New posts (not deduped): ${summary.newPosts}`,
    `Duplicates skipped: ${summary.duplicates}`,
    `Stored to D1: ${summary.stored}`,
    `Errors: ${summary.errors}`,
    ``,
    `Error details:`,
    ...summary.errorDetails.map((e) => `  - ${e}`),
  ].join('\n')

  const response = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${resendApiKey}`,
    },
    body: JSON.stringify({
      from: 'SMD Services <noreply@smd.services>',
      to: ['scott@smd.services'],
      subject: `[Social Listening] Pipeline run failed — ${summary.errors} errors`,
      text: body,
    }),
  })

  if (!response.ok) {
    console.error(`Resend alert failed: ${response.status}`)
  }
}

// ---------------------------------------------------------------------------
// Main run
// ---------------------------------------------------------------------------

async function run(env: Env): Promise<RunSummary> {
  const summary: RunSummary = {
    queries: 0,
    totalPosts: 0,
    newPosts: 0,
    duplicates: 0,
    stored: 0,
    errors: 0,
    errorDetails: [],
  }

  // Authenticate with Reddit
  let token: string
  try {
    token = await getRedditToken(env)
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err)
    summary.errors++
    summary.errorDetails.push(`Reddit auth: ${msg}`)
    console.error(`Fatal: Reddit auth failed — aborting run`)
    return summary
  }

  // Search across all queries, dedup by post ID within this run
  const seen = new Set<string>()
  const allPosts: RedditPost[] = []

  for (const query of QUERIES) {
    summary.queries++
    try {
      const posts = await searchReddit(token, query)
      for (const post of posts) {
        if (!seen.has(post.id)) {
          seen.add(post.id)
          allPosts.push(post)
        }
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      summary.errors++
      summary.errorDetails.push(`Query "${query}": ${msg}`)
    }
  }

  summary.totalPosts = allPosts.length
  console.log(`Reddit: ${summary.queries} queries, ${summary.totalPosts} unique posts`)

  // Dedup against D1, store new signals, collect for digest
  const digestPosts: RedditPost[] = []

  for (const post of allPosts) {
    try {
      const sourceRef = `reddit_${post.id}`
      const alreadyProcessed = await env.DB.prepare(
        `SELECT 1 FROM context WHERE org_id = ? AND source = 'social_listening' AND source_ref = ?`
      )
        .bind(ORG_ID, sourceRef)
        .first()

      if (alreadyProcessed) {
        summary.duplicates++
        continue
      }

      summary.newPosts++

      // Store as context entry against system entity (no entity creation for discovery)
      const content = `[r/${post.subreddit}] ${post.title}\n\n${post.selftext}`
      const metadata: Record<string, unknown> = {
        reddit_id: post.id,
        subreddit: post.subreddit,
        url: post.url,
        score: post.score,
        created_utc: post.created_utc,
        date_found: new Date().toISOString().split('T')[0],
      }

      await appendContext(env.DB, ORG_ID, {
        entity_id: SYSTEM_ENTITY_ID,
        type: 'signal',
        content,
        source: 'social_listening',
        source_ref: sourceRef,
        metadata,
      })

      summary.stored++
      digestPosts.push(post)
    } catch (err) {
      summary.errors++
      const msg = err instanceof Error ? err.message : String(err)
      summary.errorDetails.push(`Post "${post.id}": ${msg}`)
    }
  }

  console.log(
    `Run complete: ${summary.newPosts} new, ${summary.duplicates} duplicates, ` +
      `${summary.stored} stored, ${summary.errors} errors`
  )

  // Send digest email with new posts
  if (digestPosts.length > 0 && env.RESEND_API_KEY) {
    try {
      await sendDigest(env, digestPosts)
      console.log(`Digest sent: ${digestPosts.length} posts`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error(`Digest send failed: ${msg}`)
    }
  }

  return summary
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

export default {
  async scheduled(controller: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const summary = await run(env)
    if (summary.stored === 0 && summary.errors > 0 && env.RESEND_API_KEY) {
      ctx.waitUntil(sendFailureAlert(summary, env.RESEND_API_KEY))
    }
  },

  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const auth = request.headers.get('Authorization')
    if (auth !== `Bearer ${env.LEAD_INGEST_API_KEY}`) {
      return new Response('Unauthorized', { status: 401 })
    }
    const summary = await run(env)
    return new Response(JSON.stringify(summary, null, 2), {
      headers: { 'Content-Type': 'application/json' },
    })
  },
} satisfies ExportedHandler<Env>

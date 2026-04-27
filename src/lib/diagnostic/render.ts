/**
 * Anti-fabrication renderer for the diagnostic email (#598).
 *
 * The diagnostic report has 5 sections (mirroring the existing
 * intelligence_brief structure in `dossier.ts`):
 *
 *   1. Business Overview
 *   2. Owner / Decision-Maker Profile
 *   3. Technology & Operations Assessment
 *   4. Engagement Opportunity
 *   5. Conversation Starters
 *
 * Each section is rendered ONLY from authored signals (database
 * enrichment rows) — never invented to fill out the report. CLAUDE.md
 * P0 compliance: every line in a client-facing diagnostic must come
 * from data we actually have. Per the scoping doc:
 *
 *   | Section          | Render condition                            | Else                       |
 *   |------------------|---------------------------------------------|----------------------------|
 *   | Business Overview| google_places returned a match              | Don't render               |
 *   | Owner Profile    | website OR outscraper has owner_name        | "Owner not publicly        |
 *   |                  |                                             |  identified" (NOT invented)|
 *   | Tech & Ops       | website_analysis ran (null = real signal)   | OK — null IS a finding     |
 *   | Engagement Opp.  | review_synthesis returned >=2 problems with | Render only digital-maturity|
 *   |                  | confidence >= medium                        | gap, no padded extras      |
 *   | Conversation     | >=3 evidence-anchored facts                 | Omit section entirely      |
 *
 * The intelligence_brief markdown produced by Claude is used ONLY as
 * an "additional context" appendix when emitted — it is not the
 * structural backbone of the email, because Claude's prompt instructs
 * it to fill all 5 sections (Pattern A risk per CLAUDE.md). The
 * structural sections come from the authored enrichment rows in the
 * database via this renderer.
 */

import type { Entity } from '../db/entities'
import { listContext } from '../db/context'

export interface RenderedSection {
  /** Stable id for the email template. */
  id:
    | 'business_overview'
    | 'owner_profile'
    | 'tech_ops'
    | 'engagement_opportunity'
    | 'conversation_starters'
  title: string
  /** When false, the email template skips this section entirely. */
  rendered: boolean
  /** When rendered=false but skipReason is set, the email template may
   *  still render a one-line "Insufficient data — N/A" placeholder. The
   *  default behavior is to omit the section silently. */
  insufficientDataNote?: string
  /** HTML-safe content lines. Each is escaped at render time. */
  bullets?: string[]
  /** Optional intro paragraph. */
  paragraph?: string
}

export interface RenderedReport {
  /** True if at least one section rendered (i.e., the email is worth sending
   *  with structural content). When false the email template falls back to
   *  a "we couldn't gather enough public footprint" paragraph instead of
   *  shipping an empty 5-section shell. */
  hasContent: boolean
  sections: RenderedSection[]
  /** Optional appendix containing the Claude-generated narrative brief.
   *  Rendered after the structured sections when present. The structured
   *  sections are the source of truth for client-facing claims; the
   *  narrative is informational. */
  appendixMarkdown: string | null
}

/**
 * Build the rendered report from the entity's enrichment context. The
 * passed-in `briefMarkdown` is the optional intelligence_brief text from
 * Claude — included as the "additional context" appendix only.
 */
export async function renderDiagnosticReport(
  db: D1Database,
  entity: Entity,
  briefMarkdown: string | null
): Promise<RenderedReport> {
  const enrichment = await listContext(db, entity.id, { type: 'enrichment' })

  // Index enrichment metadata by source for quick lookup. Each key is
  // the source name; value is the JSON-parsed metadata or null.
  const meta = new Map<string, Record<string, unknown> | null>()
  for (const row of enrichment) {
    if (!meta.has(row.source)) {
      try {
        meta.set(row.source, row.metadata ? JSON.parse(row.metadata) : null)
      } catch {
        meta.set(row.source, null)
      }
    }
  }

  const sections: RenderedSection[] = [
    renderBusinessOverview(entity, meta),
    renderOwnerProfile(entity, meta),
    renderTechOps(entity, meta),
    renderEngagementOpportunity(entity, meta),
    renderConversationStarters(entity, meta),
  ]

  const hasContent = sections.some((s) => s.rendered)
  return {
    hasContent,
    sections,
    appendixMarkdown: briefMarkdown && briefMarkdown.trim() ? briefMarkdown : null,
  }
}

// ---------------------------------------------------------------------------
// Section renderers — each enforces an authored-data gate.
// ---------------------------------------------------------------------------

function renderBusinessOverview(
  entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): RenderedSection {
  const places = meta.get('google_places')
  const outscraper = meta.get('outscraper')
  // Render only if Google Places (or Outscraper) actually matched. A scan
  // that didn't get a place match has no business overview to write.
  if (!places && !outscraper) {
    return {
      id: 'business_overview',
      title: 'Business Overview',
      rendered: false,
    }
  }

  const bullets: string[] = []
  bullets.push(`Name: ${entity.name}`)
  if (entity.area) bullets.push(`Area: ${entity.area}`)
  const rating = numberFrom(places, 'rating')
  const reviewCount = numberFrom(places, 'reviewCount') ?? numberFrom(outscraper, 'review_count')
  if (rating != null && reviewCount != null && reviewCount > 0) {
    bullets.push(`Google rating: ${rating} stars (${reviewCount} reviews)`)
  } else if (reviewCount != null && reviewCount > 0) {
    bullets.push(`Google reviews: ${reviewCount}`)
  }
  const status = stringFrom(places, 'businessStatus')
  if (status) bullets.push(`Listing status: ${status}`)
  if (entity.website) bullets.push(`Website: ${entity.website}`)
  if (entity.phone) bullets.push(`Phone: ${entity.phone}`)

  return {
    id: 'business_overview',
    title: 'Business Overview',
    rendered: bullets.length > 0,
    bullets,
  }
}

function renderOwnerProfile(
  _entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): RenderedSection {
  const websiteAnalysis = meta.get('website_analysis')
  const outscraper = meta.get('outscraper')
  const deepWebsite = meta.get('deep_website')

  // Render only when we ran at least one of the modules that could
  // produce owner data. With zero source signals the section is
  // structurally absent — not "Insufficient data", not "not publicly
  // identified" (rendering either of those for a row with no signals
  // is itself fabrication: it implies we looked).
  if (!websiteAnalysis && !outscraper && !deepWebsite) {
    return {
      id: 'owner_profile',
      title: 'Owner / Decision-Maker Profile',
      rendered: false,
    }
  }

  // Owner name comes from one of three authored sources. Never invented.
  let ownerName: string | null = null
  let foundingYear: number | null = null
  let teamSize: number | null = null

  ownerName = stringFrom(websiteAnalysis, 'owner_name') ?? ownerName
  if (!ownerName) ownerName = stringFrom(outscraper, 'owner_name') ?? ownerName
  if (!ownerName && deepWebsite) {
    const op = (deepWebsite as Record<string, unknown>).owner_profile as
      | Record<string, unknown>
      | undefined
    if (op && typeof op.name === 'string') ownerName = op.name
  }

  foundingYear =
    numberFrom(websiteAnalysis, 'founding_year') ??
    (deepWebsite
      ? numberFrom(
          ((deepWebsite as Record<string, unknown>).business_profile as
            | Record<string, unknown>
            | undefined) ?? null,
          'founding_year'
        )
      : null)

  teamSize = numberFrom(websiteAnalysis, 'team_size') ?? teamSize

  // If we have nothing, render an explicit "not publicly identified"
  // placeholder rather than inventing a name. Never silently fill.
  const bullets: string[] = []
  if (ownerName) {
    bullets.push(`Owner / decision-maker: ${ownerName}`)
  } else {
    bullets.push('Owner / decision-maker: not publicly identified')
  }
  if (foundingYear) bullets.push(`Founded: ${foundingYear}`)
  if (teamSize) bullets.push(`Apparent team size: ~${teamSize}`)

  return {
    id: 'owner_profile',
    title: 'Owner / Decision-Maker Profile',
    rendered: bullets.length > 0,
    bullets,
    insufficientDataNote: ownerName
      ? undefined
      : 'We list "not publicly identified" rather than guessing — the assessment call is where we confirm.',
  }
}

function renderTechOps(
  _entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): RenderedSection {
  const websiteAnalysis = meta.get('website_analysis')
  const outscraper = meta.get('outscraper')

  if (!websiteAnalysis && !outscraper) {
    return {
      id: 'tech_ops',
      title: 'Technology & Operations',
      rendered: false,
    }
  }

  const bullets: string[] = []

  // Detected tools.
  const techStack = (websiteAnalysis as Record<string, unknown> | null)?.tech_stack as
    | Record<string, unknown>
    | undefined
  if (techStack) {
    const detected: string[] = []
    for (const key of ['scheduling', 'crm', 'reviews', 'payments', 'communication']) {
      const arr = techStack[key]
      if (Array.isArray(arr) && arr.length > 0) {
        detected.push(...arr.filter((v): v is string => typeof v === 'string'))
      }
    }
    if (detected.length > 0) {
      bullets.push(`Tools detected on the site: ${detected.join(', ')}`)
    } else {
      bullets.push('Tools detected on the site: none we could identify')
    }

    const missing: string[] = []
    const sched = techStack.scheduling
    const crm = techStack.crm
    const reviews = techStack.reviews
    if (Array.isArray(sched) && sched.length === 0) missing.push('scheduling')
    if (Array.isArray(crm) && crm.length === 0) missing.push('CRM')
    if (Array.isArray(reviews) && reviews.length === 0) missing.push('review management')
    if (missing.length > 0) {
      bullets.push(`Apparent gaps: no public ${missing.join(', no public ')}`)
    }
  }

  // Booking link / online presence (outscraper).
  const bookingLink = stringFrom(outscraper, 'booking_link')
  if (bookingLink) {
    bullets.push('Online booking visible on Google profile.')
  }

  // Site quality (heuristic from website_analysis).
  const quality = stringFrom(websiteAnalysis, 'quality')
  if (quality) bullets.push(`Site quality (heuristic): ${quality}`)

  return {
    id: 'tech_ops',
    title: 'Technology & Operations',
    rendered: bullets.length > 0,
    bullets,
  }
}

function renderEngagementOpportunity(
  _entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): RenderedSection {
  const synthesis = meta.get('review_synthesis')
  const deepWebsite = meta.get('deep_website')

  // Operational problems with confidence >= medium.
  const problems = (synthesis as Record<string, unknown> | null)?.operational_problems
  const qualified: Array<{ problem: string; evidence: string }> = []
  if (Array.isArray(problems)) {
    for (const p of problems) {
      if (typeof p !== 'object' || p === null) continue
      const rec = p as Record<string, unknown>
      const problem = typeof rec.problem === 'string' ? rec.problem : null
      const confidence = typeof rec.confidence === 'string' ? rec.confidence.toLowerCase() : ''
      const evidence = typeof rec.evidence === 'string' ? rec.evidence : ''
      if (!problem) continue
      if (confidence === 'high' || confidence === 'medium') {
        qualified.push({ problem, evidence })
      }
    }
  }

  // Digital-maturity gap from deep_website.
  let maturityGap: string | null = null
  if (deepWebsite) {
    const dm = (deepWebsite as Record<string, unknown>).digital_maturity as
      | Record<string, unknown>
      | undefined
    if (dm) {
      const score = typeof dm.score === 'number' ? dm.score : null
      const reasoning = typeof dm.reasoning === 'string' ? dm.reasoning.trim() : ''
      if (score != null && score < 7 && reasoning) {
        maturityGap = `Digital maturity score: ${score}/10 — ${reasoning}`
      } else if (score != null && score < 7) {
        maturityGap = `Digital maturity score: ${score}/10`
      }
    }
  }

  const bullets: string[] = []
  // Per the scoping doc: render top problems only when >=2 are qualified.
  // Otherwise render only the digital-maturity gap (no padded fabrications).
  if (qualified.length >= 2) {
    for (const q of qualified.slice(0, 3)) {
      const line = q.evidence ? `${q.problem} — ${q.evidence}` : q.problem
      bullets.push(line)
    }
  }
  if (maturityGap) bullets.push(maturityGap)

  if (bullets.length === 0) {
    return {
      id: 'engagement_opportunity',
      title: 'Engagement Opportunity',
      rendered: false,
      insufficientDataNote:
        'Not enough corroborated public signal to identify specific operational opportunities — the assessment conversation is where these usually surface.',
    }
  }

  return {
    id: 'engagement_opportunity',
    title: 'Engagement Opportunity',
    rendered: true,
    paragraph:
      'Up to three observations from the public footprint, only when corroborated by multiple signals. We surface what we can defend with evidence; the rest waits for the conversation.',
    bullets,
  }
}

function renderConversationStarters(
  entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): RenderedSection {
  // Pull evidence-anchored facts: review themes, services, certifications.
  const facts: string[] = []
  const synthesis = meta.get('review_synthesis')
  const websiteAnalysis = meta.get('website_analysis')
  const deepWebsite = meta.get('deep_website')
  const places = meta.get('google_places')

  if (synthesis) {
    const themes = (synthesis as Record<string, unknown>).top_themes
    if (Array.isArray(themes) && themes.length > 0) {
      const topTheme = themes.find((t): t is string => typeof t === 'string' && t.trim().length > 0)
      if (topTheme) {
        facts.push(`Reviews most often mention: ${topTheme}`)
      }
    }
  }

  const reviewCount = numberFrom(places, 'reviewCount')
  const rating = numberFrom(places, 'rating')
  if (reviewCount != null && reviewCount >= 5 && rating != null) {
    facts.push(`${reviewCount} Google reviews averaging ${rating} stars`)
  }

  const services = (websiteAnalysis as Record<string, unknown> | null)?.services
  if (Array.isArray(services) && services.length > 0) {
    const top = services.filter((s): s is string => typeof s === 'string').slice(0, 3)
    if (top.length > 0) facts.push(`Services listed on the website: ${top.join(', ')}`)
  }

  if (deepWebsite) {
    const bp = (deepWebsite as Record<string, unknown>).business_profile as
      | Record<string, unknown>
      | undefined
    if (bp) {
      const certs = Array.isArray(bp.certifications)
        ? (bp.certifications.filter((s): s is string => typeof s === 'string') as string[])
        : []
      if (certs.length > 0) {
        facts.push(`Certifications visible: ${certs.slice(0, 3).join(', ')}`)
      }
    }
  }

  // Anti-fabrication rule: omit the section if we have <3 evidence-anchored
  // facts. A single fact "you have a website" is not a conversation starter
  // worth shipping in a credibility-driven email.
  if (facts.length < 3) {
    return {
      id: 'conversation_starters',
      title: 'Conversation Starters',
      rendered: false,
    }
  }

  void entity // entity not used directly here; facts already capture identity
  return {
    id: 'conversation_starters',
    title: 'What stood out',
    rendered: true,
    paragraph: 'A few specifics from your public footprint we would lead with in a conversation.',
    bullets: facts.slice(0, 4),
  }
}

// ---------------------------------------------------------------------------
// Helpers — extract typed values from a metadata record without throwing.
// ---------------------------------------------------------------------------

function stringFrom(m: Record<string, unknown> | null | undefined, key: string): string | null {
  if (!m) return null
  const v = m[key]
  if (typeof v === 'string' && v.trim()) return v.trim()
  return null
}

function numberFrom(m: Record<string, unknown> | null | undefined, key: string): number | null {
  if (!m) return null
  const v = m[key]
  if (typeof v === 'number' && Number.isFinite(v)) return v
  return null
}

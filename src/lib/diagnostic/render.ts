/**
 * Anti-fabrication renderer for the diagnostic email (#598, #616).
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
 *   | Owner Profile    | website OR outscraper has owner_name        | "Insufficient data —       |
 *   |                  |  AND it isn't the business name             |  we'll surface in convo"   |
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
 *
 * #616 hardenings (2026-04-27 phoenixanimalexterminator.com smoke test):
 *   - Owner section: anti-fab guard — refuse to render the business
 *     name as the owner name (the bug exemplar). Also prefer the new
 *     "Insufficient data — we'll surface this in conversation" wording.
 *   - Engagement Opportunity: humanize raw 5-cat taxonomy IDs
 *     (process_design, tool_systems, etc.) via PROBLEM_LABELS.
 *   - Conversation Starters: humanize underscored review-theme keys.
 *   - Tech & Ops: drop "no public scheduling" from gaps when Outscraper
 *     surfaced an online-booking link (internal contradiction).
 *   - Conversation Starters: stop re-rendering rating + review count
 *     (already in Business Overview).
 *   - Owner section: only render founding_year when it came from
 *     website_analysis (explicit "founded in" / "established" on-page
 *     copy), not from deep_website's heuristic inference.
 *   - Title: prefer the authored business name from Outscraper's
 *     enrichment metadata over the placeholder humanized-domain entity
 *     name. See `resolveDisplayName`.
 */

import type { Entity } from '../db/entities'
import { listContext } from '../db/context'
import {
  PROBLEM_LABELS,
  PROBLEM_IDS,
  type ProblemId,
} from '../../portal/assessments/extraction-schema'

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
  /** Resolved business name to use as the email title. Prefers the
   *  Outscraper canonical name when available; otherwise the entity row's
   *  name (which may be a humanized-domain placeholder). The email
   *  template should use this in the H1, NOT entity.name directly. */
  displayName: string
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

  const displayName = resolveDisplayName(entity, meta)

  const sections: RenderedSection[] = [
    renderBusinessOverview(entity, meta, displayName),
    renderOwnerProfile(entity, meta, displayName),
    renderTechOps(entity, meta),
    renderEngagementOpportunity(entity, meta),
    renderConversationStarters(entity, meta),
  ]

  const hasContent = sections.some((s) => s.rendered)
  return {
    hasContent,
    displayName,
    sections,
    appendixMarkdown: briefMarkdown && briefMarkdown.trim() ? briefMarkdown : null,
  }
}

// ---------------------------------------------------------------------------
// Display-name resolution (#616 issue 1).
//
// At entity-create time the orchestrator sets entity.name to the humanized
// domain (`phoenixanimalexterminator.com` -> `Phoenixanimalexterminator`).
// Outscraper later returns the canonical business name in its metadata
// (`Phoenix Animal Exterminator`). We prefer the canonical name in the
// rendered report; the placeholder is only the fallback.
// ---------------------------------------------------------------------------

/**
 * Resolve the best available business name for the rendered report.
 *
 * Preference order:
 *   1. Outscraper's `name` field (canonical, properly cased — set by
 *      Google Maps Places API)
 *   2. entity.name (placeholder humanized-domain at scan start; may
 *      have been updated to a canonical name by a downstream module)
 *
 * When Outscraper's name is structurally identical to entity.name except
 * for casing/whitespace, we prefer Outscraper because its casing is the
 * canonical one ("Phoenix Animal Exterminator" beats
 * "Phoenixanimalexterminator"). When the names diverge meaningfully we
 * still prefer Outscraper — it is the authored source.
 */
export function resolveDisplayName(
  entity: Entity,
  meta: Map<string, Record<string, unknown> | null>
): string {
  const outscraperName = stringFrom(meta.get('outscraper'), 'name')
  if (outscraperName) return outscraperName
  return entity.name
}

// ---------------------------------------------------------------------------
// Section renderers — each enforces an authored-data gate.
// ---------------------------------------------------------------------------

function renderBusinessOverview(
  entity: Entity,
  meta: Map<string, Record<string, unknown> | null>,
  displayName: string
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
  bullets.push(`Name: ${displayName}`)
  if (entity.area) bullets.push(`Area: ${entity.area}`)
  const rating = numberFrom(places, 'rating') ?? numberFrom(outscraper, 'rating')
  const reviewCount = numberFrom(places, 'reviewCount') ?? numberFrom(outscraper, 'review_count')
  if (rating != null && reviewCount != null && reviewCount > 0) {
    bullets.push(`Google rating: ${rating} stars (${reviewCount} reviews)`)
  } else if (reviewCount != null && reviewCount > 0) {
    bullets.push(`Google reviews: ${reviewCount}`)
  }
  const status = stringFrom(places, 'businessStatus') ?? stringFrom(outscraper, 'business_status')
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
  meta: Map<string, Record<string, unknown> | null>,
  displayName: string
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
  let teamSize: number | null = null

  ownerName = stringFrom(websiteAnalysis, 'owner_name') ?? ownerName
  if (!ownerName) ownerName = stringFrom(outscraper, 'owner_name') ?? ownerName
  if (!ownerName && deepWebsite) {
    const op = (deepWebsite as Record<string, unknown>).owner_profile as
      | Record<string, unknown>
      | undefined
    if (op && typeof op.name === 'string') ownerName = op.name.trim() || null
  }

  // #616 issue 2 (P1 anti-fab) — refuse to render the BUSINESS NAME as
  // the OWNER name. This was the 2026-04-27 phoenixanimalexterminator.com
  // bug exemplar: Outscraper's `owner_title` field returned the business
  // name itself (because the Google Maps "owner" was the listing-claim
  // owner — the company), and the renderer surfaced that as
  // "Owner / decision-maker: Phoenix Animal Exterminator". That mislabels
  // the field — Pattern A/B violation under CLAUDE.md "No fabricated
  // client-facing content". Compare case-insensitive against both the
  // resolved display name AND entity.name to catch all spellings of the
  // business identity.
  if (ownerName && isLikelyBusinessName(ownerName, displayName, _entity.name)) {
    ownerName = null
  }

  // #616 issue 6 — only surface founding_year when it came from
  // website_analysis. That module's prompt explicitly looks for
  // "founded in" / "established" / copyright-year text on the site, which
  // is high-confidence provenance. We deliberately skip deep_website's
  // founding_year here — it's an LLM heuristic over the same scraped
  // text and we have no source citation to defend if challenged.
  const foundingYear = numberFrom(websiteAnalysis, 'founding_year')

  teamSize = numberFrom(websiteAnalysis, 'team_size') ?? teamSize

  // If we have nothing, render the explicit "Insufficient data" placeholder
  // (#616 issue 2 wording) rather than a fabricated name.
  const bullets: string[] = []
  if (ownerName) {
    bullets.push(`Owner / decision-maker: ${ownerName}`)
  } else {
    bullets.push("Owner / decision-maker: Insufficient data — we'll surface this in conversation.")
  }
  if (foundingYear) bullets.push(`Founded: ${foundingYear} (from website)`)
  if (teamSize) bullets.push(`Apparent team size: ~${teamSize}`)

  return {
    id: 'owner_profile',
    title: 'Owner / Decision-Maker Profile',
    rendered: bullets.length > 0,
    bullets,
    insufficientDataNote: ownerName
      ? undefined
      : "We don't guess at owner names — the assessment call is where we confirm.",
  }
}

/**
 * Anti-fabrication helper: detect when a candidate "owner name" is in
 * fact the business name itself. Catches the 2026-04-27 bug exemplar
 * (Outscraper returning the listing-claim title as `owner_title`) plus
 * any future drift where a different field's value is mislabeled as the
 * owner.
 *
 * The comparison normalizes case, whitespace, and non-alphanumerics so
 * "Phoenix Animal Exterminator" matches "phoenixanimalexterminator" and
 * "Phoenix Animal Exterminator LLC".
 *
 * Exported so the test suite can lock the behavior.
 */
export function isLikelyBusinessName(
  candidate: string,
  ...businessNames: (string | null | undefined)[]
): boolean {
  const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '')
  const candNorm = norm(candidate)
  if (!candNorm) return false
  for (const name of businessNames) {
    if (!name) continue
    const nameNorm = norm(name)
    if (!nameNorm) continue
    // Exact normalized match — "Phoenix Animal Exterminator" vs the
    // entity row's "Phoenixanimalexterminator" placeholder.
    if (candNorm === nameNorm) return true
    // The candidate is the business name with a corporate suffix
    // ("LLC", "Inc", "Co"). Strip those off and compare again.
    const candStripped = candNorm.replace(/(llc|inc|co|corp|corporation|company|ltd|llp)$/i, '')
    const nameStripped = nameNorm.replace(/(llc|inc|co|corp|corporation|company|ltd|llp)$/i, '')
    if (candStripped && nameStripped && candStripped === nameStripped) return true
  }
  return false
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

  // Booking link / online presence (outscraper) — read first so the gap
  // logic below can suppress contradictory "no scheduling" claims.
  const bookingLink = stringFrom(outscraper, 'booking_link')
  const hasOnlineBooking = !!bookingLink

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

    // #616 issue 4 — internal contradiction guard. Only flag a gap if no
    // adjacent signal contradicts it. If Outscraper surfaced an online
    // booking link on the Google profile, "no public scheduling" is wrong
    // — booking IS public, just not on the site we analyzed.
    const missing: string[] = []
    const sched = techStack.scheduling
    const crm = techStack.crm
    const reviews = techStack.reviews
    if (Array.isArray(sched) && sched.length === 0 && !hasOnlineBooking) {
      missing.push('scheduling')
    }
    if (Array.isArray(crm) && crm.length === 0) missing.push('CRM')
    if (Array.isArray(reviews) && reviews.length === 0) missing.push('review management')
    if (missing.length > 0) {
      bullets.push(`Apparent gaps: no public ${missing.join(', no public ')}`)
    }
  }

  if (hasOnlineBooking) {
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
      // #616 issue 3 — humanize raw 5-cat taxonomy IDs into human labels.
      // The review-synthesis prompt explicitly asks Claude to map problems
      // to one of the five observation taxonomy IDs (process_design,
      // tool_systems, ...). Surface the human label, not the schema key.
      const label = humanizeProblemId(q.problem)
      const line = q.evidence ? `${label} — ${q.evidence}` : label
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

  if (synthesis) {
    const themes = (synthesis as Record<string, unknown>).top_themes
    if (Array.isArray(themes) && themes.length > 0) {
      const topTheme = themes.find((t): t is string => typeof t === 'string' && t.trim().length > 0)
      if (topTheme) {
        // #616 issue 3 — review themes come from Claude's free-form
        // synthesis prompt; some come back as snake_case keys
        // ("limited_online_presence"), some as full sentences. Apply a
        // generic underscore-to-space humanizer either way.
        facts.push(`Reviews most often mention: ${humanizeThemeKey(topTheme)}`)
      }
    }
  }

  // #616 issue 5 — DO NOT re-render rating + review count here. It already
  // appears in Business Overview; surfacing it again reads as filler.

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
// Taxonomy + theme humanization (#616 issue 3).
//
// Two distinct cases:
//   1. PROBLEM_LABELS-backed taxonomy IDs (`process_design`, `tool_systems`,
//      `data_visibility`, `customer_pipeline`, `team_operations`). These
//      come from the review-synthesis prompt's instruction to map issues
//      to the 5-cat observation taxonomy. Use the authored label table.
//   2. Free-form review-theme keys (`limited_online_presence`,
//      `friendly_staff`, etc.). These are unbounded — Claude composes
//      them per scan — so a label table would never be complete. Apply a
//      generic underscore -> space + sentence-case transform.
// ---------------------------------------------------------------------------

/**
 * Map a problem id from review_synthesis to its human-readable label.
 *
 * If the value is one of the 5-cat IDs (`PROBLEM_IDS`), use the authored
 * `PROBLEM_LABELS` table. Otherwise the value is already a human-readable
 * sentence (the prompt allows Claude to fall back to free text); pass it
 * through unchanged.
 *
 * Exported for testing.
 */
export function humanizeProblemId(value: string): string {
  if ((PROBLEM_IDS as readonly string[]).includes(value)) {
    return PROBLEM_LABELS[value as ProblemId]
  }
  return value
}

/**
 * Humanize a free-form review-theme key.
 *
 * Examples:
 *   `limited_online_presence` -> `Limited online presence`
 *   `friendly staff`          -> `Friendly staff`
 *   `Quick response`          -> `Quick response`
 *
 * Underscores become spaces; the first character is upper-cased; the
 * rest is left alone (so "AI tools" stays "AI tools" rather than
 * "Ai tools"). Exported for testing.
 */
export function humanizeThemeKey(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) return trimmed
  const spaced = trimmed.replace(/_/g, ' ')
  return spaced.charAt(0).toUpperCase() + spaced.slice(1)
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

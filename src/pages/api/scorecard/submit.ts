import type { APIRoute } from 'astro'
import { findOrCreateEntity } from '../../../lib/db/entities'
import { appendContext } from '../../../lib/db/context'
import { createContact } from '../../../lib/db/contacts'
import { ORG_ID } from '../../../lib/constants'
import { QUESTIONS } from '../../../lib/scorecard/questions'
import {
  computeScores,
  computePainScore,
  computeAutoStage,
  parseEmployeeCount,
} from '../../../lib/scorecard/scoring'
import { SCORE_DESCRIPTIONS } from '../../../lib/scorecard/descriptions'
import { renderScorecardReport } from '../../../lib/pdf/render'
import { sendEmail } from '../../../lib/email/resend'
import { scorecardReportEmailHtml } from '../../../lib/email/templates'
import type { DimensionId } from '../../../lib/scorecard/questions'
import { env } from 'cloudflare:workers'

/**
 * POST /api/scorecard/submit
 *
 * Public endpoint for scorecard form submission. Creates an entity,
 * contact, and scorecard context entry. Generates and emails PDF report.
 *
 * Security:
 * - Honeypot field rejects bot submissions silently
 * - Minimum completion time check (< 15s = bot)
 * - No auth required (public-facing)
 */
export const POST: APIRoute = async ({ request }) => {
  let body: Record<string, unknown>
  try {
    body = (await request.json()) as Record<string, unknown>
  } catch {
    return jsonResponse(400, { error: 'Invalid JSON' })
  }

  // Honeypot check — bots fill this hidden field, humans don't
  if (typeof body.website_url === 'string' && body.website_url.trim() !== '') {
    return jsonResponse(200, { ok: true })
  }

  // Minimum completion time — real humans take > 15 seconds
  if (typeof body.started_at === 'number' && Date.now() - body.started_at < 15000) {
    return jsonResponse(200, { ok: true })
  }

  // Validate required fields
  const firstName = trimString(body.first_name)
  const email = trimString(body.email)
  const businessName = trimString(body.business_name)
  const vertical = trimString(body.vertical) || 'other'
  const employeeRange = trimString(body.employee_range) || '11-25'
  const role = trimString(body.role) || 'owner'
  const phone = trimString(body.phone)

  if (!firstName || !email || !businessName) {
    return jsonResponse(400, { error: 'first_name, email, and business_name are required' })
  }

  // Validate answers — all 18 question IDs must be present with values 0-3
  const answers = body.answers as Record<string, number> | undefined
  if (!answers || typeof answers !== 'object') {
    return jsonResponse(400, { error: 'answers object is required' })
  }

  const questionIds = QUESTIONS.map((q) => q.id)
  for (const qid of questionIds) {
    const val = answers[qid]
    if (typeof val !== 'number' || val < -1 || val > 3) {
      return jsonResponse(400, { error: `Invalid or missing answer for ${qid}` })
    }
  }

  try {
    // Compute scores
    const scores = computeScores(answers)
    const painScore = computePainScore(scores.overall)
    const autoStage = computeAutoStage(painScore)
    const employeeCount = parseEmployeeCount(employeeRange)

    // Find or create entity (deduplicates by business name slug)
    const { entity } = await findOrCreateEntity(env.DB, ORG_ID, {
      name: businessName,
      stage: autoStage,
      source_pipeline: 'website_scorecard',
    })

    // Check for existing contact by email — skip creation on retakes
    const existingContact = await env.DB.prepare(
      'SELECT id FROM contacts WHERE org_id = ? AND email = ? LIMIT 1'
    )
      .bind(ORG_ID, email)
      .first<{ id: string }>()

    if (!existingContact) {
      await createContact(env.DB, ORG_ID, entity.id, {
        name: firstName,
        email,
        phone,
      })
    }

    // Build human-readable summary for context content
    const topProblemLabels = scores.topProblems.map((id) => {
      const dim = scores.dimensions.find((d) => d.id === id)
      return dim?.label ?? id
    })
    const contentLines = [
      `Operations Health Score: ${scores.overall}/100 (${scores.overallDisplayLabel})`,
      `Vertical: ${vertical} | Team size: ${employeeRange} | Role: ${role}`,
      `Top opportunities: ${topProblemLabels.join(', ')}`,
      '',
      ...scores.dimensions.map((d) => `  ${d.label}: ${d.scaled}/100 (${d.displayLabel})`),
    ]

    // Always append scorecard context (supports retakes)
    // Metadata includes pain_score, vertical, employee_count for recomputeDeterministicCache
    await appendContext(env.DB, ORG_ID, {
      entity_id: entity.id,
      type: 'scorecard',
      content: contentLines.join('\n'),
      source: 'website_scorecard',
      metadata: {
        vertical,
        employee_range: employeeRange,
        role,
        answers,
        dimension_scores: Object.fromEntries(
          scores.dimensions.map((d) => [
            d.id,
            { raw: d.raw, scaled: d.scaled, label: d.scoreLabel },
          ])
        ),
        overall_score: scores.overall,
        overall_label: scores.overallLabel,
        top_problems: scores.topProblems,
        pain_score: painScore,
        employee_count: employeeCount,
        first_name: firstName,
        email,
        business_name: businessName,
        phone,
      },
    })

    // Generate PDF and send email (synchronous for v1)
    try {
      const dimensions = scores.dimensions.map((d) => ({
        label: d.label,
        scaled: d.scaled,
        displayLabel: d.displayLabel,
        color: d.color,
        description: SCORE_DESCRIPTIONS[d.id as DimensionId]?.[d.scoreLabel] ?? '',
      }))

      const opportunities = scores.topProblems.map((id) => {
        const dim = scores.dimensions.find((d) => d.id === id)
        return {
          label: dim?.label ?? id,
          description: SCORE_DESCRIPTIONS[id]?.[dim?.scoreLabel ?? 'needs_attention'] ?? '',
        }
      })

      const pdfBytes = await renderScorecardReport({
        firstName,
        businessName,
        vertical,
        overallScore: scores.overall,
        overallDisplayLabel: scores.overallDisplayLabel,
        overallColor: scores.overallColor,
        dimensions,
        opportunities,
        completedAt: new Date().toLocaleDateString('en-US', {
          year: 'numeric',
          month: 'long',
          day: 'numeric',
        }),
      })

      const pdfBase64 = uint8ArrayToBase64(pdfBytes)

      const emailHtml = scorecardReportEmailHtml(
        firstName,
        scores.overall,
        scores.overallDisplayLabel
      )

      await sendEmail(env.RESEND_API_KEY, {
        to: email,
        subject: `Your Operations Health Report — ${scores.overall}/100`,
        html: emailHtml,
        attachments: [
          {
            filename: 'operations-health-report.pdf',
            content: pdfBase64,
            content_type: 'application/pdf',
          },
        ],
      })
    } catch (pdfErr) {
      // PDF/email failure should not block the response
      console.error('[api/scorecard/submit] PDF/email error:', pdfErr)
    }

    return jsonResponse(201, { ok: true, entity_id: entity.id })
  } catch (err) {
    console.error('[api/scorecard/submit] Error:', err)
    return jsonResponse(500, { error: 'Internal server error' })
  }
}

function trimString(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

function jsonResponse(status: number, data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function uint8ArrayToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) {
    binary += String.fromCharCode(bytes[i])
  }
  return btoa(binary)
}

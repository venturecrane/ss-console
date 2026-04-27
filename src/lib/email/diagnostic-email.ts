/**
 * Email templates for the public diagnostic scan flow (#598).
 *
 * Three templates:
 *   1. Magic-link verification     — "click to confirm and run your scan"
 *   2. Diagnostic report           — full 5-section report with /book CTA
 *   3. Thin-footprint refusal      — "we couldn't read enough; let's talk"
 *
 * All follow the SMD Services voice from CLAUDE.md / Decision Stack #20:
 *   - "we" / "our team", never "I" / "the consultant"
 *   - No fixed timeframes ("we start with a conversation", not "60-min call")
 *   - No fabricated commitments (no "replies within 1 business day")
 *   - "Solution" not "systems" in marketing context
 *   - No published dollar amounts
 *
 * The report template structurally renders pre-validated sections from
 * `RenderedReport` (see `src/lib/diagnostic/render.ts`); it does not
 * touch raw enrichment data. Anti-fabrication enforcement lives in the
 * renderer, not here.
 */

import { BRAND_NAME } from '../config/brand'
import type { RenderedReport, RenderedSection } from '../diagnostic/render'

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// ---------------------------------------------------------------------------
// 1. Magic-link verification
// ---------------------------------------------------------------------------

export interface ScanVerificationEmailInput {
  /** Verification URL the prospect clicks to start their scan. Already
   *  signed and ready to embed. */
  verifyUrl: string
  /** The domain they asked us to scan. Echoed in the body so they can
   *  confirm they got the right link. */
  scannedDomain: string
}

/**
 * Sent immediately after a successful POST /api/scan/start. Clicking the
 * link verifies the email is reachable + the scan was actually requested,
 * and triggers the pruned enrichment pipeline via ctx.waitUntil.
 *
 * Voice rules: "we" only. No promises about turnaround time. The link
 * itself is the implicit "we'll send your report when you click" — no
 * uncontracted commitment beyond that.
 */
export function scanVerificationEmailHtml(input: ScanVerificationEmailInput): string {
  const verifyUrl = escapeHtml(input.verifyUrl)
  const domain = escapeHtml(input.scannedDomain)
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <h1 style="font-size:20px;font-weight:700;color:#0f172a;margin:0 0 8px;">Confirm your scan</h1>
      <p style="font-size:14px;color:#64748b;margin:0 0 24px;">${BRAND_NAME} &middot; Operational Readiness Scan</p>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        We received a request to run an operational scan for <strong>${domain}</strong>.
        Click the button below to confirm and we'll get started.
      </p>

      <p style="margin:24px 0;">
        <a href="${verifyUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 28px;border-radius:6px;">
          Confirm and run my scan
        </a>
      </p>

      <p style="font-size:13px;color:#64748b;margin:0 0 16px;word-break:break-all;">
        Or paste this link into your browser:<br>
        <a href="${verifyUrl}" style="color:#1e40af;">${verifyUrl}</a>
      </p>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        This link works for 24 hours. If you didn't request this, you can safely ignore this email.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} ${BRAND_NAME} &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

// ---------------------------------------------------------------------------
// 2. Diagnostic report
// ---------------------------------------------------------------------------

export interface DiagnosticReportEmailInput {
  businessName: string
  rendered: RenderedReport
  bookingUrl: string
}

/**
 * The full report email. Walks the RenderedReport sections, rendering each
 * one only when `rendered === true`. Sections marked rendered=false are
 * either silently omitted, or — when an `insufficientDataNote` is set — a
 * one-line italic placeholder is shown so the prospect understands we are
 * being explicit about what we couldn't determine, rather than padding.
 *
 * If `rendered.hasContent === false` (no sections rendered at all), we
 * fall back to a "we couldn't gather enough public footprint" body
 * matching the thin-footprint email format. This is defense-in-depth: the
 * pre-flight gate should catch most thin-footprint cases before we ever
 * render, but a degraded LLM run that returns no signals shouldn't ship
 * an empty 5-section shell.
 */
export function diagnosticReportEmailHtml(input: DiagnosticReportEmailInput): string {
  const businessName = escapeHtml(input.businessName)
  const bookingUrl = escapeHtml(input.bookingUrl)

  const sectionsHtml = input.rendered.hasContent
    ? input.rendered.sections
        .map(renderSectionHtml)
        .filter((s) => s)
        .join('')
    : insufficientDataFallbackHtml()

  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:640px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <p style="font-size:13px;color:#64748b;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.05em;">${BRAND_NAME} &middot; Operational read</p>
      <h1 style="font-size:24px;font-weight:700;color:#0f172a;margin:0 0 24px;">${businessName}</h1>

      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Here's what we read from your public footprint. Each section below comes from
        a specific signal we found — when we couldn't find enough to back something up,
        we say so rather than guessing.
      </p>

      ${sectionsHtml}

      <div style="border-top:1px solid #e2e8f0;margin:32px 0 24px;"></div>

      <h2 style="font-size:18px;font-weight:600;color:#0f172a;margin:0 0 12px;">Want to dig in?</h2>
      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        The most useful next step is a short conversation. We walk through your day together,
        understand where you're trying to go, and figure out what's in the way.
      </p>

      <p style="margin:0 0 32px;">
        <a href="${bookingUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 28px;border-radius:6px;">
          Pick a time
        </a>
      </p>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        ${BRAND_NAME} is a Phoenix-based operations consulting firm. You requested this report
        from smd.services/scan. We don't add you to a mailing list and we don't share your
        information with anyone else.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} ${BRAND_NAME} &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

function renderSectionHtml(section: RenderedSection): string {
  if (!section.rendered) {
    if (section.insufficientDataNote) {
      return `
        <h2 style="font-size:18px;font-weight:600;color:#0f172a;margin:24px 0 8px;">${escapeHtml(section.title)}</h2>
        <p style="font-size:14px;color:#64748b;font-style:italic;margin:0 0 8px;">
          Insufficient data — ${escapeHtml(section.insufficientDataNote)}
        </p>`
    }
    return ''
  }
  const intro = section.paragraph
    ? `<p style="font-size:14px;color:#475569;margin:0 0 8px;">${escapeHtml(section.paragraph)}</p>`
    : ''
  const list =
    section.bullets && section.bullets.length > 0
      ? `<ul style="margin:0 0 16px;padding-left:20px;">${section.bullets
          .map(
            (b) => `<li style="font-size:15px;color:#334155;margin:0 0 6px;">${escapeHtml(b)}</li>`
          )
          .join('')}</ul>`
      : ''
  const note = section.insufficientDataNote
    ? `<p style="font-size:13px;color:#64748b;font-style:italic;margin:0 0 16px;">${escapeHtml(section.insufficientDataNote)}</p>`
    : ''
  return `
    <h2 style="font-size:18px;font-weight:600;color:#0f172a;margin:24px 0 8px;">${escapeHtml(section.title)}</h2>
    ${intro}
    ${list}
    ${note}`
}

function insufficientDataFallbackHtml(): string {
  return `
    <p style="font-size:15px;color:#334155;margin:0 0 16px;">
      We tried to read your public digital footprint and couldn't find enough to
      produce a useful report. That's actually informative on its own — a thin public
      footprint is often a sign of where we'd start. The most useful next step is a
      short conversation.
    </p>`
}

// ---------------------------------------------------------------------------
// 3. Thin-footprint refusal
// ---------------------------------------------------------------------------

export interface ThinFootprintEmailInput {
  businessName: string
  submittedDomain: string
  /** Machine-readable reason — used only for choosing the explanatory line.
   *  We never echo the raw reason to the prospect. */
  reason: string
  bookingUrl: string
}

/**
 * Sent when the pre-flight gate refuses the scan. Anti-fabrication-safe:
 * we don't ship a padded report, we honestly say "we couldn't find enough"
 * and route to the same /book CTA.
 *
 * The body deliberately reframes the refusal as informative — a thin
 * public footprint is itself a starting point — rather than as a failure
 * the prospect would resent.
 */
export function thinFootprintEmailHtml(input: ThinFootprintEmailInput): string {
  const businessName = escapeHtml(input.businessName)
  const submittedDomain = escapeHtml(input.submittedDomain)
  const bookingUrl = escapeHtml(input.bookingUrl)
  return `<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background-color:#f8fafc;font-family:'Inter',Arial,sans-serif;">
  <div style="max-width:520px;margin:40px auto;background:#ffffff;border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">
    <div style="padding:32px 24px;">
      <p style="font-size:13px;color:#64748b;margin:0 0 4px;text-transform:uppercase;letter-spacing:0.05em;">${BRAND_NAME}</p>
      <h1 style="font-size:22px;font-weight:700;color:#0f172a;margin:0 0 16px;">About your scan</h1>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Thanks for trying us out for <strong>${businessName}</strong>.
      </p>

      <p style="font-size:15px;color:#334155;margin:0 0 16px;">
        Our scan reads what's publicly available — Google reviews, your website, business
        listings — and we couldn't find enough public footprint on
        <strong>${submittedDomain}</strong> to produce something useful.
      </p>

      <p style="font-size:15px;color:#334155;margin:0 0 24px;">
        Honestly, that's informative on its own. A thin public footprint is often
        where we'd start — there's usually low-effort, high-leverage work to do
        before customers can even find you. The most useful next step is a short
        conversation.
      </p>

      <p style="margin:24px 0;">
        <a href="${bookingUrl}"
           style="display:inline-block;background-color:#1e40af;color:#ffffff;
                  font-size:14px;font-weight:600;text-decoration:none;
                  padding:12px 28px;border-radius:6px;">
          Pick a time
        </a>
      </p>

      <p style="font-size:12px;color:#94a3b8;margin:24px 0 0;">
        ${BRAND_NAME} is a Phoenix-based operations consulting firm.
      </p>
    </div>
    <div style="background-color:#f8fafc;padding:16px 24px;text-align:center;border-top:1px solid #e2e8f0;">
      <p style="font-size:11px;color:#94a3b8;margin:0;">
        &copy; ${new Date().getFullYear()} ${BRAND_NAME} &middot; Phoenix, AZ
      </p>
    </div>
  </div>
</body>
</html>`
}

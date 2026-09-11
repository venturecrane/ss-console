/**
 * Regression guard: forbidden strings must not appear in shipped source files.
 *
 * These strings represent CLAUDE.md Pattern A/B violations (committed template
 * sentences promising uncontracted behavior, or hardcoded fallback identities).
 * Any re-introduction of these strings is a P0 compliance failure.
 *
 * @see CLAUDE.md — "No fabricated client-facing content"
 * @see docs/archive/code-review-2026-04-16.md
 * @see GitHub issues #398
 */

import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync, existsSync } from 'fs'
import { resolve, join, extname } from 'path'

const SRC_ROOT = resolve('src')
const PAGES_ROOT = resolve('src/pages')
const COMPONENTS_ROOT = resolve('src/components')
const LAYOUTS_ROOT = resolve('src/layouts')

const USER_FACING_EXCLUDED_DIRS = [
  resolve('src/pages/admin'),
  resolve('src/pages/api'),
  resolve('src/pages/design-preview'),
  resolve('src/pages/dev'),
  resolve('src/components/admin'),
]

/** Collect all .astro, .ts, .tsx files under src/ (excluding test files and dev harness) */
function collectSourceFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      // Exclude src/pages/dev/ — developer harness, not shipped client-facing code
      if (fullPath === resolve('src/pages/dev')) continue
      files.push(...collectSourceFiles(fullPath))
    } else {
      const ext = extname(entry)
      if (
        ['.astro', '.ts', '.tsx'].includes(ext) &&
        !entry.endsWith('.test.ts') &&
        !entry.endsWith('.test.tsx')
      ) {
        files.push(fullPath)
      }
    }
  }
  return files
}

function isWithinDir(path: string, dir: string): boolean {
  return path === dir || path.startsWith(`${dir}/`)
}

function collectAstroFiles(dir: string): string[] {
  const files: string[] = []
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry)
    if (USER_FACING_EXCLUDED_DIRS.some((excludedDir) => isWithinDir(fullPath, excludedDir))) {
      continue
    }

    const stat = statSync(fullPath)
    if (stat.isDirectory()) {
      files.push(...collectAstroFiles(fullPath))
    } else if (extname(entry) === '.astro') {
      files.push(fullPath)
    }
  }
  return files
}

function stripComments(content: string): string {
  return content
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

const FORBIDDEN_PATTERNS: Array<{ label: string; pattern: RegExp | string }> = [
  {
    // A worker that never existed was cited as the thing that deletes stale
    // booking holds and OAuth states (code review 2026-09-10, Documentation
    // finding 3). A comment that names a non-existent mechanism is a false
    // claim about the system; nothing under src/ may name it again.
    label: 'phantom mechanism: workers/booking-cleanup never existed',
    pattern: 'workers/booking-cleanup',
  },
  {
    label: 'Pattern A: hardcoded kickoff outreach promise',
    pattern: "We'll reach out to schedule kickoff",
  },
  {
    label: 'Pattern A: hardcoded "within two weeks" start window',
    pattern: 'Work begins within two weeks',
  },
  {
    label: 'Pattern A: hardcoded SLA "Replies within 1 business day"',
    pattern: 'Replies within 1 business day',
  },
  {
    label: 'Pattern A: hardcoded "2-week stabilization period" duration',
    pattern: '2-week stabilization period',
  },
  {
    label: 'Pattern A: hardcoded "within 1 business day of receiving"',
    pattern: 'within 1 business day of receiving',
  },
  {
    label: 'Pattern A: hardcoded "within one business day" promise',
    pattern: 'within one business day',
  },
  {
    label: 'Pattern A: hardcoded "will / we\'ll reach out" outreach promise (structural)',
    // 2026-04-17 audit finding: dashboard fallback rendered
    // `${consultantFirst} will reach out to schedule the next check-in.` as
    // fabricated next-step copy when no authored touchpoint existed.
    // 2026-06-12 code review: "We'll reach out once it's ready to review."
    // on the portal operator provisioning card slipped past the literal
    // /will reach out/ form. Broadened to catch the contraction. The
    // first-person prospect voice ("I'll reach out") is deliberately not
    // matched — that is the client speaking, not SMD promising.
    pattern: /(?:we['’]ll|will)\s+reach out/i,
  },
  {
    // 2026-06-30 code review (C2): src/lib/sow/service-finalize.ts hardcoded
    // `description: 'Deposit - Operations Cleanup Engagement'` on EVERY client's
    // Stripe invoice — a synthesized scope label rendered to a client (same
    // Pattern-B family as the audited `overview: 'Operations cleanup engagement
    // as discussed during assessment.'`). Invoice/SOW/PDF descriptions must come
    // from authored quote content, never a template scope phrase.
    // Case-SENSITIVE on purpose: the lowercase "operations cleanup engagements"
    // in LLM system prompts (dossier.ts) describes the
    // business to the model and is not client-rendered content — matching the
    // title-case rendered label avoids those false positives.
    label: 'Pattern B: hardcoded "Operations Cleanup Engagement" scope label (invoices/SOW/PDF)',
    pattern: 'Operations Cleanup Engagement',
  },
  {
    label: 'Pattern A: hardcoded "will / we\'ll be in touch" outbound-contact promise (structural)',
    // 2026-05-04 /book intake architecture review caught this same class
    // creeping back in as a Send-acknowledgement copy ("Got it. We'll be
    // in touch.") before merge. Same false-promise shape as `will reach
    // out` — commits SMD to an outbound action that no system guarantees.
    // 2026-06-12 code review: the signature-confirmation email shipped
    // "Our team will be in touch shortly" — the uncontracted tense variant
    // the literal /we'll be in touch/ form missed. Broadened.
    pattern: /(?:['’]ll|will)\s+be in touch/i,
  },
  {
    label: 'Pattern A: hardcoded "we will send it/a new" outbound delivery promise',
    // 2026-06-12 code review: InvoiceDetail rendered "We will send a new
    // one." / "We will send it shortly." — promises of a manual outbound
    // action no system guarantees. Scoped to the it/a-new objects so that
    // system-guaranteed mechanics ("we will send a calendar invite", which
    // booking reserve actually sends automatically) stay legal.
    pattern: /we will send (?:it|a new)\b/i,
  },
  {
    label: 'Pattern A: hardcoded work-start framing "work begins at/once/when"',
    // 2026-06-12 code review: EngagementProgress empty state rendered
    // "The work begins at the first scheduled check-in." — a schedule
    // commitment not authored per engagement. The literal "Work begins
    // within two weeks" guard above covers the duration variant; this
    // covers the event-anchored variants.
    pattern: /work begins (?:at|once|when)\b/i,
  },
  {
    label: 'Pattern B: synthesized "Kickoff next:" next-step copy',
    // 2026-04-17 audit finding: signed-state copy synthesized
    // `Kickoff next: ${engagement.scope_summary}.` when next_touchpoint_label
    // was missing. scope_summary is not an authored next-step field.
    pattern: /Kickoff next:/,
  },
  {
    label: 'Pattern B: fabricated "Engagement work" invoice line-item fallback',
    // 2026-04-17 audit finding: invoice line-item fallback fabricated
    // 'Engagement work' when no line items existed. Send-gate now blocks
    // sending an invoice without line items; this guards re-introduction
    // of a client-facing placeholder.
    pattern: /['"]Engagement work['"]/,
  },
  {
    label: 'Entity detail regression: do not reintroduce outreach_angle',
    pattern: /\boutreach_angle\b/,
  },
  {
    label: 'Entity detail regression: do not reintroduce pre-dossier draft copy',
    pattern: /Pre-dossier draft/,
  },
  {
    label: 'Pattern B: hardcoded "Scott" fallback in portal render paths',
    // Match ?? 'Scott' or : 'Scott' (ternary / nullish) in portal pages and components.
    // Does not flag 'Scott' in test fixtures, variable names, or email author strings.
    pattern: /\?\? ['"]Scott['"]/,
  },
  {
    label: "Pattern B: hardcoded default consultantFirstName = 'Scott'",
    pattern: /consultantFirstName\s*=\s*['"]Scott['"]/,
  },
  // --- Decision Stack #20 voice violations — portal surfaces must use
  //     "we / our team", never a named human or single-person framing.
  //     Added 2026-04-23 alongside the Plainspoken PR B voice fixes. ---
  {
    // "Text {consultantFirst} with questions." routes the client at a
    // specific person. The portal's persistent header three-icon control
    // (email / SMS / phone) is the canonical contact affordance; do not
    // re-route to a named consultant in body copy.
    label: 'Decision Stack #20: personalized "Text {firstName} with questions" CTA',
    pattern: /Text \{?\w+\}? with questions/i,
  },
  {
    // "Your consultant will send…" / "your consultant will…" frames the
    // engagement as a single-person service relationship. Prefer "we'll
    // send…" or similar. First flagged in the 2026-04-15 Pattern audit
    // and fixed in src/components/portal/InvoiceDetail.astro.
    label: 'Decision Stack #20: "your consultant will …" named-person framing',
    pattern: /your consultant will/i,
  },
  // --- Specific phrases removed from CaseStudies.astro (2026-04-22) ---
  // CaseStudies shipped four fabricated case studies with specific
  // quantified results. Real case studies belong in an authored data
  // source per engagement, not committed to source. These patterns
  // guard against reintroduction of the exact phrases removed.
  {
    label: 'Pattern A: fabricated "X hours/week freed" result',
    pattern: /hours\/week freed/,
  },
  {
    label: 'Pattern A: fabricated "Zero missed leads in [period]" result',
    pattern: /Zero missed leads in /,
  },
  {
    label: 'Pattern A: fabricated "Zero turnover in [period]" result',
    // Matches both "Zero turnover in 6 months" and "Zero turnover in the 6 months"
    // (the original fabrication used the latter phrasing). Still shape-bound —
    // honest general copy like "Zero turnover in isolation" would not match.
    pattern: /Zero turnover in (?:the |\d)/,
  },
  {
    label: 'Pattern A: fabricated "Partner reclaimed N+ hours" result',
    pattern: /Partner reclaimed \d+\+? hours/,
  },
  // --- AI-opener voice violations — see issue #815 ---
  // "Thanks for sharing" is a banned AI opener that performs gratitude
  // without conveying information. First flagged on the /get-started post-
  // booking success page; Captain decision 2026-05-26 to kill outright
  // rather than carve out a confirmed-prospect exception.
  {
    label: 'AI-opener: banned "Thanks for sharing" acknowledgment',
    pattern: /Thanks for sharing/i,
  },
  // --- Structural pattern: quantified time-savings results at large ---
  // Catches "12 hours/week freed", "15 hrs per month saved",
  // "10+ hours/day reclaimed", etc. Drift resistance to the pattern-class,
  // not just today's exact wording. Narrowly scoped to the
  // freed/saved/reclaimed/back verbs to avoid false positives on honest
  // general copy about "hours per week".
  {
    label: 'Pattern A: fabricated quantified time-savings result (structural)',
    pattern:
      /\d+\+?\s*(?:hours?|hrs?)\s*(?:per|\/)\s*(?:week|wk|month|mo|day)\s+(?:freed|saved|reclaimed|back)/i,
  },
  // --- Retired Operator marketing spine (ADR 0037, 2026-06-27) ---
  // "the role you keep meaning to fill" opened on a role the owner had failed
  // to fill, which is an accusation; "fill the seat" carried the same framing.
  // Both retired in favor of the off-the-shelf / built-for-you / third-option
  // spine. Scanned over src only (docs/ may quote the retired line to explain
  // the rip). See feedback_no_accusatory_role_framing.
  {
    label: 'Retired accusatory spine: "keep(s) meaning to fill"',
    pattern: /keeps?\s+meaning\s+to\s+fill/i,
  },
  {
    label: 'Retired spine framing: "fill the seat"',
    pattern: /\bfill the seat\b/i,
  },
]

const sourceFiles = collectSourceFiles(SRC_ROOT)
const userFacingSurfaceFiles = [
  ...collectAstroFiles(PAGES_ROOT),
  ...collectAstroFiles(COMPONENTS_ROOT),
  ...collectAstroFiles(LAYOUTS_ROOT),
]

const USER_FACING_COPY_GUARDS: Array<{ label: string; pattern: RegExp }> = [
  {
    label: 'no em dashes in shipped user-facing surfaces',
    pattern: /—/,
  },
  {
    label: 'no "coming soon" placeholder copy in shipped user-facing surfaces',
    pattern: /\bcoming soon\b/i,
  },
  // "off the shelf" disparages the very people and software an Operator works
  // alongside (a non-starter for a staffing alternative). Retired 2026-06-27 in
  // favor of the gap / space-between spine. See feedback_respect_people_and_software.
  {
    label: 'no "off the shelf" framing in shipped user-facing surfaces',
    pattern: /\boff the shelf\b/i,
  },
  // The compliance page asserted which agreements are in force from a
  // template sentence (claims review 2026-09-04, A4). Whether documents are
  // in force is a reading of the paper, never a sentence the code supplies.
  {
    label: 'no "in force together" contractual-status assertion in shipped user-facing surfaces',
    pattern: /\bin force together\b/i,
  },
]

// ============================================================================
// Checkout-return guard (claims review 2026-09-04, A5). The Billing surface
// told the client "Your subscription is active" from `?start=done` alone,
// and the Hosted Agent thanks page carried the lowercase form. Both surfaces
// now render from the Checkout Session and the client's own row, so the
// sentence must not return to either file. Scoped to the two files where it
// lived: the hosted-agent product page derives its own wording from a
// loaded row plus roles and is not this guard's subject.
// ============================================================================

const CHECKOUT_RETURN_FILES = [
  resolve('src/pages/portal/billing/index.astro'),
  resolve('src/pages/agent/thanks.astro'),
]
const CHECKOUT_RETURN_PATTERN = /\byour subscription is active\b/i

describe('checkout-return copy guard (no "your subscription is active" from the query string)', () => {
  it('finds both checkout-return surfaces (sanity)', () => {
    for (const file of CHECKOUT_RETURN_FILES) {
      expect(existsSync(file), file).toBe(true)
    }
  })

  it('neither surface states the subscription is active', () => {
    const violations: string[] = []
    for (const file of CHECKOUT_RETURN_FILES) {
      const content = stripComments(readFileSync(file, 'utf-8'))
      if (CHECKOUT_RETURN_PATTERN.test(content)) {
        violations.push(file.replace(SRC_ROOT, 'src'))
      }
    }
    expect(violations).toEqual([])
  })
})

describe('forbidden-strings: Pattern A/B violations must not appear in shipped source', () => {
  for (const { label, pattern } of FORBIDDEN_PATTERNS) {
    it(`must not contain: ${label}`, () => {
      const violations: string[] = []
      for (const file of sourceFiles) {
        const content = readFileSync(file, 'utf-8')
        const matched =
          typeof pattern === 'string' ? content.includes(pattern) : pattern.test(content)
        if (matched) {
          // Compute a relative path for readable failure messages
          const rel = file.replace(SRC_ROOT, 'src')
          violations.push(rel)
        }
      }
      expect(violations).toEqual([])
    })
  }
})

// ============================================================================
// Done-card fabrication-trap guard — V3 /book chat redesign.
//
// IntakeClosed.astro renders an acknowledgment card after the prospect
// clicks "Done" without booking. Per the no-fabricated-content policy in
// CLAUDE.md (Pattern A is P0), the card MUST NOT promise follow-up
// outreach. The Captain authors the literal copy line; this scoped check
// blocks regression patterns sneaking into THIS file specifically. The
// global FORBIDDEN_PATTERNS list catches historical violations elsewhere;
// this list is targeted at the surface most likely to drift.
// ============================================================================

const DONE_CARD_FILE = resolve('src/components/booking/IntakeClosed.astro')
const DONE_CARD_FABRICATION_PATTERNS: Array<{ label: string; pattern: RegExp }> = [
  {
    label: '"we will" follow-up promise',
    pattern: /\bwe\s+will\b/i,
  },
  {
    label: '"we\'ll" follow-up promise',
    pattern: /\bwe['’]ll\b/i,
  },
  {
    label: '"review your" implies we will read and act',
    pattern: /\breview your\b/i,
  },
  {
    label: '"get back" implies a return contact',
    pattern: /\bget back\b/i,
  },
  {
    label: '"in touch" implies we will reach out',
    pattern: /\bin touch\b/i,
  },
  {
    label: '"reach out" implies we will reach out',
    pattern: /\breach out\b/i,
  },
]

describe('IntakeClosed.astro fabrication guard (no follow-up promises)', () => {
  it('finds the Done acknowledgment card source (sanity)', () => {
    expect(() => readFileSync(DONE_CARD_FILE, 'utf-8')).not.toThrow()
  })

  for (const { label, pattern } of DONE_CARD_FABRICATION_PATTERNS) {
    it(`IntakeClosed.astro must not contain: ${label}`, () => {
      const content = stripComments(readFileSync(DONE_CARD_FILE, 'utf-8'))
      expect(pattern.test(content)).toBe(false)
    })
  }
})

// ============================================================================
// Operator marketing page — fenced-term guard.
//
// Four terms are fenced from the Operator SKU marketing copy: "compliant" (no
// compliance claim without counsel review), "AI Workforce" (a competitor
// trademark), "AI Operating System" (a competitor trademark), and "litigation
// insurance" (overclaim). This guard targets the Operator SKU page specifically
// rather than all of src/, where "compliant" can legitimately appear (e.g.
// "WCAG-compliant" in a technical context). Comments are stripped first, so an
// explanatory note that mentions a fenced term does not trip the guard.
// ============================================================================

// The Hosted Agent storefront (ADR 0067) carries the same fenced-term
// exposure as the Operator page, so both SKU pages are scanned.
const SKU_MARKETING_PAGES = [resolve('src/pages/operator.astro'), resolve('src/pages/agent.astro')]
const MARKETING_FENCED_TERMS: Array<{ label: string; pattern: RegExp }> = [
  {
    label: '"compliant" (no compliance claim without counsel review)',
    pattern: /\bcompliant\b/i,
  },
  {
    label: '"AI Workforce" (competitor trademark)',
    pattern: /\bAI Workforce\b/i,
  },
  {
    label: '"AI Operating System" (competitor trademark)',
    pattern: /\bAI Operating System\b/i,
  },
  {
    label: '"litigation insurance" (overclaim)',
    pattern: /\blitigation insurance\b/i,
  },
]

describe('SKU marketing pages fenced-term guard', () => {
  it('finds the SKU page sources (sanity)', () => {
    for (const page of SKU_MARKETING_PAGES) {
      expect(() => readFileSync(page, 'utf-8')).not.toThrow()
    }
  })

  for (const page of SKU_MARKETING_PAGES) {
    for (const { label, pattern } of MARKETING_FENCED_TERMS) {
      it(`${page.split('/').slice(-1)[0]} must not contain fenced term: ${label}`, () => {
        const content = stripComments(readFileSync(page, 'utf-8'))
        expect(pattern.test(content)).toBe(false)
      })
    }
  }
})

describe('user-facing copy guardrails', () => {
  it('finds shipped user-facing Astro surfaces to check (sanity)', () => {
    expect(userFacingSurfaceFiles.length).toBeGreaterThan(0)
  })

  for (const { label, pattern } of USER_FACING_COPY_GUARDS) {
    it(`must enforce: ${label}`, () => {
      const violations: string[] = []
      for (const file of userFacingSurfaceFiles) {
        const content = stripComments(readFileSync(file, 'utf-8'))
        if (pattern.test(content)) {
          violations.push(file.replace(SRC_ROOT, 'src'))
        }
      }
      expect(violations).toEqual([])
    })
  }
})

// ============================================================================
// Portal list-row registry — UI-PATTERNS R7 enforcement.
//
// List-row markup drifted across portal surfaces (proposals, invoices,
// documents) when Stitch generated each screen in isolation. The registry
// collapses the pattern to one component (`PortalListItem.astro`) + two
// helper modules (`src/lib/portal/{formatters,status}.ts`). These tests
// enforce the registry at CI time so drift fails the build, not review.
//
// Scope: every `src/pages/portal/*/index.astro` EXCEPT the home dashboard
// (which iterates its timeline, not list rows). New portal list surfaces
// auto-enroll — explicit exceptions go in LIST_INDEX_ALLOWLIST with a
// comment explaining why.
// ============================================================================

const PORTAL_INDEX_ROOT = resolve('src/pages/portal')
const PORTAL_HOME = resolve('src/pages/portal/index.astro')

/**
 * Allowlist for portal list-index files that legitimately cannot use
 * `PortalListItem` (e.g., because they iterate something that isn't a
 * list-row card — timeline, form fields, milestone rail). Each entry needs
 * a comment explaining why.
 */
const LIST_INDEX_ALLOWLIST: string[] = [
  // `engagement/index.astro` is the engagement DETAIL surface (one
  // engagement per client), not a list of engagements. Its milestone
  // rendering is a vertical-timeline with marker-ring state semantics, not
  // a repeating card — a different primitive than `PortalListItem`. Track
  // as a follow-up if milestone rail drifts or gains a second use.
  resolve('src/pages/portal/engagement/index.astro'),
  // `products/hosted-agent/index.astro` is the Hosted Agent status surface
  // (one subscription per render), not a list of products. Its `.map(` is
  // the setup-journey stepper: numbered steps with whose-move state badges
  // (done / your turn / our team / up next) — milestone-rail semantics like
  // the engagement surface above, not list-row cards.
  resolve('src/pages/portal/products/hosted-agent/index.astro'),
  // `products/operator/index.astro` is the Operator dashboard
  // landing (one customer per render), not a list of products. The
  // small `.map(roles, …)` inside the sidebar renders a bullet list of
  // granted role names (principal / staff / compliance) — text
  // items inside a chrome card, not a list-row card surface.
  resolve('src/pages/portal/products/operator/index.astro'),
  // `products/operator/[instance]/calendar/index.astro` is the Operator
  // calendar agenda (#872). It renders list rows through the
  // dedicated <CalendarItemRow> primitive (mirrors DraftRow's
  // justification — the six-cell calendar vocabulary, time-range /
  // title / type / source / matter / conflict, does not fit
  // PortalListItem's status or document variants). The .map( hits on
  // this page render the filter form's type checkboxes and sort
  // <option>s, not list rows. The agenda itself is rendered through
  // <CalendarAgenda>, which iterates via <CalendarItemRow>.
  resolve('src/pages/portal/products/operator/[instance]/calendar/index.astro'),
  // `products/operator/[instance]/settings/advanced/index.astro` is the
  // customer.yaml editor (#877): a structured FORM, not a list
  // surface. The only `.map(` on the page is the frontmatter
  // `resolved.errors.map((e) => e.path)` call that joins validation-
  // error paths into a status banner. Form sections (PersonaFields,
  // EscalationFields, BusinessHoursFields, ConnectorsFields,
  // ScopeFields) live under
  // `src/components/portal/operator/customer-yaml-editor/` and
  // render typed inputs per field group, not list-row cards. The
  // PortalListItem primitive is the wrong shape here. There is no
  // status/document repeating-card vocabulary to enforce.
  resolve('src/pages/portal/products/operator/[instance]/settings/advanced/index.astro'),
  // `products/operator/[instance]/connections/index.astro` (§5.8) iterates connectors
  // through the dedicated <ConnectionRowCard> primitive, not PortalListItem. A
  // connection row's vocabulary (capability + adapter/health + custody badge +
  // custody description + the operable OAuth/write-only-secret controls) does
  // not fit either of PortalListItem's two variants (status / document). Same
  // justification as DraftRow / MatterRow / AuditEntryRow / CalendarItemRow /
  // NotificationRow. The .map( hits render the read-slot and operable-slot
  // connector lists; both go through <ConnectionRowCard>.
  resolve('src/pages/portal/products/operator/[instance]/connections/index.astro'),
  // `products/operator/[instance]/configure/index.astro` (§5.6) renders config FIELD rows
  // (skill name + on/off, action-class + governance floor) as plain text <li>s
  // inside config section cards — not the PortalListItem status/document
  // repeating-card vocabulary. Same justification as settings/advanced (a
  // structured config surface, not a list of records). The .map( hits iterate
  // the skill list and the action-class governance rows; neither carries a
  // money/status/document cell that PortalListItem's variants model.
  resolve('src/pages/portal/products/operator/[instance]/configure/index.astro'),
  // `products/operator/[instance]/team/index.astro` (§5.7) renders the people-on-this-
  // account roster as identity rows (name + email/last-login, away badge, role
  // chips) inside the dual-mode read/operable slots — not the PortalListItem
  // status/document record-row vocabulary. The .map( iterates members; the
  // people_access domain is Read + Request at launch (ADR 0041).
  resolve('src/pages/portal/products/operator/[instance]/team/index.astro'),
  // `products/operator/[instance]/index.astro` is the operator ONE-PAGER
  // (console blueprint §5, amended 2026-07-15): the whole configuration
  // rendered inline as a document, not a list of records. Its .map( hits are
  // the sticky anchor-rail links and the Access section's connector rows —
  // the latter render through the dedicated <ConnectionRowCard> primitive
  // (same justification as the connections act surface below); duties and
  // people render through the shared <OperatorWork>/<OperatorPeople> viewers.
  resolve('src/pages/portal/products/operator/[instance]/index.astro'),
  // `products/operator/[instance]/settings/index.astro` is the settings hub — a
  // NAVIGATION menu, not a list of records. The `.map(` iterates
  // SETTINGS_LINKS (label + description → link) to render nav rows pointing
  // at the sub-surfaces (Connections / Users / Advanced). Menu links are not
  // the PortalListItem status/document record-row vocabulary; same category
  // as the other Operator sub-pages above.
  resolve('src/pages/portal/products/operator/[instance]/settings/index.astro'),
  // `products/operator/[instance]/onboarding/index.astro` (§6) renders the three
  // get-started steps as numbered guidance cards (step number, title,
  // description, honest status badge) linking to Team/Connections/Calibration —
  // not the PortalListItem status/document record-row vocabulary. The .map(
  // iterates the derived steps; a step with no signal reads "to do", never a
  // fabricated completion.
  resolve('src/pages/portal/products/operator/[instance]/onboarding/index.astro'),
]

/** Collect every `index.astro` under `src/pages/portal/` EXCEPT the home. */
function collectPortalListIndexFiles(): string[] {
  const files: string[] = []
  function walk(dir: string): void {
    for (const entry of readdirSync(dir)) {
      const fullPath = join(dir, entry)
      const stat = statSync(fullPath)
      if (stat.isDirectory()) {
        walk(fullPath)
      } else if (entry === 'index.astro' && fullPath !== PORTAL_HOME) {
        files.push(fullPath)
      }
    }
  }
  walk(PORTAL_INDEX_ROOT)
  return files.filter((f) => !LIST_INDEX_ALLOWLIST.includes(f))
}

const portalListIndexFiles = collectPortalListIndexFiles()

describe('portal list-row registry: UI-PATTERNS R7 enforcement', () => {
  it('finds at least one portal list-index file to check (sanity)', () => {
    // If this fails, the file collection logic broke — not the registry.
    expect(portalListIndexFiles.length).toBeGreaterThan(0)
  })

  // ----------------------------------------------------------------
  // Presence assertion.
  //
  // If the file iterates (`.map(`), it must render through
  // `<PortalListItem`. Class-reorder evasion (the Devil's Advocate's
  // objection to "no forbidden markup string" assertions) is defeated
  // because this asserts PRESENCE of the primitive, not absence of
  // specific class strings.
  // ----------------------------------------------------------------
  for (const file of portalListIndexFiles) {
    const rel = file.replace(resolve('.') + '/', '')
    it(`${rel} — must render list rows through <PortalListItem>`, () => {
      const content = readFileSync(file, 'utf-8')
      const iteratesList = /\.map\(/.test(content)
      if (!iteratesList) return // not a list surface, no assertion
      const usesPrimitive = content.includes('<PortalListItem')
      expect(
        usesPrimitive,
        `${rel} iterates with .map( but does not render through <PortalListItem>. ` +
          `Portal list-row markup must go through src/components/portal/PortalListItem.astro.`
      ).toBe(true)
    })
  }

  // ----------------------------------------------------------------
  // No local helper redefinition.
  //
  // Drift starts when a page defines its own formatDate / statusColorMap
  // instead of importing from src/lib/portal/{formatters,status}.ts.
  // Catch it at the declaration site.
  // ----------------------------------------------------------------
  const FORBIDDEN_LOCAL_DECLARATIONS: Array<{ name: string; pattern: RegExp }> = [
    {
      name: 'formatDate',
      pattern: /^\s*(?:const|function)\s+formatDate\b/m,
    },
    {
      name: 'formatCurrency',
      pattern: /^\s*(?:const|function)\s+formatCurrency\b/m,
    },
    {
      name: 'statusColorMap',
      pattern: /^\s*const\s+statusColorMap\b/m,
    },
    {
      name: 'statusLabelMap',
      pattern: /^\s*const\s+statusLabelMap\b/m,
    },
    {
      name: 'typeLabels',
      // Matches `typeLabels` as a standalone const; does NOT match
      // `typeLabel` or `typeLabelMap` (detail pages use those for
      // one-off single-title mapping and are out of scope).
      pattern: /^\s*const\s+typeLabels\s*(?::|=)/m,
    },
  ]

  for (const file of portalListIndexFiles) {
    const rel = file.replace(resolve('.') + '/', '')
    for (const { name, pattern } of FORBIDDEN_LOCAL_DECLARATIONS) {
      it(`${rel} — must not redefine local helper \`${name}\``, () => {
        const content = readFileSync(file, 'utf-8')
        expect(
          pattern.test(content),
          `${rel} declares a local \`${name}\`. Import from ` +
            `src/lib/portal/formatters.ts or src/lib/portal/status.ts instead.`
        ).toBe(false)
      })
    }
  }
})

/**
 * Operator customer.yaml invariants.
 *
 * Guards the exact regression #776 shipped: a customer config with an invalid
 * `hermes_ref` fork tag (`v2026.5.16-smd.0`). Also bans `composio:` backends:
 * composio is retired (ADR 0020, 2026-05-30 revision) and the `composio:` prefix
 * is no longer accepted by the customer.yaml validator — this is a defensive
 * second layer so no committed config can reintroduce it. Narrow on purpose —
 * this guards two invariants, not the full config content (the config is data,
 * not code).
 *
 * @see docs/adr/0020-connector-strategy.md (composio retired)
 * @see docs/adr/0024-hermes-consumption-and-update-cadence.md (hermes_ref pin format)
 */
describe('operator customer.yaml invariants', () => {
  const customersRoot = resolve('operator/customers')
  // v{YYYY}.{M}.{D}@{40-hex-sha} — fork tags like -smd.N do not match.
  const HERMES_REF_RE = /^v\d{4}\.\d{1,2}\.\d{1,2}@[0-9a-f]{40}$/

  function customerYamls(): string[] {
    const out: string[] = []
    let entries: string[]
    try {
      entries = readdirSync(customersRoot)
    } catch {
      return out // no customers dir yet
    }
    for (const entry of entries) {
      if (entry.startsWith('_')) continue // _template scaffold, not a real customer
      const file = join(customersRoot, entry, 'customer.yaml')
      try {
        if (statSync(file).isFile()) out.push(file)
      } catch {
        // no customer.yaml in this dir
      }
    }
    return out
  }

  it('no committed customer.yaml uses a composio: backend or a hermes_ref fork tag', () => {
    for (const file of customerYamls()) {
      const rel = file.replace(resolve('.') + '/', '')
      const content = readFileSync(file, 'utf-8')
      expect(
        content.includes('composio:'),
        `${rel} uses a composio: backend — composio is retired and the prefix is ` +
          `no longer accepted by the validator (ADR 0020).`
      ).toBe(false)
      const match = content.match(/^\s*hermes_ref:\s*['"]?([^'"\n]+?)['"]?\s*$/m)
      if (match) {
        const ref = match[1].trim()
        expect(
          HERMES_REF_RE.test(ref),
          `${rel} hermes_ref "${ref}" is not v{YYYY}.{M}.{D}@{40-hex-sha} ` +
            `(fork tags like -smd.N are rejected per ADR 0024).`
        ).toBe(true)
      }
    }
  })
})

describe('operator client-portal surfaces stay vertical-agnostic (ADR 0052)', () => {
  // The Operator is a vertical-agnostic product (ADR 0052): client-portal
  // surfaces must not reintroduce law-vertical vocabulary or demo persona
  // names. A regression here means the "Matters"/case-data model or a hardcoded
  // persona crept back onto a shipped surface.
  //
  // Scope is the CLIENT portal only. Deliberately excluded:
  //   - the customer.yaml editor (customer-yaml-editor/): it authors per-customer
  //     config whose field names (e.g. matter_blocks for a law customer) are
  //     legitimate vertical config, not product structure — the same out-of-scope
  //     category as capability adapters and practice_areas (ADR 0052 "Out of
  //     scope").
  //   - the admin console (src/pages/admin/**): it observes/authors one specific,
  //     possibly-law customer, so per-customer law values are legitimate there.
  const OPERATOR_SURFACE_ROOTS = [
    resolve('src/pages/portal/products/operator'),
    resolve('src/components/portal/operator'),
  ]
  const EDITOR_EXCLUDE = resolve('src/components/portal/operator/customer-yaml-editor')

  const BANNED: ReadonlyArray<{ re: RegExp; label: string }> = [
    {
      re: /\bmatters?\b/i,
      label: 'matter(s) — use the generic opaque object reference (ADR 0052 §6)',
    },
    { re: /\bdeposition\b/i, label: 'deposition — law-vertical vocabulary' },
    { re: /\bhearing\b/i, label: 'hearing — law-vertical vocabulary' },
    { re: /\b(?:pre_suit|pre_trial)\b/i, label: 'PI litigation phase enum (ADR 0052)' },
    { re: /\bMarcus\b/, label: 'Marcus — demo persona name' },
    { re: /\bSusan\b/, label: 'Susan — demo persona name' },
  ]

  function operatorSurfaceFiles(): string[] {
    return OPERATOR_SURFACE_ROOTS.flatMap((root) => collectSourceFiles(root)).filter(
      (f) => !isWithinDir(f, EDITOR_EXCLUDE)
    )
  }

  it('no law-vertical vocabulary or persona names on client-portal operator surfaces', () => {
    const violations: string[] = []
    for (const file of operatorSurfaceFiles()) {
      const rel = file.replace(resolve('.') + '/', '')
      const content = readFileSync(file, 'utf-8')
      for (const { re, label } of BANNED) {
        if (re.test(content)) violations.push(`${rel}: ${label}`)
      }
    }
    expect(violations, violations.join('\n')).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Portal form-control kit — UI-PATTERNS Rule 8 enforcement (ADR 0082).
//
// The client portal's settled register is loud Plainspoken (Captain,
// 2026-07-29); the calm migration is retired and its guard removed. What
// Rule 8 enforces now is CONTROL consistency: every non-hidden <input>,
// <select>, or <textarea> on a portal surface renders through the shared kit
// in src/components/portal/form/ — one control height, one border weight,
// width delegated to the layout column (never an intrinsic-width select).
//
// FORM_KIT_PENDING is the set of pre-kit files still hand-rolling controls.
// It only SHRINKS as surfaces migrate; the end state is an empty list. New
// portal files are enforced from day one (they are not in PENDING), so
// hand-rolled controls cannot re-enter the portal.
// ---------------------------------------------------------------------------
const FORM_KIT_ROOTS = [resolve('src/pages/portal'), resolve('src/components/portal')]

// The kit's own primitives contain the raw tags by definition.
const FORM_KIT_DIR = 'src/components/portal/form/'

const FORM_KIT_PENDING: string[] = [
  // Advanced-editor field components — the ONLY sanctioned holdouts (ADR
  // 0082 sweep, 2026-07-29). The surface is unlinked and broken (see
  // settings/index.astro SETTINGS_LINKS note); migrate when the editor
  // itself is restored.
  'src/components/portal/operator/customer-yaml-editor/BusinessHoursFields.astro',
  'src/components/portal/operator/customer-yaml-editor/ConnectorRow.astro',
  'src/components/portal/operator/customer-yaml-editor/EscalationFields.astro',
  'src/components/portal/operator/customer-yaml-editor/PersonaRow.astro',
  'src/components/portal/operator/customer-yaml-editor/ScopeFields.astro',
]

// PENDING entries are repo-relative paths; the collected files are absolute.
// relOf normalizes an absolute path to the repo-relative form for comparison
// (constant `resolve('.')` only — no variable ever enters path.join/resolve).
function relOf(absPath: string): string {
  return absPath.replace(resolve('.') + '/', '')
}

// A file "has controls" when a non-hidden input/select/textarea tag appears
// in its markup (comments stripped). Hidden inputs carry form state, not
// geometry, and need no kit. Tag attributes may span lines: [^>]* crosses
// newlines because the class excludes only `>`.
function hasHandRolledControls(src: string): boolean {
  const tags = src.match(/<(input|select|textarea)\b[^>]*/g) ?? []
  return tags.some((tag) => !/type=["']hidden["']/.test(tag))
}

const KIT_IMPORT_RE = /['"][^'"]*\/form\/(Field|TextInput|SelectField|SubmitButton)\.astro['"]/

describe('portal form-control kit: UI-PATTERNS R8 enforcement (ADR 0082)', () => {
  const candidates = FORM_KIT_ROOTS.flatMap((root) =>
    existsSync(root) ? collectSourceFiles(root) : []
  ).filter((f) => !relOf(f).startsWith(FORM_KIT_DIR))

  it('finds portal files to check (sanity)', () => {
    expect(candidates.length).toBeGreaterThan(0)
  })

  for (const file of candidates) {
    const rel = relOf(file)
    if (FORM_KIT_PENDING.includes(rel)) continue
    it(`${rel} — controls render through the form kit (Rule 8)`, () => {
      const src = stripComments(readFileSync(file, 'utf-8'))
      if (!hasHandRolledControls(src)) return
      expect(
        KIT_IMPORT_RE.test(src),
        `${rel} renders a non-hidden input/select/textarea without importing the portal form kit (src/components/portal/form/)`
      ).toBe(true)
    })
  }

  // Keep PENDING honest: every entry must still exist AND still hand-roll
  // controls without the kit. Once a file is migrated (or loses its
  // controls), it MUST be removed from PENDING so the guard enforces it.
  it('FORM_KIT_PENDING has no stale entries (migrated files must be removed)', () => {
    const stale: string[] = []
    for (const rel of FORM_KIT_PENDING) {
      if (!existsSync(rel)) {
        stale.push(`${rel} (no longer exists)`)
        continue
      }
      const src = stripComments(readFileSync(rel, 'utf-8'))
      if (!hasHandRolledControls(src) || KIT_IMPORT_RE.test(src)) {
        stale.push(`${rel} (now on the kit or control-free — remove from PENDING)`)
      }
    }
    expect(stale, `stale PENDING entries:\n${stale.join('\n')}`).toEqual([])
  })
})

describe('operator send-posture doctrine guard (recipient-aware send; ADR 0025/0035/0055)', () => {
  // The "nothing ever sends" regression regrew every time from (a) the retired
  // universal drafts-only doctrine re-taught in prose and (b) the vestigial
  // per-skill trust_ceiling scalar. This block is the string-HYGIENE backstop;
  // the anti-regression GUARANTEE is behavioral and lives in the golden
  // enforcement tests (operator/adapter/tests/test_external_send_internal.py +
  // test_recipient_classifier.py, and the overlay evaluate_tool_call tests) —
  // drop the recipient classification and those go red with no banned string
  // present. The one source is operator/references/send-posture.md.
  const RETIRED_DOCTRINE: ReadonlyArray<{ re: RegExp; label: string }> = [
    {
      re: /no outbound external send without confirmation/i,
      label: 'retired invariant-#2 wording',
    },
    { re: /agent drafts only/i, label: 'retired universal drafts-only framing' },
    { re: /always draft-for-review/i, label: 'retired universal draft-for-review framing' },
    { re: /drafts only unless/i, label: 'retired universal drafts-only framing' },
  ]
  const DOCTRINE_ROOTS = [
    resolve('operator/skills'),
    resolve('operator/references'),
    resolve('operator/safety-substrate'),
    resolve('operator/verticals'),
    resolve('docs/specs/operator'),
  ]

  function collectDoctrineFiles(dir: string): string[] {
    const out: string[] = []
    let entries: string[]
    try {
      entries = readdirSync(dir)
    } catch {
      return out
    }
    for (const entry of entries) {
      // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- dir is a hardcoded repo root; entry is readdirSync output, not user input (same pattern as collectSourceFiles above).
      const full = join(dir, entry)
      let st
      try {
        st = statSync(full)
      } catch {
        continue
      }
      // Skip test fixtures — they legitimately narrate the retired doctrine when
      // documenting the reform (e.g. the invariant-2 test docstring).
      if (st.isDirectory()) {
        if (entry === 'tests') continue
        out.push(...collectDoctrineFiles(full))
      } else if (/\.(md|py)$/.test(entry)) {
        out.push(full)
      }
    }
    return out
  }

  it('no retired universal drafts-only doctrine wording (defer to send-posture.md)', () => {
    const violations: string[] = []
    for (const root of DOCTRINE_ROOTS) {
      for (const file of collectDoctrineFiles(root)) {
        const content = readFileSync(file, 'utf-8')
        const rel = file.replace(resolve('.') + '/', '')
        for (const { re, label } of RETIRED_DOCTRINE) {
          if (re.test(content)) violations.push(`${rel}: ${label}`)
        }
      }
    }
    expect(
      violations,
      `retired send-posture doctrine re-introduced:\n${violations.join('\n')}\n` +
        `State the authored ceiling and defer to operator/references/send-posture.md.`
    ).toEqual([])
  })

  it('no SKILL.md carries the retired metadata.smd.trust_ceiling scalar (ADR 0056)', () => {
    const offenders: string[] = []
    for (const file of collectDoctrineFiles(resolve('operator/skills'))) {
      if (!file.endsWith('SKILL.md')) continue
      const content = readFileSync(file, 'utf-8')
      if (/^\s*trust_ceiling:/m.test(content)) {
        offenders.push(file.replace(resolve('.') + '/', ''))
      }
    }
    expect(
      offenders,
      `retired per-skill trust_ceiling scalar (replaced by persona exposure, ADR 0056):\n` +
        offenders.join('\n')
    ).toEqual([])
  })
})

describe('retired persona name stays retired (Captain directive 2026-07-13)', () => {
  // The pilot/A&P persona's original name was retired by repeated Captain
  // directive: the display name went first (name: Operator, 2026-07-02), and
  // the slug + every active reference were renamed 2026-07-13 after the word
  // kept resurfacing in configs, fixtures, and agent conversation. This guard
  // makes any reintroduction a CI failure.
  //
  // Historical records keep the word legitimately and are excluded: dated
  // grading run logs (rewriting dated records would falsify them). This test
  // file excludes itself (it must spell the pattern to ban it). The A&P
  // correspondence archive was a third exclusion until the engagement material
  // moved to venturecrane/engagements; it is no longer in this tree to scan.
  const RETIRED_NAME = /quinn/i
  const SCAN_ROOTS = ['operator', 'src', 'tests', 'scripts', 'docs/design', 'docs/handbook']
  const EXCLUDED = [resolve('operator/grading/runs'), resolve('tests/forbidden-strings.test.ts')]

  function scanFiles(dir: string): string[] {
    const out: string[] = []
    let entries: string[]
    try {
      entries = readdirSync(dir)
    } catch {
      return out
    }
    for (const entry of entries) {
      // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- dir is a hardcoded repo root; entry is readdirSync output, not user input (same pattern as collectSourceFiles above).
      const full = join(dir, entry)
      if (EXCLUDED.some((e) => isWithinDir(full, e))) continue
      let st
      try {
        st = statSync(full)
      } catch {
        continue
      }
      if (st.isDirectory()) {
        if (entry === 'node_modules') continue
        out.push(...scanFiles(full))
      } else if (
        ['.ts', '.tsx', '.astro', '.md', '.yaml', '.yml', '.json', '.py', '.sh'].includes(
          extname(entry)
        )
      ) {
        out.push(full)
      }
    }
    return out
  }

  it('the retired persona name appears in no active file', () => {
    const offenders: string[] = []
    for (const root of SCAN_ROOTS) {
      for (const file of scanFiles(resolve(root))) {
        if (RETIRED_NAME.test(readFileSync(file, 'utf-8'))) {
          offenders.push(file.replace(resolve('.') + '/', ''))
        }
      }
    }
    expect(
      offenders,
      `the retired persona name must not return (Captain directive; historical records excluded):\n` +
        offenders.join('\n')
    ).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Console vocabulary guard (console blueprint §6 — locked once, then enforced).
// The §6 table is decided by Captain exactly once; this guard keeps the retired
// display labels from re-entering the operator console surfaces, so naming
// stops being re-litigated per page. Comments are stripped first — only real
// template text can trip it. Registry ids and route paths are deliberately NOT
// scanned (labels rename; identifiers stay stable).
// ---------------------------------------------------------------------------
describe('console vocabulary guard (blueprint §6 — retired display labels)', () => {
  const RETIRED_VOCAB: ReadonlyArray<{ re: RegExp; label: string }> = [
    { re: /The work\b/, label: '"The work" (§6: renamed to Duties)' },
    { re: /Today:/, label: '"Today:" autonomy row label (§6: renamed to Autonomy:)' },
    { re: /Can become/, label: '"Can become" (§6: renamed to Can be raised to:)' },
  ]
  const VOCAB_ROOTS = [
    resolve('src/components/portal/operator'),
    resolve('src/pages/portal/products/operator'),
    resolve('src/pages/admin/operator'),
  ]
  const files = VOCAB_ROOTS.flatMap((root) =>
    existsSync(root) ? collectSourceFiles(root) : []
  ).filter((f) => f.endsWith('.astro'))

  it('finds console files to scan (sanity)', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  for (const file of files) {
    const rel = relOf(file)
    it(`${rel} — no retired vocabulary (§6)`, () => {
      const src = stripComments(readFileSync(file, 'utf-8'))
      const hits = RETIRED_VOCAB.filter(({ re }) => re.test(src)).map(({ label }) => label)
      expect(hits, `${rel} uses retired labels: ${hits.join(', ')}`).toEqual([])
    })
  }
})

// ---------------------------------------------------------------------------
// MOVED to venturecrane/engagements -> tests/engagement-guards.test.ts
//
// Two guards used to live here and read client letters directly:
//
//   - correspondence DRAFT provenance header (Law 5; incident 2026-07-26,
//     where a "review" invented two commercial terms into an approved draft)
//   - dossier sentinels never appear in correspondence (Law 2 leakage rule)
//
// Correspondence and dossiers now live in the private engagements repo, and
// ss-console CI is deliberately NOT given a token that can read client data.
// So the guards moved to the material rather than the material being exposed
// to the guards. They did not weaken in the move: each now opens with a
// sanity assertion, where the DRAFT check here passed vacuously on an empty
// draft set, and each was proven red-then-green against injected defects.
//
// Do not re-add a client-data scan to this file. If the engagements repo's
// guards need extending, extend them there.
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Skill prose must not carry a matter-number-shaped example (ss#2168, AC2).
//
// THE DEFECT THIS CLOSES. The 2026-07-31 provenance audit found 37 of 51 skills
// instructing "refer to the matter by its NUMBER (e.g. 2026-PI-101)". The
// instruction demands an identifier on every line and hands the model a
// plausible one, so on a seat with a failed or absent connector the model emits
// the example — which is exactly the phantom 2026-PI-101 / -103 / -105 rows that
// turned up in ashton-price's escalation ledger for matters that do not exist in
// that firm's tenant.
//
// The prose itself was remediated (examples replaced with <matter-id>
// placeholders; verified 2026-08-13, zero phantom identifiers across 58 skills).
// What never landed was anything stopping the pattern coming back. AC2 asked for
// exactly this guard and it did not exist — which is why ss#2168 sat open with
// its first AC met, and why a fix with no guard is one careless edit from being
// unfixed.
//
// WHY THE PATTERN IS BORROWED, NOT INVENTED. It is the overlay's own
// _MATTER_NUM_RE (shared/matter_gate.py) — the regex the runtime uses to decide
// what counts as a cited matter. A guard written against a different pattern
// would police something other than what the gate reacts to.
// ---------------------------------------------------------------------------

/** The overlay's `_MATTER_NUM_RE`, kept spelling-identical on purpose. */
const MATTER_NUM_RE =
  /\b(?:[A-Za-z]{2,4}-\d{4}-\d{2,5}|\d{2,4}-[A-Za-z]{2,4}-\d{2,5}|[A-Za-z]{2,4}-\d{4,6})\b/g

/**
 * Token classes this regex matches that are NOT fabricated matter numbers.
 *
 * Deliberately prefix classes with a stated reason, rather than a list of
 * literal strings: a literal allowlist grows on every edit, and eventually
 * someone adds a real offender to it to make the build green. Each entry here
 * has to be a class a reader can evaluate on sight.
 */
const NOT_A_MATTER_NUMBER: ReadonlyArray<{ re: RegExp; why: string }> = [
  { re: /^ISO-\d+$/i, why: 'standards reference (ISO-8601), not a matter' },
  { re: /^ADR-\d+$/i, why: 'architecture decision record reference' },
  {
    re: /^(?:sect|ev|ccp|crc)-[\d-]+$/i,
    why: 'statute / code-section citation — real legal writing, and the runtime gate drops unresolved tokens rather than judging them',
  },
  {
    re: /^ZZ-9999-0001$/,
    why: 'the deliberate never-real sentinel operator-self-test uses to PROVE the fabrication guard refuses; removing it would delete a control',
  },
]

describe('skill prose carries no matter-number-shaped example (ss#2168)', () => {
  const SKILLS_ROOT = resolve('operator/skills')

  /**
   * Skill directory names are a closed shape: lowercase kebab-case, no dots, no
   * separators. Validating before joining keeps a directory name from ever being
   * a path fragment — belt and braces on a test-only read, but the alternative is
   * suppressing the rule, and a suppressed rule teaches the next reader that this
   * shape is fine.
   */
  const SKILL_DIR_RE = /^[a-z0-9][a-z0-9-]*$/

  // The path is composed by concatenation rather than join()/resolve() ON
  // PURPOSE. The directory name is already constrained to the closed shape above
  // — no dot, no slash, no separator can survive SKILL_DIR_RE — so the join is
  // safe either way, but semgrep's path-traversal rule is a taint rule that
  // cannot see the filter and would flag it forever. Concatenating after
  // validating keeps the check honest instead of parking a nosemgrep comment
  // here, which is the thing that actually rots: a suppression teaches the next
  // reader the shape is fine, where this spells out why it is.
  const skillFiles = existsSync(SKILLS_ROOT)
    ? readdirSync(SKILLS_ROOT)
        .filter((d) => SKILL_DIR_RE.test(d))
        .map((d) => `${SKILLS_ROOT}/${d}/SKILL.md`)
        .filter((f) => existsSync(f))
    : []

  /** Matter-number-shaped tokens in `text`, minus classes legitimately shaped that way. */
  function offendingTokens(text: string): string[] {
    return [...new Set(text.match(MATTER_NUM_RE) ?? [])].filter(
      (tok) => !NOT_A_MATTER_NUMBER.some(({ re }) => re.test(tok))
    )
  }

  it('finds skills to scan at all', () => {
    // Without this the whole describe passes vacuously the day the skills move,
    // and green would mean "found nothing" rather than "nothing to find".
    expect(skillFiles.length).toBeGreaterThan(20)
  })

  it('the detector fires on a known-bad string', () => {
    // The control. A guard that cannot fail has measured nothing, and this one's
    // failure mode is silent: an over-broad exemption empties every result while
    // the suite stays green.
    expect(offendingTokens('refer to the matter by its number (e.g. 2026-PI-101)')).toEqual([
      '2026-PI-101',
    ])
    expect(offendingTokens('PI-2026-0001 and PI-123456')).toEqual(['PI-2026-0001', 'PI-123456'])
  })

  it('the exemptions do not swallow a real offender', () => {
    // The inverse control: prove the allowlist is narrow. A statute cite passes,
    // and a matter number sitting right beside it still does not.
    expect(offendingTokens('under sect-2033-290, see matter 2026-PI-104')).toEqual(['2026-PI-104'])
  })

  for (const file of skillFiles) {
    const rel = file.replace(resolve('.') + '/', '')
    it(`${rel} — no fabricated matter identifier in prose`, () => {
      const tokens = offendingTokens(readFileSync(file, 'utf-8'))
      expect(
        tokens,
        `${rel} supplies matter-number-shaped example(s) the model will emit under failure: ${tokens.join(', ')}. ` +
          'Demand the identifier FROM THE READ RECORD and use a non-emittable placeholder such as <matter-id>.'
      ).toEqual([])
    })
  }
})

// The establishment skills must warn that an ordered-list marker is a digit
// (ss#2212).
//
// The digit invariant refuses any digit in a spec body outside a `{{profile.*}}`
// token, and it counts `1.` and `2.` at the head of a line. Found on
// pilot-smokeball 2026-08-02: a specification written as a numbered list of rules
// was refused with `REFUSED: 5 digit(s) in spec.md outside a profile token`, one
// per list item; the identical content in bullets installed cleanly
// (vfy_01KZ288YZAPW5GNY180DRNX2Q1).
//
// A numbered list is the natural way to write "rules", so the constraint reads as
// a bug the first time a firm hits it. The fix is prose in the skills rather than
// a wider gate: nothing about the invariant changes, and no line position gains
// the ability to carry an asserted measurement. Prose with no guard is a
// suggestion, though, so this pins it the same way ss#2168's example ban is
// pinned.
describe('establishment skills warn that a numbered list is refused (ss#2212)', () => {
  // Both skills write a spec body through the same invariant. `document-library-
  // establishment` does not, so it is deliberately absent.
  const SPEC_WRITING_SKILLS = ['voice-establishment', 'shape-establishment']

  for (const skill of SPEC_WRITING_SKILLS) {
    it(`${skill} tells the model to use bullets, not numbers`, () => {
      // Whitespace-tolerant on purpose: these files are hard-wrapped, so the
      // phrase routinely straddles a newline plus indent. A regex with a literal
      // space passes or fails on where prettier happened to break the line,
      // which is a check that answers a question about formatting rather than
      // about content. (It failed exactly that way on first run.)
      const body = readFileSync(resolve('operator/skills', skill, 'SKILL.md'), 'utf-8')
      expect(
        /bullets,\s+never\s+(as\s+)?a\s+numbered\s+list/i.test(body),
        `operator/skills/${skill}/SKILL.md no longer warns that an ordered-list marker counts ` +
          'as a digit. Without it the first firm to write its rules as "1. ... 2. ..." gets ' +
          'the whole specification refused and reads the control as a defect.'
      ).toBe(true)
      // The warning is worth nothing if it does not say what breaks, so pin the
      // mechanism too: a reader who sees only "use bullets" will delete it as
      // style advice.
      expect(
        /digit\s+invariant\s+counts\s+an\s+ordered-list\s+marker/i.test(body),
        `operator/skills/${skill}/SKILL.md states the rule without its reason. Keep the ` +
          'mechanism next to it or the next editor removes it as a style preference.'
      ).toBe(true)
    })
  }
})

// Seat-reaching scripts must not print a process's command line (ss#2218).
//
// `seat-probe.sh` re-execs the probe as `runuser -- env ${ENVV} ...`, which puts
// the gateway's entire environment on the wrapper's own argv. That is deliberate
// and load-bearing: it is how the probe reaches the seat with the credentials it
// needs. The consequence is that any flag which prints a command line is an
// exfiltration primitive in these files, not a debugging convenience.
//
// On 2026-08-10 a probe ran `pgrep -af establish_intake`, matched its own
// wrapper, and printed ANTHROPIC_API_KEY, the Smokeball client id and secret and
// more into a session transcript (P1). The prose warning landed with the fix;
// this is what stops the next edit removing it by accident.
describe('seat-reaching scripts never print a process command line (ss#2218)', () => {
  const SEAT_SCRIPTS = ['operator/bin/seat-probe.sh']

  // `pgrep -a`, `pgrep -af`, `ps e`, `ps auxe`. Matches the flag cluster, not a
  // fixed string, so `-fa` and `-af` are both caught.
  const ARGV_PRINTERS = /\b(pgrep\s+-[a-z]*a[a-z]*|ps\s+(e\b|aux?e\b))/

  for (const rel of SEAT_SCRIPTS) {
    const body = readFileSync(resolve(rel), 'utf-8')

    it(`${rel} contains no argv-printing invocation`, () => {
      // Comments are where the ban is explained, so they must not trip it.
      const code = body
        .split('\n')
        .filter((l) => !/^\s*#/.test(l))
        .join('\n')
      const hit = ARGV_PRINTERS.exec(code)
      expect(
        hit?.[0] ?? null,
        `${rel} invokes ${hit?.[0]} — on a seat this prints the gateway environment, ` +
          'secret values included (ss#2218). Match a pattern that cannot match the ' +
          'wrapper and print pids only.'
      ).toBeNull()
    })

    it(`${rel} still carries the ban in prose`, () => {
      // A guard with no explanation gets deleted by whoever hits it next.
      expect(
        /NEVER run `pgrep -a`/.test(body),
        `${rel} lost the ss#2218 warning. The rule is not obvious from the code: ` +
          'the env is on the wrapper argv by design, and the comment is what says why.'
      ).toBe(true)
    })
  }

  it('the pattern catches the exact invocation from the incident', () => {
    // The inverse control. Without this the regex could match nothing at all and
    // every assertion above would pass on an empty check.
    expect(ARGV_PRINTERS.test('pgrep -af establish_intake')).toBe(true)
    expect(ARGV_PRINTERS.test('ps auxe')).toBe(true)
    // ...and leaves the safe form alone.
    expect(ARGV_PRINTERS.test('pgrep -f "hermes.*gateway run"')).toBe(false)
  })
})

// ---------------------------------------------------------------------------
// Tree walker for the two guards below (same shape as the retired-persona
// scan above): every file with one of `exts` under `dir`, node_modules and the
// caller's exclusions skipped, unreadable entries ignored.
// ---------------------------------------------------------------------------
function walkTree(dir: string, exts: string[], excluded: string[]): string[] {
  const out: string[] = []
  let entries: string[]
  try {
    entries = readdirSync(dir)
  } catch {
    return out
  }
  for (const entry of entries) {
    // nosemgrep: javascript.lang.security.audit.path-traversal.path-join-resolve-traversal.path-join-resolve-traversal -- dir is a hardcoded repo root; entry is readdirSync output, not user input.
    const full = join(dir, entry)
    if (excluded.some((e) => isWithinDir(full, e))) continue
    let st
    try {
      st = statSync(full)
    } catch {
      continue
    }
    if (st.isDirectory()) {
      if (entry === 'node_modules') continue
      out.push(...walkTree(full, exts, excluded))
    } else if (exts.includes(extname(entry))) {
      out.push(full)
    }
  }
  return out
}

/** Python source with comment lines dropped, so an explanation cannot trip a gate. */
function stripPythonComments(content: string): string {
  return content
    .split('\n')
    .filter((line) => !/^\s*#/.test(line))
    .join('\n')
}

// ---------------------------------------------------------------------------
// Delivered documents promise no future behaviour (claims review 2026-09-04,
// B6). The medchron chronology's limitations section told the reader "tell us
// and we will extend the chronology" in the firm's voice: a first-person
// commitment nobody contracted, shipped inside a delivered work product. The
// same Pattern A rule that governs the portal governs every Python module that
// renders text a client reads.
// ---------------------------------------------------------------------------
describe('operator Python renders no first-person future-behaviour promise (ss claims 2026-09-04 B6)', () => {
  const PROMISE = /\bwe will extend the\b/i
  const files = walkTree(resolve('operator'), ['.py'], [])

  it('finds operator Python to scan (sanity)', () => {
    expect(files.length).toBeGreaterThan(0)
  })

  it('no operator Python module carries "we will extend the"', () => {
    const offenders: string[] = []
    for (const file of files) {
      if (PROMISE.test(stripPythonComments(readFileSync(file, 'utf-8')))) {
        offenders.push(file.replace(resolve('.') + '/', ''))
      }
    }
    expect(
      offenders,
      'a delivered document may state what was reviewed; it may not promise what will be done next:\n' +
        offenders.join('\n')
    ).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// The other venture's mail domain stays out of SMD code (CLAUDE.md, "Contact
// Addresses"; claims review 2026-09-04, I15). Seventeen occurrences had
// accumulated, most in a skill reference COPYed into every seat image, and
// `tests/client-identity-gate.test.ts` allowlists the domain as "ours", so it
// could not catch them. Example addresses use `@example.com`. This scans the
// same four roots the review probed, comments included: the domain has no
// business in a comment either.
// ---------------------------------------------------------------------------
describe('the venturecrane mail domain appears nowhere in SMD code (CLAUDE.md contact addresses)', () => {
  const DOMAIN = /@venturecrane\.com/i
  const ROOTS = ['src', 'operator', 'scripts', 'workers']
  const EXTS = [
    '.ts',
    '.tsx',
    '.astro',
    '.md',
    '.yaml',
    '.yml',
    '.json',
    '.py',
    '.sh',
    '.toml',
    '.txt',
  ]

  it('the venturecrane address is in no file under src, operator, scripts, workers', () => {
    const offenders: string[] = []
    for (const root of ROOTS) {
      for (const file of walkTree(resolve(root), EXTS, [])) {
        if (DOMAIN.test(readFileSync(file, 'utf-8'))) {
          offenders.push(file.replace(resolve('.') + '/', ''))
        }
      }
    }
    expect(
      offenders,
      'SMD addresses are team@smd.services and scott@smd.services; examples use @example.com:\n' +
        offenders.join('\n')
    ).toEqual([])
  })
})

import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync, readdirSync } from 'fs'
import { resolve, join } from 'path'
import { INDUSTRIES, PACK_META } from '../src/lib/operator-packs/shared'

const srcDir = resolve('src')
const componentsDir = resolve('src/components')

function readComponent(name: string): string {
  return readFileSync(join(componentsDir, name), 'utf-8')
}

function readAllSrcFiles(): string[] {
  const files: string[] = []
  function walk(dir: string) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const fullPath = join(dir, entry.name)
      if (entry.isDirectory()) {
        walk(fullPath)
      } else if (entry.name.endsWith('.astro') || entry.name.endsWith('.ts')) {
        files.push(fullPath)
      }
    }
  }
  walk(srcDir)
  return files
}

// Marketing surfaces. The "no dollar amounts" check below applies to these
// files. The Operator SKU page (src/pages/operator.astro) is included:
// as of 2026-05-30 we pulled the published price and route pricing
// to the first conversation, so no dollar amount may appear on that surface
// either. New marketing sections SHOULD be added here so a future edit cannot
// accidentally publish a price.
//
// DELIBERATE EXEMPTION (do not "fix"): src/pages/agent.astro and
// src/pages/agent/thanks.astro are OMITTED from this list on purpose. The
// Hosted Agent SKU sells self-serve with published pricing per ADR 0067
// (decision-stack #51) and the positioning-spine decision log entry dated
// 2026-07-06. The exemption is scoped to those two pages only; every other
// marketing surface stays price-free, and the agent pages remain enrolled
// in all other content guards (forbidden-strings, voice scan below).
function readMarketingFiles(): string[] {
  return [
    resolve('src/pages/index.astro'),
    resolve('src/pages/operator.astro'),
    resolve('src/pages/about.astro'),
    resolve('src/pages/book.astro'),
    resolve('src/pages/packs/law-firm.astro'),
    resolve('src/pages/case-studies/personal-injury-law-firm.astro'),
    resolve('src/pages/packs/insurance.astro'),
    resolve('src/pages/packs/veterinary.astro'),
    resolve('src/pages/packs/title.astro'),
    resolve('src/pages/packs/accounting.astro'),
    resolve('src/pages/packs/ria.astro'),
    resolve('src/pages/packs/mortgage.astro'),
    resolve('src/pages/packs/dental.astro'),
    resolve('src/pages/packs/med-spa.astro'),
    resolve('src/pages/packs/marketing-agency.astro'),
    resolve('src/pages/packs/property-management.astro'),
    resolve('src/pages/packs/home-services.astro'),
    join(componentsDir, 'OperatorHero.astro'),
    join(componentsDir, 'About.astro'),
    join(componentsDir, 'Footer.astro'),
    join(componentsDir, 'JsonLd.astro'),
  ]
}

describe('component existence', () => {
  const expectedComponents = [
    'CtaButton.astro',
    'OperatorHero.astro',
    'About.astro',
    'Nav.astro',
    'Footer.astro',
    'JsonLd.astro',
  ]

  it.each(expectedComponents)('%s exists', (component) => {
    expect(existsSync(join(componentsDir, component))).toBe(true)
  })
})

describe('content integrity', () => {
  it('no dollar amounts published in marketing content', () => {
    const files = readMarketingFiles()
    const dollarPattern = /\$[\d,]+/
    for (const filePath of files) {
      const content = readFileSync(filePath, 'utf-8')
      expect(content, `Dollar amount found in ${filePath}`).not.toMatch(dollarPattern)
    }
  })
})

describe('voice standard', () => {
  // About.astro is intentionally excluded: SMD is positioned as a practitioner
  // firm (lawyer / doctor / craftsman model) where the founder *is* the firm.
  // The About founder bio uses Scott's first-person voice; the rest of the page
  // stays in firm-level "we" voice. See CLAUDE.md "Voice standard" practitioner-firm
  // exception.
  const marketingComponents = ['OperatorHero.astro']

  // Operator-forward home and the /why manifesto carry the lead argument as
  // long-form page prose, not components. They must hold the same firm-level
  // "we" voice. Decision (Operator-forward redesign): keep the strict component
  // regex and constrain page copy to pass it, rather than loosen the guardrail
  // for pages. First-person "I" stays confined to the test-excluded About.astro
  // component; it is never inlined into these page files.
  const marketingPages = [
    'src/pages/index.astro',
    'src/pages/operator.astro',
    'src/pages/case-studies/personal-injury-law-firm.astro',
    // The Hosted Agent storefront is price-exempt (see readMarketingFiles)
    // but holds firm "we" voice like every other marketing page.
    'src/pages/agent.astro',
  ]

  // Shared scan: flag standalone first-person "I " in the author's voice, after
  // stripping quoted spans (owner quotes such as "I can't take a day off" are
  // allowed). Identical heuristic for components and pages.
  function scanFirstPerson(content: string, label: string) {
    const lines = content.split('\n')
    for (const line of lines) {
      if (line.includes('quote:') || line.includes('"I ') || line.includes("'I ")) continue
      const stripped = line.replace(/['"][^'"]*['"]/g, '')
      expect(stripped, `First-person "I " found in ${label}: ${line.trim()}`).not.toMatch(
        /\bI\s(?!can't|don't|have|personally|text)/
      )
    }
  }

  it.each(marketingComponents)('%s does not use first-person singular "I "', (component) => {
    scanFirstPerson(readComponent(component), component)
  })

  it.each(marketingPages)('%s does not use first-person singular "I "', (page) => {
    scanFirstPerson(readFileSync(resolve(page), 'utf-8'), page)
  })
})

describe('marketing structure: firm-with-flagship (locked)', () => {
  // Encodes the structure locked in docs/marketing/positioning-spine.md as STABLE
  // INVARIANTS, not brittle section order. The site went in circles because an agent
  // kept choosing the frame and the next pass reversed it. These assert the
  // Captain-ratified frame: SMD is a software & AI solutions firm (the frame), the
  // Operator is the flagship (cradled, not the whole site), and the assessment is the
  // ONE front door. Prettier wraps prose, so phrase checks normalize whitespace.
  const flat = (s: string) => s.replace(/\s+/g, ' ').toLowerCase()
  const operatorHero = flat(readComponent('OperatorHero.astro'))
  const homeRaw = readFileSync(resolve('src/pages/index.astro'), 'utf-8')
  const home = flat(homeRaw)
  const operator = flat(readFileSync(resolve('src/pages/operator.astro'), 'utf-8'))
  const nav = readComponent('Nav.astro')

  it('home composes the lead hero', () => {
    expect(homeRaw).toContain('OperatorHero')
  })

  it('the hero orients to the firm (software & AI solutions for small business)', () => {
    // Beat 1: a stranger learns who SMD is, not just the Operator. Fixes the
    // "land in the middle of what" miss.
    expect(operatorHero).toContain('software and ai solutions firm for small businesses')
  })

  it('the hero names the Operator as the flagship and links to it (above the fold)', () => {
    // Flagship-forward inside the firm frame: cradled, not buried, not the whole site.
    expect(operatorHero).toContain('flagship')
    expect(operatorHero).toContain('operator')
    expect(operatorHero).toContain('/operator')
  })

  it('the home carries the solution-first stance (not every problem needs an Operator)', () => {
    // Beat 5: the firm reasserts. The honest differentiator vs. the AI-agency swarm.
    expect(home).toContain('not every problem needs an operator')
  })

  it('the homepage assessment entry is the neutral front door (presumes no solution)', () => {
    // Firm-level surfaces start the assessment with no `interest`, so the chip
    // never presumes an Operator before the objectives-first conversation. The
    // CTA's presence + verb are guarded by the single-verb spine test below;
    // here we lock that the homepage carries no product attribution. The
    // /operator page and the vertical packs keep their interest on purpose
    // (route contract asserted centrally in booking-interest-parity).
    expect(home).toContain('data-ev="home-final-cta"')
    expect(home, 'home CTA must not presume a solution').not.toContain('interest=')
    expect(operatorHero, 'home hero CTA must not presume a solution').not.toContain('interest=')
  })

  it('/operator absorbs the comparison as the answer-engine surface', () => {
    // The retired /why folded here; the FAQPage schema must survive.
    expect(operator).toContain('faqpage')
  })

  it('a single primary verb across the surviving spine', () => {
    for (const page of [
      'src/pages/index.astro',
      'src/pages/operator.astro',
      'src/pages/about.astro',
      'src/pages/book.astro',
      'src/pages/case-studies/personal-injury-law-firm.astro',
    ]) {
      const c = flat(readFileSync(resolve(page), 'utf-8'))
      expect(c, `"start with an assessment" missing from ${page}`).toContain(
        'start with an assessment'
      )
      expect(c, `retired verb "start the conversation" present in ${page}`).not.toContain(
        'start the conversation'
      )
    }
  })

  it('the packs carry the same single verb (CTA in shared components, no retired verb anywhere)', () => {
    // The pack CTA text lives in the shared pack components, so presence is asserted
    // there once; the retired verb must appear on neither the components nor any pack page.
    const packCtaComponents = [
      'src/components/packs/PackHero.astro',
      'src/components/packs/PackClosing.astro',
      'src/components/packs/PackSectionCta.astro',
    ]
    for (const comp of packCtaComponents) {
      const c = flat(readFileSync(resolve(comp), 'utf-8'))
      expect(c, `"start with an assessment" missing from ${comp}`).toContain(
        'start with an assessment'
      )
      expect(c, `retired verb "start the conversation" present in ${comp}`).not.toContain(
        'start the conversation'
      )
    }
    const packPages = readdirSync(resolve('src/pages/packs'))
      .filter((n) => n.endsWith('.astro'))
      .map((n) => `src/pages/packs/${n}`)
    for (const page of packPages) {
      const c = flat(readFileSync(resolve(page), 'utf-8'))
      expect(c, `retired verb "start the conversation" present in ${page}`).not.toContain(
        'start the conversation'
      )
    }
  })

  it('the retired marketing pages are gone (not live routes)', () => {
    for (const p of ['src/pages/why.astro', 'src/pages/consulting.astro', 'src/pages/ai.astro']) {
      expect(existsSync(resolve(p)), `${p} should be removed (folded + redirected)`).toBe(false)
    }
  })

  // /contact is NOT retired. Captain decision 2026-06-30 restored it as the
  // quiet general-inquiry channel (a real form, not a published email address),
  // recorded in docs/marketing/positioning-spine.md. The assessment at /book
  // stays the single primary front door; /contact is a lower-weight channel
  // (footer-linked, never nav or a hero CTA). This guard inverts the prior
  // "retired" assertions so a future rebuild that reads the spine cannot
  // silently reap the form again.
  it('the contact form is restored and wired to its backend (quiet channel, not a peer door)', () => {
    const page = resolve('src/pages/contact.astro')
    expect(existsSync(page), 'src/pages/contact.astro should exist (restored 2026-06-30)').toBe(
      true
    )
    const contact = readFileSync(page, 'utf-8')
    // Posts to the surviving Resend-backed endpoint.
    expect(contact, 'contact page must POST to /api/contact').toContain('/api/contact')
    // The backend endpoint itself must be present.
    expect(
      existsSync(resolve('src/pages/api/contact.ts')),
      'the /api/contact endpoint must exist'
    ).toBe(true)
    // Honors the "do not publish the email" intent: no mailto on the page.
    expect(contact, 'contact page must not publish a mailto address').not.toContain('mailto:')
    // Routes engagement-seekers to the real front door via the shared helper
    // (bookHref() builds the neutral /book — asserted centrally in
    // booking-interest-parity). Guards the actual CTA, not an incidental
    // "/book" mention in a comment.
    expect(contact, 'contact page must point to the assessment front door').toContain('bookHref()')
  })

  it('the footer links to /contact and does not publish a raw email address', () => {
    const footer = readComponent('Footer.astro')
    expect(footer, 'footer must link to /contact').toContain('href="/contact"')
    expect(footer, 'footer must not publish a mailto address').not.toContain('mailto:')
  })

  it('the contact route is NOT 301-redirected away', () => {
    // Redirect logic lives in the legacy-redirect table now; assert /contact is
    // absent from both it and the middleware so the guard is not vacuous.
    const mw = readFileSync(resolve('src/middleware.ts'), 'utf-8')
    const redirects = readFileSync(resolve('src/lib/routing/legacy-redirects.ts'), 'utf-8')
    expect(mw, 'middleware must not redirect /contact').not.toContain("'/contact'")
    expect(redirects, 'legacy-redirect table must not redirect /contact').not.toContain(
      "'/contact'"
    )
  })

  it('nav does not link the retired pages', () => {
    expect(nav).not.toContain("href: '/why'")
    expect(nav).not.toContain("href: '/consulting'")
  })

  it('the retired routes 301 via the legacy-redirect table (bookmarks keep working)', () => {
    // The redirect rules were extracted from middleware.ts into a declarative
    // table (code review 2026-07-02 §1.3). Retired routes now live there.
    const redirects = readFileSync(resolve('src/lib/routing/legacy-redirects.ts'), 'utf-8')
    for (const route of ["'/why'", "'/consulting'", "'/ai'"]) {
      expect(redirects, `legacy-redirect table should reference retired route ${route}`).toContain(
        route
      )
    }
    expect(redirects).toContain('/operator#compare')
  })
})

describe('JSON-LD schema', () => {
  it('JsonLd.astro contains LocalBusiness type', () => {
    const content = readComponent('JsonLd.astro')
    expect(content).toContain('LocalBusiness')
  })

  it('JsonLd.astro contains schema.org context', () => {
    const content = readComponent('JsonLd.astro')
    expect(content).toContain('https://schema.org')
  })
})

describe('decision compliance', () => {
  it('no "free assessment" language in any src file', () => {
    const files = readAllSrcFiles()
    for (const filePath of files) {
      const content = readFileSync(filePath, 'utf-8').toLowerCase()
      expect(content, `"free assessment" found in ${filePath}`).not.toContain('free assessment')
      expect(content, `"free consultation" found in ${filePath}`).not.toContain('free consultation')
    }
  })

  it('no deprecated "team_invisibility" in src/', () => {
    const files = readAllSrcFiles()
    for (const filePath of files) {
      const content = readFileSync(filePath, 'utf-8')
      expect(content, `"team_invisibility" found in ${filePath}`).not.toContain('team_invisibility')
      expect(content.toLowerCase(), `"team invisibility" found in ${filePath}`).not.toContain(
        'team invisibility'
      )
    }
  })

  it('no "the consultant" language in marketing components', () => {
    const files = readAllSrcFiles()
    for (const filePath of files) {
      if (!filePath.endsWith('.astro')) continue
      const content = readFileSync(filePath, 'utf-8').toLowerCase()
      expect(content, `"the consultant" found in ${filePath}`).not.toContain('the consultant')
    }
  })
})

// Proof and the first case study. Captain decision 2026-09-14, recorded in
// docs/marketing/positioning-spine.md. Every published figure traces to a row in
// docs/marketing/proof-ledger.md, the pricing line tells the truth about the
// allowance, and the law pack's seat label is one string everywhere it renders.
describe('proof and case study (2026-09-14)', () => {
  const CASE_STUDY = 'src/pages/case-studies/personal-injury-law-firm.astro'
  const CASE_STUDY_HREF = '/case-studies/personal-injury-law-firm'
  const flat = (s: string) => s.replace(/\s+/g, ' ').toLowerCase()

  it('the case study page exists', () => {
    expect(existsSync(resolve(CASE_STUDY))).toBe(true)
  })

  it('home, /operator, and the law pack link the case study', () => {
    for (const page of [
      'src/pages/index.astro',
      'src/pages/operator.astro',
      'src/pages/packs/law-firm.astro',
    ]) {
      expect(readFileSync(resolve(page), 'utf-8'), `${page} must link the case study`).toContain(
        CASE_STUDY_HREF
      )
    }
  })

  it('/operator no longer promises "no usage meter" (the allowance is real)', () => {
    const operator = flat(readFileSync(resolve('src/pages/operator.astro'), 'utf-8'))
    expect(operator).not.toContain('no usage meter')
    expect(operator).toContain('clear allowance')
  })

  it('every proof-tile value has a row in the proof ledger', () => {
    const ledger = readFileSync(resolve('docs/marketing/proof-ledger.md'), 'utf-8')
    const values: string[] = []
    for (const page of ['src/pages/index.astro', CASE_STUDY]) {
      const src = readFileSync(resolve(page), 'utf-8')
      for (const m of src.matchAll(/\{ value: '([^']+)', label:/g)) values.push(m[1])
    }
    // Non-vacuous: three home tiles plus three case study stats.
    expect(values.length).toBeGreaterThanOrEqual(6)
    for (const v of values) {
      expect(ledger, `proof value "${v}" has no backticked row in proof-ledger.md`).toContain(
        '`' + v + '`'
      )
    }
  })

  it('/industries and /operator render the one INDUSTRIES list, which covers every pack', () => {
    for (const page of ['src/pages/industries.astro', 'src/pages/operator.astro']) {
      const src = readFileSync(resolve(page), 'utf-8')
      expect(src, `${page} must render INDUSTRIES`).toContain('INDUSTRIES.map(')
      expect(src, `${page} must not carry its own industries array`).not.toContain(
        'const industries = ['
      )
    }
    expect(INDUSTRIES.map((i) => i.slug).sort()).toEqual(Object.keys(PACK_META).sort())
    for (const i of INDUSTRIES) {
      expect(i.seat.toLowerCase()).toBe(PACK_META[i.slug].seat.toLowerCase())
    }
  })
})

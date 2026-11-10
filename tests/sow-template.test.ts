import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import { renderDocumentWithLayout } from '@formepdf/core'
import type { ElementInfo } from '@formepdf/core'
import { EXCLUSIONS } from '../src/lib/pdf/sow-template-base'
import { SOWTemplate, type SOWTemplateProps } from '../src/lib/pdf/sow-template'

// The template is three modules since 2026-09-11 (review 2026-09-10,
// Architecture 3): the shared base (props, exclusions, styles, footer), the
// page-2/page-3 module, and the template itself. The assertions below are
// about the SOW's authored content wherever it lives, so they read all three.
const sowTemplateModules = () =>
  ['sow-template-base.tsx', 'sow-template-pages.tsx', 'sow-template.tsx'].map((file) =>
    readFileSync(resolve('src/lib/pdf', file), 'utf-8')
  )
const sowTemplateSource = () => sowTemplateModules().join('\n')

// Text a reader of the PDF sees: what sits between a closing '>' and the next
// '<'. Extracted per module, because across the concatenation a module's
// header comment would sit between the previous module's last tag and the
// next '<' and read as rendered text.
const renderedText = () =>
  sowTemplateModules()
    .flatMap((code) => code.match(/>([^<]+)</g) || [])
    .join(' ')
    .toLowerCase()

describe('sow-template: conditional payment term rendering', () => {
  const source = () => sowTemplateSource()

  it('renders two-part payment schedule (default)', () => {
    const code = source()
    expect(code).toContain("payment.schedule === 'two_part'")
    expect(code).toContain('Due at signing (50%)')
    expect(code).toContain('Due at completion (50%)')
  })

  it('renders three-milestone payment schedule', () => {
    const code = source()
    expect(code).toContain('payment.milestone')
    expect(code).toContain('payment.milestoneLabel')
  })
})

describe('sow-template: no hourly rates in output (Decision #16)', () => {
  const source = () => sowTemplateSource()

  it('scope table has no hours column', () => {
    const code = source()
    // The template should not include an "Hours" column header in the scope table
    // Only "Deliverable" and "Description" columns
    const scopeSection = code.substring(code.indexOf('SCOPE OF WORK'), code.indexOf('TIMELINE'))
    expect(scopeSection).not.toContain('Hours')
    expect(scopeSection).not.toContain('Rate')
    expect(scopeSection).not.toContain('Price')
  })

  it('does not display rate anywhere in the rendered output', () => {
    const code = source()
    // The Forme template should never render the rate in JSX output
    expect(code).not.toContain('props.rate')
    // The word "hourly" appears in a comment about business rules — that is fine.
    // It must NOT appear inside JSX template text (between > and <).
    const text = renderedText()
    expect(text).not.toContain('hourly')
    expect(text).not.toContain('/hr')
  })

  it('project investment shows total price only', () => {
    const code = source()
    expect(code).toContain('Project total')
    expect(code).toContain('payment.totalPrice')
    // Payment section should NOT show any hourly breakdown
    const investmentSection = code.substring(
      code.indexOf('function SOWPaymentBlock'),
      code.indexOf('interface Page1Props')
    )
    expect(investmentSection).toContain('Project total')
    expect(investmentSection).not.toContain('per hour')
    expect(investmentSection).not.toContain('hourly')
  })
})

describe('sow-template: exclusions list (Decision #10)', () => {
  const source = () => sowTemplateSource()

  it('exports EXCLUSIONS constant', () => {
    expect(source()).toContain('export const EXCLUSIONS')
  })

  it('includes all 4 hard exclusions from Decision #10 (evolved)', () => {
    const code = source()
    expect(code).toContain('Bookkeeping remediation or catch-up')
    expect(code).toContain('Data migration from legacy systems')
    expect(code).toContain('Ground-up product development (consumer apps, SaaS products)')
    expect(code).toContain('Ongoing support beyond the handoff session')
  })

  it('includes parking lot language from Decision #11', () => {
    const code = source()
    // Text may be split across lines by Prettier
    expect(code).toContain('logged')
    expect(code).toContain('reviewed together')
    expect(code).toContain('separate scope and estimate')
  })
})

describe('sow-template: voice compliance (Decision #20)', () => {
  const source = () => sowTemplateSource()

  it('uses "we" voice in terms section', () => {
    const code = source()
    expect(code).toContain('We will confirm')
    expect(code).toContain('we will')
    // Prettier may split "address questions" across lines; check "we will address" instead
    expect(code).toContain('we will address')
    expect(code).toContain('questions and minor adjustments')
  })

  it('does not use "I" or "the consultant" anywhere', () => {
    // Check static text content (not JS variable names)
    const allText = renderedText()
    expect(allText).not.toContain(' i ')
    expect(allText).not.toContain('the consultant')
  })
})

describe('sow-template: what is included section', () => {
  const source = () => sowTemplateSource()

  it('includes problem diagnosis', () => {
    expect(source()).toContain('problem diagnosis')
  })

  it('includes process documentation', () => {
    expect(source()).toContain('process documentation')
  })

  it('includes tool configuration', () => {
    expect(source()).toContain('tool configuration')
  })

  it('includes handoff training session', () => {
    expect(source()).toContain('handoff training session')
  })

  it('includes written handoff document', () => {
    expect(source()).toContain('written handoff document')
  })
})

describe('sow-template: terms compliance', () => {
  const source = () => sowTemplateSource()

  it('includes 5-day validity term (Decision #18)', () => {
    expect(source()).toContain('5 business days')
  })

  it('does not contain a fixed-duration stabilization period commitment (#398)', () => {
    expect(source()).not.toContain('2-week stabilization period')
    // The stabilization concept is preserved — just without a hardcoded duration.
    expect(source()).toContain('stabilization period')
  })

  it('includes termination clause', () => {
    expect(source()).toContain('3 business days')
    expect(source()).toContain('written notice')
  })

  it('includes payment regardless of scope additions note (Decision #14)', () => {
    expect(source()).toContain('Payment is due regardless of scope additions')
  })
})

// The same rules, read from the laid-out pages of the real template. Fixture
// values are fictional and exist only for this test.
const props: SOWTemplateProps = {
  client: { businessName: 'Fixture Co', contactName: 'Pat Fixture', contactTitle: 'Owner' },
  document: { date: 'May 1, 2099', expirationDate: 'May 8, 2099', sowNumber: 'SOW-209905-001' },
  engagement: {
    overview: 'We work alongside the team on the areas that matter most to the owner.',
    startDate: 'May 10, 2099',
    endDate: 'June 20, 2099',
  },
  items: [
    { name: 'Fixture deliverable 1', description: 'Fixture description 1.' },
    { name: 'Fixture deliverable 2', description: 'Fixture description 2.' },
  ],
  payment: { schedule: 'two_part', totalPrice: '$7,777', deposit: '$3,888', completion: '$3,889' },
}

async function renderedPageText(): Promise<string> {
  const { layout } = await renderDocumentWithLayout(SOWTemplate(props))
  const lines: string[] = []
  const walk = (nodes: ElementInfo[]) => {
    for (const n of nodes) {
      if (n.nodeType === 'TextLine' && n.textContent) lines.push(n.textContent)
      walk(n.children ?? [])
    }
  }
  for (const page of layout.pages) walk(page.elements)
  return lines.join('\n')
}

describe('sow-template: what the rendered SOW never says', () => {
  it('shows no hourly rate, only the project total (Decision #16)', async () => {
    const text = (await renderedPageText()).toLowerCase()
    expect(text).toContain('project total')
    for (const word of ['hourly', '/hr', 'per hour']) expect(text).not.toContain(word)
  })

  it('speaks as the firm, never as "I" or "the consultant" (Decision #20)', async () => {
    const text = ` ${(await renderedPageText()).toLowerCase().replace(/\s+/g, ' ')} `
    expect(text).not.toContain(' i ')
    expect(text).not.toContain('the consultant')
  })

  it('renders every hard exclusion (Decision #10)', async () => {
    const text = (await renderedPageText()).replace(/\s+/g, ' ')
    expect(EXCLUSIONS.length).toBeGreaterThan(0)
    for (const exclusion of EXCLUSIONS) expect(text).toContain(exclusion)
  })
})

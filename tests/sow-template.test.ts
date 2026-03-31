import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('sow-template: template file', () => {
  it('sow-template.tsx exists', () => {
    expect(existsSync(resolve('src/lib/pdf/sow-template.tsx'))).toBe(true)
  })

  it('render.ts exists', () => {
    expect(existsSync(resolve('src/lib/pdf/render.ts'))).toBe(true)
  })
})

describe('sow-template: props interface', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('exports SOWTemplateProps interface', () => {
    expect(source()).toContain('export interface SOWTemplateProps')
  })

  it('includes client props with businessName, contactName, contactTitle', () => {
    const code = source()
    expect(code).toContain('businessName: string')
    expect(code).toContain('contactName: string')
    expect(code).toContain('contactTitle?: string')
  })

  it('includes document props with date, expirationDate, sowNumber', () => {
    const code = source()
    expect(code).toContain('date: string')
    expect(code).toContain('expirationDate: string')
    expect(code).toContain('sowNumber: string')
  })

  it('includes engagement props with overview, startDate, endDate', () => {
    const code = source()
    expect(code).toContain('overview: string')
    expect(code).toContain('startDate: string')
    expect(code).toContain('endDate: string')
  })

  it('includes items array with name and description', () => {
    const code = source()
    expect(code).toContain('items: Array<')
    expect(code).toContain('name: string')
    expect(code).toContain('description: string')
  })

  it('includes payment props with schedule, totalPrice, deposit, completion', () => {
    const code = source()
    expect(code).toContain("schedule: 'two_part' | 'three_milestone'")
    expect(code).toContain('totalPrice: string')
    expect(code).toContain('deposit: string')
    expect(code).toContain('completion: string')
  })

  it('includes optional milestone fields for three_milestone schedule', () => {
    const code = source()
    expect(code).toContain('milestone?: string')
    expect(code).toContain('milestoneLabel?: string')
  })

  it('includes smd props with signerName and signerTitle', () => {
    const code = source()
    expect(code).toContain('signerName: string')
    expect(code).toContain('signerTitle: string')
  })
})

describe('sow-template: conditional payment term rendering', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

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
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

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
    const jsxTexts = code.match(/>([^<]+)</g) || []
    const renderedText = jsxTexts.join(' ').toLowerCase()
    expect(renderedText).not.toContain('hourly')
    expect(renderedText).not.toContain('/hr')
  })

  it('project investment shows total price only', () => {
    const code = source()
    expect(code).toContain('Project total')
    expect(code).toContain('payment.totalPrice')
    // Payment section should NOT show any hourly breakdown
    const investmentSection = code.substring(
      code.indexOf('PROJECT INVESTMENT'),
      code.indexOf('Page 1 of 2')
    )
    expect(investmentSection).not.toContain('per hour')
    expect(investmentSection).not.toContain('hourly')
  })
})

describe('sow-template: exclusions list (Decision #10)', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('exports EXCLUSIONS constant', () => {
    expect(source()).toContain('export const EXCLUSIONS')
  })

  it('includes all 5 hard exclusions from Decision #10', () => {
    const code = source()
    expect(code).toContain('Bookkeeping remediation or catch-up')
    expect(code).toContain('Data migration from legacy systems')
    expect(code).toContain('Custom software or application development')
    expect(code).toContain('Ongoing support beyond the handoff session')
    expect(code).toContain('Multi-location or franchise scope')
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
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('uses "we" voice in terms section', () => {
    const code = source()
    expect(code).toContain('We will confirm')
    // "we will" and "address" may be split across lines by Prettier
    expect(code).toContain('we will')
    expect(code).toContain('address questions')
  })

  it('does not use "I" or "the consultant" anywhere', () => {
    const code = source()
    // Check static text content (not JS variable names)
    const staticTexts = code.match(/>([^<]+)</g) || []
    const allText = staticTexts.join(' ').toLowerCase()
    expect(allText).not.toContain(' i ')
    expect(allText).not.toContain('the consultant')
  })
})

describe('sow-template: what is included section', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

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

describe('sow-template: deliverable count validation', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('enforces maximum 8 deliverable items', () => {
    const code = source()
    expect(code).toContain('items.length > 8')
    expect(code).toContain('maximum of 8 deliverable items')
  })

  it('reduces row padding for 7-8 items', () => {
    const code = source()
    expect(code).toContain('items.length > 6')
  })
})

describe('sow-template: terms compliance', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('includes 5-day validity term (Decision #18)', () => {
    expect(source()).toContain('5 business days')
  })

  it('includes stabilization period term (Decision #27)', () => {
    expect(source()).toContain('2-week stabilization period')
  })

  it('includes termination clause', () => {
    expect(source()).toContain('3 business days')
    expect(source()).toContain('written notice')
  })

  it('includes payment regardless of scope additions note (Decision #14)', () => {
    expect(source()).toContain('Payment is due regardless of scope additions')
  })
})

describe('sow-template: render wrapper', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/render.ts'), 'utf-8')

  it('exports renderSow function', () => {
    expect(source()).toContain('export async function renderSow')
  })

  it('imports SOWTemplate and renderDocument', () => {
    const code = source()
    expect(code).toContain('renderDocument')
    expect(code).toContain('SOWTemplate')
  })

  it('returns Uint8Array', () => {
    expect(source()).toContain('Promise<Uint8Array>')
  })
})

describe('sow-template: signature block', () => {
  const source = () => readFileSync(resolve('src/lib/pdf/sow-template.tsx'), 'utf-8')

  it('includes client signature section', () => {
    expect(source()).toContain('CLIENT')
  })

  it('includes SMD Services signature section', () => {
    expect(source()).toContain('SMD SERVICES')
  })

  it('renders contact title conditionally', () => {
    expect(source()).toContain('client.contactTitle')
  })

  it('includes agreement text', () => {
    expect(source()).toContain('By signing below, both parties agree')
  })
})

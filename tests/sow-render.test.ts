/**
 * The SOW PDF, rendered for real, carries the words a client signs.
 *
 * This is the document a client signs, and until 2026-09-25 no test rendered
 * it: the suite here was `describe.skip` because `src/lib/pdf/render.ts`
 * imports `forme_bg.wasm` the Workers way, which Node cannot load. The gap had
 * a price. The #2771 decomposition turned the header and the payment block into
 * components that return a fragment, and @formepdf/react serializes a
 * component whose result is a fragment to nothing, so SOWs rendered after it
 * carried no client name, no SOW number and no price, and every check stayed
 * green because the build only proves the template compiles.
 *
 * So this renders the real template through @formepdf/core's Node entry
 * (which loads its WASM from disk: the same engine and serializer the Worker
 * entry in `render.ts` uses, with a different WASM loader), takes the laid-out
 * text of each page from `renderDocumentWithLayout` (text lives on `TextLine`
 * leaves), and asserts what a signer must see: the header on page 1, the price
 * and its whole schedule together on one page, a correctly numbered footer on
 * every page, and the signature anchors on the last. A component that
 * serializes to nothing, a price split from its schedule, or a footer that
 * miscounts turns this red.
 *
 * Fixture values are fictional and exist only for this test.
 */

import { describe, it, expect } from 'vitest'
import { renderDocumentWithLayout } from '@formepdf/core'
import type { ElementInfo } from '@formepdf/core'
import { SOWTemplate, type SOWTemplateProps } from '../src/lib/pdf/sow-template'

function fixture(
  schedule: SOWTemplateProps['payment']['schedule'],
  items: number,
  longDescriptions: boolean
): SOWTemplateProps {
  return {
    client: {
      businessName: 'Fixture Plumbing Co',
      contactName: 'Pat Fixture',
      contactTitle: 'Owner',
    },
    document: {
      date: 'September 25, 2026',
      expirationDate: 'October 2, 2026',
      sowNumber: 'SOW-209901-042',
    },
    engagement: {
      // Three sentences: the spec's overview runs three to four (4.2).
      overview:
        'Based on our conversation, we identified three areas where the operation can improve. ' +
        'This engagement scopes the work to address those areas together. ' +
        'We work alongside the team in the order that matters most to the owner.',
      startDate: 'October 5, 2026',
      endDate: 'November 20, 2026',
    },
    items: Array.from({ length: items }, (_, i) => ({
      name: `Fixture deliverable ${i + 1}`,
      description: longDescriptions
        ? `Fixture description ${i + 1}, long enough to wrap onto a second line inside its table cell.`
        : `Fixture description ${i + 1}.`,
    })),
    payment:
      schedule === 'two_part'
        ? { schedule, totalPrice: '$7,777', deposit: '$3,888', completion: '$3,889' }
        : {
            schedule,
            totalPrice: '$9,999',
            deposit: '$3,333',
            completion: '$3,334',
            milestone: '$3,332',
            milestoneLabel: 'fixture midpoint review',
          },
  }
}

/** Every TextLine's text on each page, in layout order, joined per page. */
async function pageTexts(props: SOWTemplateProps): Promise<string[]> {
  const { layout, pdf } = await renderDocumentWithLayout(SOWTemplate(props))
  expect(pdf.length).toBeGreaterThan(1000)
  return layout.pages.map((page) => {
    const lines: string[] = []
    const walk = (nodes: ElementInfo[]) => {
      for (const n of nodes) {
        if (n.nodeType === 'TextLine' && n.textContent) lines.push(n.textContent)
        walk(n.children ?? [])
      }
    }
    walk(page.elements)
    return lines.join('\n')
  })
}

function paymentStrings(props: SOWTemplateProps): string[] {
  const p = props.payment
  const out = [
    'PROJECT INVESTMENT',
    'Project total',
    p.totalPrice,
    'Due at signing',
    p.deposit,
    'Due at completion',
    p.completion,
  ]
  if (p.schedule === 'three_milestone') out.push(`Due at ${p.milestoneLabel}`, p.milestone ?? '')
  return out
}

async function assertSignable(props: SOWTemplateProps): Promise<string[]> {
  const pages = await pageTexts(props)
  expect(pages.length).toBeGreaterThanOrEqual(3)
  const first = pages[0]
  const last = pages[pages.length - 1]

  // The header block: who it is for and which document this is.
  for (const s of [
    'STATEMENT OF WORK',
    'Prepared for:',
    props.client.businessName,
    'Attn:',
    props.client.contactName,
    'Valid through:',
    props.document.expirationDate,
    'SOW #:',
    props.document.sowNumber,
  ])
    expect(first, `page 1 is missing "${s}"`).toContain(s)

  // Every deliverable, on page 1.
  for (const item of props.items) expect(first).toContain(item.name)

  // The price and its whole schedule on ONE page: never split, never absent.
  const priced = pages.filter((t) => t.includes('Project total'))
  expect(priced, 'the price appears on exactly one page').toHaveLength(1)
  for (const s of paymentStrings(props))
    expect(priced[0], `the priced page is missing "${s}"`).toContain(s)

  // A footer on every page, numbered against the real page count. The layout
  // tree carries the engine's page-number fields as placeholders (they are
  // filled when the PDF is written), so a field passes as the engine's own;
  // a literal number must be the true one, which is what a hard-coded
  // "Page 1 of 3" on an overflow page fails.
  pages.forEach((text, i) => {
    const sow = props.document.sowNumber.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const m = text.match(new RegExp(`${sow} \\| Page (\\S+) of (\\S+)`))
    expect(m, `page ${i + 1} has no footer`).not.toBeNull()
    const [, n, of] = m ?? []
    if (/^\d+$/.test(n ?? '')) expect(Number(n)).toBe(i + 1)
    if (/^\d+$/.test(of ?? '')) expect(Number(of)).toBe(pages.length)
  })

  // The terms before the signature page, and the anchors SignWell places on.
  expect(pages.some((t) => t.includes('EXCLUSIONS') && t.includes('TERMS'))).toBe(true)
  expect(last).toContain('CLIENT ACCEPTANCE')
  expect(last).toContain(props.client.contactName)
  expect(last).toContain('{{s:1}}')
  expect(last).toContain('{{d:1}}')
  return pages
}

describe('SOW PDF: the rendered pages carry what a client signs', () => {
  // docs/templates/sow-template.md: page 1 carries the header, scope,
  // timeline and price (4); three to six deliverables are typical and eight
  // must still fit, with tighter row padding past six (4.3, 7.3).
  for (const [schedule, items] of [
    ['two_part', 3],
    ['three_milestone', 6],
    ['two_part', 8],
  ] as const) {
    it(`${items} deliverables (${schedule}): three pages, the price and its schedule on page 1`, async () => {
      const props = fixture(schedule, items, false)
      const pages = await assertSignable(props)
      expect(pages).toHaveLength(3)
      for (const s of paymentStrings(props)) expect(pages[0], `page 1 lacks "${s}"`).toContain(s)
    })
  }

  it('past what page 1 holds (eight two-line deliverables), the price stays whole and every page is numbered true', async () => {
    await assertSignable(fixture('three_milestone', 8, true))
  })

  it('refuses more than eight deliverables rather than rendering them', () => {
    expect(() => SOWTemplate(fixture('two_part', 9, false))).toThrow(/maximum of 8/)
  })
})

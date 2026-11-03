/**
 * A seat that enables the matter-records skill (the `records` lane, ADR 0102
 * amendment 4) authors who may receive the files and how many replies SMD
 * reviews first. Without `matter_file_delivery.recipients` the broker refuses
 * every submit; without `review_first` (or `review: smd`) the first records reply
 * a runner writes would go out unreviewed. The switch-on PR adds the skill to a
 * seat; this pin makes that PR author both keys in the same change.
 */

import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { parse as parseYaml } from 'yaml'
import { JOB_LANES, authoredLanes } from '../src/lib/operator/customer-yaml/sections-capabilities'

const CUSTOMERS = resolve(__dirname, '..', 'operator/customers')
const SEATS = readdirSync(CUSTOMERS)
  .filter((d) => existsSync(join(CUSTOMERS, d, 'customer.yaml')))
  .sort()

type Doc = Record<string, unknown>

/** What a records seat is missing, or [] when it authors both keys (or no records lane). */
function recordsGaps(doc: Doc): string[] {
  if (!authoredLanes(doc).includes('records')) return []
  const block = doc['matter_file_delivery']
  const delivery = block && typeof block === 'object' ? (block as Doc) : {}
  const gaps: string[] = []
  const recipients = delivery['recipients']
  if (!Array.isArray(recipients) || recipients.length === 0) {
    gaps.push('matter_file_delivery.recipients')
  }
  const first = delivery['review_first']
  const reviewed =
    (typeof first === 'number' && Number.isInteger(first) && first > 0) ||
    String(delivery['review'] ?? '').toLowerCase() === 'smd'
  if (!reviewed) gaps.push('matter_file_delivery.review_first')
  return gaps
}

function seatWith(skills: string[], delivery?: Doc): Doc {
  return {
    personas: [{ name: 'operator', skills: skills.map((name) => ({ name })) }],
    ...(delivery ? { matter_file_delivery: delivery } : {}),
  }
}

describe('a records seat authors its recipients and its SMD review', () => {
  it('the records lane is authored by the matter-records skill', () => {
    expect(JOB_LANES.records).toBe('matter-records')
  })

  it.each(SEATS)('%s', (slug) => {
    const doc = parseYaml(readFileSync(join(CUSTOMERS, slug, 'customer.yaml'), 'utf8')) as Doc
    expect(recordsGaps(doc)).toEqual([])
  })

  it('a seat enabling matter-records without either key is caught (the check can fail)', () => {
    expect(recordsGaps(seatWith(['matter-records']))).toEqual([
      'matter_file_delivery.recipients',
      'matter_file_delivery.review_first',
    ])
    expect(
      recordsGaps(seatWith(['matter-records'], { recipients: ['@firm.example'], review_first: 0 }))
    ).toEqual(['matter_file_delivery.review_first'])
    expect(
      recordsGaps(seatWith(['matter-records'], { recipients: ['@firm.example'], review_first: 10 }))
    ).toEqual([])
    expect(
      recordsGaps(seatWith(['matter-records'], { recipients: ['@firm.example'], review: 'SMD' }))
    ).toEqual([])
    // A seat without the lane needs neither.
    expect(recordsGaps(seatWith(['smd-note']))).toEqual([])
  })
})

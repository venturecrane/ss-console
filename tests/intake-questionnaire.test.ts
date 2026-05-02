import { describe, expect, it } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'
import {
  INTAKE_EMPLOYEE_COUNT_OPTIONS,
  INTAKE_HOW_HEARD_OPTIONS,
  INTAKE_YEARS_IN_BUSINESS_OPTIONS,
  normalizeIntakePayload,
} from '../src/lib/booking/intake-questionnaire'

describe('intake questionnaire canonical helper', () => {
  it('exports the shared employee-count options', () => {
    expect(INTAKE_EMPLOYEE_COUNT_OPTIONS).toEqual([
      { value: '1-5', label: '1 - 5' },
      { value: '6-10', label: '6 - 10' },
      { value: '11-25', label: '11 - 25' },
      { value: '26-50', label: '26 - 50' },
      { value: '50+', label: '50+' },
    ])
  })

  it('exports the shared years-in-business options', () => {
    expect(INTAKE_YEARS_IN_BUSINESS_OPTIONS).toEqual([
      { value: '<1', label: 'Less than 1 year' },
      { value: '1-3', label: '1 - 3 years' },
      { value: '3-5', label: '3 - 5 years' },
      { value: '5-10', label: '5 - 10 years' },
      { value: '10+', label: '10+ years' },
    ])
  })

  it('exports the shared how-heard options', () => {
    expect(INTAKE_HOW_HEARD_OPTIONS).toEqual([
      { value: 'Google Search', label: 'Google Search' },
      { value: 'Referral', label: 'Referral' },
      { value: 'BNI / Networking group', label: 'BNI / Networking group' },
      { value: 'Chamber of Commerce', label: 'Chamber of Commerce' },
      { value: 'Social Media', label: 'Social Media' },
      { value: 'SCORE / SBA', label: 'SCORE / SBA' },
      { value: 'other', label: 'Other' },
    ])
  })

  it('normalizes shared other-field payloads once for both surfaces', () => {
    expect(
      normalizeIntakePayload({
        vertical: 'other',
        vertical_other: 'Commercial laundry',
        how_heard: 'other',
        how_heard_other: 'Existing client intro',
        biggest_challenge: 'Scheduling',
      })
    ).toEqual({
      vertical: 'Commercial laundry',
      how_heard: 'Existing client intro',
      biggest_challenge: 'Scheduling',
    })
  })
})

describe('intake surfaces', () => {
  const bookSrc = readFileSync(resolve('src/pages/book.astro'), 'utf-8')
  const getStartedSrc = readFileSync(resolve('src/pages/get-started.astro'), 'utf-8')
  const componentSrc = readFileSync(
    resolve('src/components/booking/IntakeQuestionnaire.astro'),
    'utf-8'
  )

  // /book was refactored from the categorical IntakeQuestionnaire to a
  // UnifiedIntake (4 required fields + free-text textarea + AI follow-up).
  // /get-started keeps using the shared questionnaire for the post-booking
  // prep flow, where the categorical fields still serve a purpose.

  it('/book renders the unified intake (post-merge with /talk)', () => {
    expect(bookSrc).toContain(
      "import UnifiedIntake from '../components/booking/UnifiedIntake.astro'"
    )
    expect(bookSrc).toContain('<UnifiedIntake')
  })

  it('/get-started still uses the shared categorical questionnaire (prep flow)', () => {
    expect(getStartedSrc).toContain(
      "import IntakeQuestionnaire from '../components/booking/IntakeQuestionnaire.astro'"
    )
    expect(getStartedSrc).toContain('<IntakeQuestionnaire idPrefix="gs" mode="prep" />')
  })

  it('/get-started routes through the shared normalization helper', () => {
    expect(getStartedSrc).toContain(
      "import { formDataToObject, normalizeIntakePayload } from '../lib/booking/intake-questionnaire'"
    )
    expect(getStartedSrc).toContain('normalizeIntakePayload(formDataToObject(new FormData(form)))')
  })

  it('renders select options from the canonical helper module inside the shared component', () => {
    expect(componentSrc).toContain("from '../../lib/booking/intake-questionnaire'")
    expect(componentSrc).toContain('INTAKE_EMPLOYEE_COUNT_OPTIONS')
    expect(componentSrc).toContain('INTAKE_YEARS_IN_BUSINESS_OPTIONS')
    expect(componentSrc).toContain('INTAKE_HOW_HEARD_OPTIONS')
  })
})

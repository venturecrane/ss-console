/**
 * Every job-lane list the console holds equals operator/contracts/job-lanes.yaml.
 *
 * The declaration is generated into src/lib/operator/job-lanes.generated.ts.
 * Each list below was a hand copy; this compares it with the generated constant
 * (by the column that says how it differs), through what each module does with
 * it, so a lane added to the yaml and missed here, or a hand list edited
 * alone, fails.
 */
import { describe, expect, it } from 'vitest'
import { JOB_LANES as CAPABILITY_LANES } from '../src/lib/operator/customer-yaml/sections-capabilities'
import { parseFirmInputs } from '../src/lib/operator/heartbeat-firm-inputs'
import {
  FIRM_INPUT_EXTRAS,
  FIRM_INPUT_FILE_LANES,
  FIRM_INPUT_LANES,
  JOB_LANES,
  LANE_EMAIL_LABEL,
  LANE_FIRM_MODULE,
  LANE_SKILL,
} from '../src/lib/operator/job-lanes.generated'
import { cardSubject } from '../src/lib/operator/request-card-email'
import type { RequestCard } from '../src/lib/operator/request-card'
import { LANE_INPUTS } from '../scripts/lib/seat-spec'

const ENTRY = { verdict: 'fetched', etag8: '1a2b3c4d', applied_at: null }

describe('the console job-lane lists equal the declaration', () => {
  it('the capability table names every lane with its skill, in declared order', () => {
    expect(Object.entries(CAPABILITY_LANES)).toEqual(Object.entries(LANE_SKILL))
  })

  it('the heartbeat accepts exactly the firm-input lanes and the extras', () => {
    for (const lane of [...FIRM_INPUT_LANES, ...FIRM_INPUT_EXTRAS]) {
      expect(parseFirmInputs({ [lane]: ENTRY }), lane).not.toBeNull()
    }
    for (const lane of JOB_LANES.filter((l) => !FIRM_INPUT_LANES.includes(l))) {
      expect(parseFirmInputs({ [lane]: ENTRY }), lane).toBeNull()
    }
  })

  it('a job card says what a person calls each lane, and a lane with no label by its name', () => {
    for (const lane of JOB_LANES) {
      const card = {
        kind: 'job_done',
        job: { lane, state: 'delivered' },
        minutes: 1,
        who: 'someone@firm.example',
        subject: 's',
      } as unknown as RequestCard
      const word = (LANE_EMAIL_LABEL as Record<string, string>)[lane] ?? lane
      expect(cardSubject('seat', card)).toContain(`: ${word} delivered: `)
    }
  })

  it('the seat-spec compiler knows each firm-input lane by its module and manifest', () => {
    const expected = Object.fromEntries(
      FIRM_INPUT_LANES.map((lane) => [
        lane,
        {
          module: `operator/runners/jobs/${(LANE_FIRM_MODULE as Record<string, string>)[lane].replaceAll('.', '/')}.py`,
          manifest: FIRM_INPUT_FILE_LANES.includes(lane)
            ? `${lane}-firm.yaml`
            : `${lane}/${lane}-firm.yaml`,
        },
      ])
    )
    expect(LANE_INPUTS).toEqual(expected)
  })
})

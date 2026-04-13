import { describe, it, expect } from 'vitest'
import {
  computeDimensionScores,
  computeOverallScore,
  identifyTopProblems,
  computeScores,
  computePainScore,
  computeAutoStage,
  parseEmployeeCount,
  getThreshold,
} from '../src/lib/scorecard/scoring'
import { SCALED_SCORES, QUESTIONS } from '../src/lib/scorecard/questions'

describe('SCALED_SCORES lookup', () => {
  it('maps raw 0-9 to expected scaled values', () => {
    expect(SCALED_SCORES[0]).toBe(0)
    expect(SCALED_SCORES[1]).toBe(11)
    expect(SCALED_SCORES[2]).toBe(22)
    expect(SCALED_SCORES[3]).toBe(33)
    expect(SCALED_SCORES[4]).toBe(44)
    expect(SCALED_SCORES[5]).toBe(56)
    expect(SCALED_SCORES[6]).toBe(67)
    expect(SCALED_SCORES[7]).toBe(78)
    expect(SCALED_SCORES[8]).toBe(89)
    expect(SCALED_SCORES[9]).toBe(100)
  })
})

describe('getThreshold', () => {
  it('returns correct threshold for each range', () => {
    expect(getThreshold(0).label).toBe('needs_attention')
    expect(getThreshold(22).label).toBe('needs_attention')
    expect(getThreshold(23).label).toBe('room_to_grow')
    expect(getThreshold(44).label).toBe('room_to_grow')
    expect(getThreshold(45).label).toBe('getting_there')
    expect(getThreshold(67).label).toBe('getting_there')
    expect(getThreshold(68).label).toBe('strong')
    expect(getThreshold(100).label).toBe('strong')
  })
})

describe('computeDimensionScores', () => {
  it('computes all zeros correctly', () => {
    const answers: Record<string, number> = {}
    for (const q of QUESTIONS) answers[q.id] = 0
    const dims = computeDimensionScores(answers)
    expect(dims).toHaveLength(5)
    for (const d of dims) {
      expect(d.raw).toBe(0)
      expect(d.scaled).toBe(0)
      expect(d.scoreLabel).toBe('needs_attention')
    }
  })

  it('computes all max correctly', () => {
    const answers: Record<string, number> = {}
    for (const q of QUESTIONS) answers[q.id] = 3
    const dims = computeDimensionScores(answers)
    for (const d of dims) {
      expect(d.raw).toBe(9)
      expect(d.scaled).toBe(100)
      expect(d.scoreLabel).toBe('strong')
    }
  })

  it('computes mixed scores correctly', () => {
    const answers: Record<string, number> = {}
    // Process design (q1-q3): 0+1+2 = 3 → scaled 33 (room_to_grow)
    answers['q1'] = 0
    answers['q2'] = 1
    answers['q3'] = 2
    // All other questions = 3
    for (const q of QUESTIONS) {
      if (!(q.id in answers)) answers[q.id] = 3
    }
    const dims = computeDimensionScores(answers)
    const processDim = dims.find((d) => d.id === 'process_design')!
    expect(processDim.raw).toBe(3)
    expect(processDim.scaled).toBe(33)
    expect(processDim.scoreLabel).toBe('room_to_grow')
  })

  it('handles skipped questions (-1) by extrapolating', () => {
    const answers: Record<string, number> = {}
    // Process design: q1=2, q2=2, q3=skipped → avg 2, extrapolated raw 6 → scaled 67
    answers['q1'] = 2
    answers['q2'] = 2
    answers['q3'] = -1
    for (const q of QUESTIONS) {
      if (!(q.id in answers)) answers[q.id] = 2
    }
    const dims = computeDimensionScores(answers)
    const processDim = dims.find((d) => d.id === 'process_design')!
    expect(processDim.scaled).toBe(67) // extrapolated from 2 answered questions
  })

  it('marks fully-skipped dimension as Skipped', () => {
    const answers: Record<string, number> = {}
    answers['q1'] = -1
    answers['q2'] = -1
    answers['q3'] = -1
    for (const q of QUESTIONS) {
      if (!(q.id in answers)) answers[q.id] = 2
    }
    const dims = computeDimensionScores(answers)
    const processDim = dims.find((d) => d.id === 'process_design')!
    expect(processDim.displayLabel).toBe('Skipped')
    expect(processDim.scaled).toBe(0)
  })
})

describe('computeOverallScore', () => {
  it('averages dimension scores', () => {
    const answers: Record<string, number> = {}
    for (const q of QUESTIONS) answers[q.id] = 2
    const dims = computeDimensionScores(answers)
    // Each dimension: raw 6 → scaled 67
    const overall = computeOverallScore(dims)
    expect(overall).toBe(67)
  })

  it('returns 0 for empty array', () => {
    expect(computeOverallScore([])).toBe(0)
  })
})

describe('identifyTopProblems', () => {
  it('returns bottom 2-3 dimensions', () => {
    const answers: Record<string, number> = {}
    // Set process_design to 0 (raw 0, scaled 0)
    answers['q1'] = 0
    answers['q2'] = 0
    answers['q3'] = 0
    // Set tool_systems to 1 each (raw 3, scaled 33)
    answers['q4'] = 1
    answers['q5'] = 1
    answers['q6'] = 1
    // Everything else max
    for (const q of QUESTIONS) {
      if (!(q.id in answers)) answers[q.id] = 3
    }
    const dims = computeDimensionScores(answers)
    const problems = identifyTopProblems(dims)
    expect(problems).toContain('process_design')
    expect(problems).toContain('tool_systems')
    expect(problems.length).toBeLessThanOrEqual(3)
  })

  it('excludes strong dimensions from problems', () => {
    const answers: Record<string, number> = {}
    for (const q of QUESTIONS) answers[q.id] = 3 // all strong
    const dims = computeDimensionScores(answers)
    const problems = identifyTopProblems(dims)
    expect(problems).toHaveLength(0)
  })
})

describe('computeScores (integration)', () => {
  it('returns full scorecard result', () => {
    const answers: Record<string, number> = {}
    for (const q of QUESTIONS) answers[q.id] = 1
    const result = computeScores(answers)
    expect(result.overall).toBeGreaterThan(0)
    expect(result.dimensions).toHaveLength(5)
    expect(result.overallLabel).toBeTruthy()
    expect(result.topProblems.length).toBeGreaterThanOrEqual(0)
  })
})

describe('computePainScore', () => {
  it('inverts health score correctly', () => {
    expect(computePainScore(0)).toBe(10)
    expect(computePainScore(22)).toBe(10)
    expect(computePainScore(23)).toBe(8)
    expect(computePainScore(44)).toBe(8)
    expect(computePainScore(45)).toBe(6)
    expect(computePainScore(66)).toBe(6)
    expect(computePainScore(67)).toBe(4)
    expect(computePainScore(88)).toBe(4)
    expect(computePainScore(89)).toBe(2)
    expect(computePainScore(100)).toBe(2)
  })
})

describe('computeAutoStage', () => {
  it('returns prospect for high pain', () => {
    expect(computeAutoStage(10)).toBe('prospect')
    expect(computeAutoStage(8)).toBe('prospect')
    expect(computeAutoStage(7)).toBe('prospect')
  })

  it('returns signal for low pain', () => {
    expect(computeAutoStage(6)).toBe('signal')
    expect(computeAutoStage(4)).toBe('signal')
    expect(computeAutoStage(2)).toBe('signal')
  })
})

describe('parseEmployeeCount', () => {
  it('returns midpoint for each range', () => {
    expect(parseEmployeeCount('1-5')).toBe(3)
    expect(parseEmployeeCount('6-10')).toBe(8)
    expect(parseEmployeeCount('11-25')).toBe(18)
    expect(parseEmployeeCount('26-50')).toBe(38)
    expect(parseEmployeeCount('50+')).toBe(75)
  })

  it('returns null for unknown range', () => {
    expect(parseEmployeeCount('unknown')).toBeNull()
  })
})

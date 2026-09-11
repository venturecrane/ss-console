/**
 * Tests for the customer.yaml structural validator
 * (src/lib/operator/customer-yaml/validator.ts).
 *
 * Test strategy: build one valid in-memory fixture (the schema's worked
 * example, simplified) and validate it once to lock the happy path. Then
 * mutate that fixture per error category and assert the matching
 * ValidationErrorCode is produced. Validator does NOT short-circuit on
 * the first error, so a single broken fixture can carry multiple
 * mutations and we assert the full error list.
 *
 * The validator never throws — every check appends to the errors[] list.
 * If a test ever expects throw, the validator's contract is broken.
 *
 * No real YAML parser is used here: the validator takes a parsed object,
 * not raw text. That separation matches ADR 0012 §4 (portal and Hermes
 * parse independently with their own libraries).
 */

import { describe, it, expect } from 'vitest'
import {
  validate,
  type CustomerYaml,
  type ValidationError,
  type ValidationErrorCode,
} from '../src/lib/operator/customer-yaml'

// -----------------------------------------------------------------------------
// Fixture builder
// -----------------------------------------------------------------------------

/**
 * Return a fresh, valid customer.yaml-shaped object. Tests mutate this
 * before passing to validate(); they never mutate a shared instance.
 *
 * The shape mirrors the spec's worked example (the smith-pi-firm example)
 * trimmed to the minimum that satisfies every required field. Optional
 * sections appear once across the suite (in `withFullOptionals`) so they
 * also get coverage.
 */
function validFixture(): Record<string, unknown> {
  return {
    schema_version: 1,
    customer_id: 'smith-pi-firm',
    customer_name: 'Smith PI Firm',
    vertical: 'law-firm',
    practice_areas: ['personal-injury', 'workers-comp'],
    fly_region: 'lax',
    model: 'claude-opus-4-7',
    hermes_ref: 'v2026.5.7@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0',
    machine: {
      size: 'performance-1x',
      memory_mb: 1024,
    },
    users: [
      { email: 'partner@firm.com', role: 'principal', full_name: 'Jane Smith' },
      { email: 'paralegal@firm.com', role: 'staff', full_name: 'Pat Lee' },
    ],
    personas: [
      {
        slug: 'marcus',
        status: 'active',
        name: 'Marcus',
        title: 'AI Associate',
        signature_html: '<p>Marcus | AI Associate at Smith PI</p>',
        tone: ['warm-but-professional', 'concise'],
        send_as: { agentmail_identity: 'marcus@smith-pi-firm.agents.smd.services' },
        entitlements: {
          exposure: {
            internal_write: 'autonomous',
            external_send: 'draft_for_review',
          },
        },
        skills: [
          {
            name: 'inbox-triage-and-draft',
            initiation: { manual: true, scheduled: false, webhook: false },
            enabled: true,
            cost_estimate: {
              tokens_in_per_run: 2000,
              tokens_out_per_run: 800,
              tool_calls_per_run: 4,
              runs_per_day_typical: 30,
            },
          },
          {
            name: 'conflict-check',
            initiation: { manual: true, scheduled: false, webhook: false },
          },
        ],
        channel_bindings: [{ integration: 'ms-graph', channels: ['primary-inbox'] }],
      },
    ],
    connectors: {
      Email: {
        adapter: 'microsoft-graph',
        backend: 'mcp:softeria/ms-365-mcp-server',
        token_ref: 'infisical:/operator/smith-pi-firm/email/refresh',
      },
      Calendar: { adapter: 'microsoft-graph', backend: 'mcp:softeria/ms-365-mcp-server' },
      PracticeManagement: {
        adapter: 'filevine',
        backend: 'build:filevine-mcp',
        scopes: ['matters:read', 'contacts:read'],
      },
    },
    scope: {
      email_folders_visible: ['Inbox', 'Clients', 'Intake'],
      email_folders_blind: ['Strategy', 'Private'],
      email_keyword_blocks: ['PRIVILEGED'],
      domain_blocks: ['opposing-counsel.example.com'],
    },
    escalation: {
      red_flag_recipients: ['partner@firm.com'],
      failure_recipients: ['partner@firm.com', 'paralegal@firm.com'],
    },
    memory: {
      d1_namespace: 'smith-pi-firm',
      r2_vault_path: 'vaults/smith-pi-firm/',
      vectorize_index: 'hermes-smith-pi-firm-vault',
    },
  }
}

function withFullOptionals(): Record<string, unknown> {
  const f = validFixture()
  f['voice_library'] = { samples_path: 'r2://vaults/smith-pi-firm/voice-samples/' }
  f['business_hours'] = {
    timezone: 'America/Phoenix',
    days: ['mon', 'tue', 'wed', 'thu', 'fri'],
    start: '08:00',
    end: '18:00',
  }
  f['logging'] = { level: 'info', ship_to: ['cloudflare-d1'] }
  f['pause'] = { active: false }
  return f
}

describe('webhook_triggers.exclude (authored trigger exceptions)', () => {
  const OPS = '3c191bed-cdda-48b9-a6ed-a51a349f3f94'
  const CHRIS = 'aaaa1111-2222-3333-4444-bbbbcccc0001'

  function withExclude(exclude: unknown) {
    const f = withWebhooks()
    const triggers = f['webhook_triggers'] as Record<string, unknown>[]
    triggers[0]['exclude'] = exclude
    return f
  }

  it('accepts matter + actor GUID lists and carries them through', () => {
    const result = validate(withExclude({ matters: [OPS], actors: [CHRIS] }))
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.webhook_triggers[0].exclude).toEqual({ matters: [OPS], actors: [CHRIS] })
    }
  })

  it('is null when unauthored', () => {
    const result = validate(withWebhooks())
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.webhook_triggers[0]?.exclude ?? null).toBeNull()
  })

  it('rejects non-GUID ids, unknown keys, and an empty block', () => {
    for (const bad of [{ matters: ['the ops matter'] }, { people: [CHRIS] }, {}]) {
      const result = validate(withExclude(bad))
      expect(result.ok).toBe(false)
    }
  })
})

describe('webhook_triggers.throttle (per-trigger cooldown, #1781)', () => {
  function withThrottle(throttle: unknown) {
    const f = withWebhooks()
    const triggers = f['webhook_triggers'] as Record<string, unknown>[]
    triggers[0]['throttle'] = throttle
    return f
  }

  it('accepts an authored cooldown and carries it through', () => {
    const result = validate(withThrottle({ cooldown_minutes: 30 }))
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.webhook_triggers[0].throttle).toEqual({ cooldown_minutes: 30 })
    }
  })

  it('accepts 0 (authored disable) and an empty block (gate default)', () => {
    const zero = validate(withThrottle({ cooldown_minutes: 0 }))
    expect(zero.ok).toBe(true)
    if (zero.ok) expect(zero.value.webhook_triggers[0].throttle).toEqual({ cooldown_minutes: 0 })
    const empty = validate(withThrottle({}))
    expect(empty.ok).toBe(true)
    if (empty.ok) {
      expect(empty.value.webhook_triggers[0].throttle).toEqual({ cooldown_minutes: null })
    }
  })

  it('is null when unauthored', () => {
    const result = validate(withWebhooks())
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.webhook_triggers[0]?.throttle ?? null).toBeNull()
  })

  it('rejects negative, non-integer, non-object, and unknown-key blocks', () => {
    for (const bad of [
      { cooldown_minutes: -5 },
      { cooldown_minutes: 2.5 },
      { cooldown_minutes: '30' },
      'nope',
      { cooldown_mins: 30 },
    ]) {
      const result = validate(withThrottle(bad))
      expect(result.ok).toBe(false)
    }
  })
})

describe('custody guard (code_execution vs gateway-held creds, ADR 0044 D8 / #1841)', () => {
  function withCodeExecution(extra?: (f: Record<string, unknown>) => void) {
    const f = validFixture()
    const personas = f['personas'] as Record<string, unknown>[]
    const entitlements = personas[0]['entitlements'] as Record<string, unknown>
    entitlements['exposure'] = {
      ...(entitlements['exposure'] as Record<string, unknown>),
      code_execution: 'autonomous',
    }
    extra?.(f)
    return f
  }

  it('rejects non-refused code_execution alongside enabled gateway connectors', () => {
    const result = validate(withCodeExecution())
    expect(result.ok).toBe(false)
    if (!result.ok) {
      const hit = result.errors.find((e) => e.code === 'CustodyGuardViolation')
      expect(hit).toBeDefined()
      expect(hit?.message).toContain('filevine')
    }
  })

  it('counts the telegram channel and agentmail send identity as surfaces', () => {
    const tg = validate(
      withCodeExecution((f) => {
        f['connectors'] = {}
        f['telegram'] = { enabled: true, allow_from: ['7367659986'] }
      })
    )
    expect(tg.ok).toBe(false)
    if (!tg.ok) expect(tg.errors.some((e) => e.message.includes('telegram'))).toBe(true)

    const am = validate(
      withCodeExecution((f) => {
        f['connectors'] = {}
        const personas = f['personas'] as Record<string, unknown>[]
        personas[0]['send_as'] = { agentmail_identity: 'marcus@smith-pi-firm.agents.smd.services' }
      })
    )
    expect(am.ok).toBe(false)
    if (!am.ok) expect(am.errors.some((e) => e.message.includes('agentmail'))).toBe(true)
  })

  it('an authored identity-channel exception accepts (the smd shape)', () => {
    const result = validate(
      withCodeExecution((f) => {
        f['connectors'] = {}
        const personas = f['personas'] as Record<string, unknown>[]
        delete personas[0]['send_as'] // fixture persona carries an agentmail identity
        f['telegram'] = { enabled: true, allow_from: ['7367659986'] }
        f['custody_exceptions'] = ['telegram']
      })
    )
    expect(result.ok, result.ok ? '' : JSON.stringify(result.errors)).toBe(true)
    if (result.ok) expect(result.value.custody_exceptions).toEqual(['telegram'])
  })

  it('client-data adapters can never be excepted', () => {
    const result = validate(withCodeExecution((f) => (f['custody_exceptions'] = ['filevine'])))
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors.some((e) => e.code === 'IneligibleCustodyException')).toBe(true)
    }
  })

  it('code_execution refused or unauthored passes with connectors present', () => {
    const refused = withCodeExecution()
    const personas = refused['personas'] as Record<string, unknown>[]
    const entitlements = personas[0]['entitlements'] as Record<string, unknown>
    ;(entitlements['exposure'] as Record<string, unknown>)['code_execution'] = 'refused'
    expect(validate(refused).ok).toBe(true)
    expect(validate(validFixture()).ok).toBe(true)
  })

  it('rejects malformed and duplicate exception lists', () => {
    for (const bad of ['telegram', ['telegram', 'telegram'], [42]]) {
      const result = validate(withCodeExecution((f) => (f['custody_exceptions'] = bad)))
      expect(result.ok).toBe(false)
    }
  })
})

describe('digest (authored digest home, #1742)', () => {
  it('accepts a valid home_matter_id GUID and carries it through', () => {
    const f = validFixture()
    f['digest'] = { home_matter_id: 'f220c8e4-eab5-4fd9-8f1d-0becf715b390' }
    const result = validate(f)
    expect(result.ok).toBe(true)
    if (result.ok) {
      expect(result.value.digest?.home_matter_id).toBe('f220c8e4-eab5-4fd9-8f1d-0becf715b390')
    }
  })

  it('is null when unauthored (fail-closed default)', () => {
    const result = validate(validFixture())
    expect(result.ok).toBe(true)
    if (result.ok) expect(result.value.digest).toBeNull()
  })

  it('rejects a non-GUID home_matter_id', () => {
    const f = validFixture()
    f['digest'] = { home_matter_id: 'the ops matter' }
    const result = validate(f)
    expect(result.ok).toBe(false)
    if (!result.ok) {
      expect(result.errors.some((e) => e.path === 'digest.home_matter_id')).toBe(true)
    }
  })
})

function codesOf(errors: ValidationError[]): ValidationErrorCode[] {
  return errors.map((e) => e.code)
}

// -----------------------------------------------------------------------------
// Happy path
// -----------------------------------------------------------------------------

describe('validate — happy path', () => {
  it('accepts the minimal valid fixture', () => {
    const result = validate(validFixture())
    if (!result.ok) {
      throw new Error(`expected ok; got errors: ${JSON.stringify(result.errors, null, 2)}`)
    }
    expect(result.ok).toBe(true)
    const value: CustomerYaml = result.value
    expect(value.customer_id).toBe('smith-pi-firm')
    expect(value.personas).toHaveLength(1)
    expect(value.personas[0].skills).toHaveLength(2)
    expect(value.connectors.Email?.token_ref).toBe(
      'infisical:/operator/smith-pi-firm/email/refresh'
    )
  })

  it('accepts the fixture with all optional sections populated', () => {
    const result = validate(withFullOptionals())
    if (!result.ok) {
      throw new Error(
        `expected ok with optionals; got errors: ${JSON.stringify(result.errors, null, 2)}`
      )
    }
    expect(result.ok).toBe(true)
    expect(result.value.business_hours?.timezone).toBe('America/Phoenix')
    expect(result.value.logging?.level).toBe('info')
    expect(result.value.pause?.active).toBe(false)
  })

  it('accepts non-law-firm vertical without practice_areas', () => {
    const f = validFixture()
    f['vertical'] = 'marketing-agency'
    delete f['practice_areas']
    const result = validate(f)
    if (!result.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(result.errors)}`)
    }
    expect(result.value.practice_areas).toEqual([])
  })
})

// -----------------------------------------------------------------------------
// Missing required fields
// -----------------------------------------------------------------------------

describe('validate — MissingField', () => {
  const requiredTopLevel = [
    'schema_version',
    'customer_id',
    'customer_name',
    'vertical',
    'fly_region',
    'model',
    'hermes_ref',
    'machine',
    'users',
    'personas',
    'connectors',
    'scope',
    'escalation',
    'memory',
  ]

  for (const field of requiredTopLevel) {
    it(`reports missing top-level field: ${field}`, () => {
      const f = validFixture()
      delete f[field]
      const r = validate(f)
      expect(r.ok).toBe(false)
      if (r.ok) return
      expect(codesOf(r.errors)).toContain('MissingField')
      expect(r.errors.some((e) => e.path.startsWith(field))).toBe(true)
    })
  }

  it('requires practice_areas when vertical=law-firm', () => {
    const f = validFixture()
    delete f['practice_areas']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'practice_areas' && e.code === 'MissingField')).toBe(
      true
    )
  })

  it('requires pause.reason when pause.active=true', () => {
    const f = validFixture()
    f['pause'] = { active: true }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'pause.reason')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Observability (ADR 0023 Wave 1)
// -----------------------------------------------------------------------------

describe('validate — observability block (ADR 0023)', () => {
  it('fills defaults when block is absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error('expected ok')
    expect(r.value.observability.sentry.enabled).toBe(true)
    expect(r.value.observability.health.period_seconds).toBe(60)
    expect(r.value.observability.health.grace_minutes).toBe(5)
  })

  it('fills defaults when block is an empty object', () => {
    const f = validFixture()
    f['observability'] = {}
    const r = validate(f)
    if (!r.ok) throw new Error('expected ok')
    expect(r.value.observability).toEqual({
      sentry: { enabled: true },
      health: { period_seconds: 60, grace_minutes: 5 },
    })
  })

  it('accepts a fully populated observability block', () => {
    const f = validFixture()
    f['observability'] = {
      sentry: { enabled: false },
      health: { period_seconds: 30, grace_minutes: 10 },
    }
    const r = validate(f)
    if (!r.ok) throw new Error('expected ok')
    expect(r.value.observability.sentry.enabled).toBe(false)
    expect(r.value.observability.health.period_seconds).toBe(30)
    expect(r.value.observability.health.grace_minutes).toBe(10)
  })

  it('partial population fills only missing fields', () => {
    const f = validFixture()
    f['observability'] = { health: { period_seconds: 120 } }
    const r = validate(f)
    if (!r.ok) throw new Error('expected ok')
    expect(r.value.observability.sentry.enabled).toBe(true) // default
    expect(r.value.observability.health.period_seconds).toBe(120) // overridden
    expect(r.value.observability.health.grace_minutes).toBe(5) // default
  })

  it('rejects non-boolean sentry.enabled', () => {
    const f = validFixture()
    f['observability'] = { sentry: { enabled: 'yes' } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'observability.sentry.enabled' && e.code === 'TypeMismatch')
    ).toBe(true)
  })

  it('rejects non-positive-integer health.period_seconds', () => {
    const f = validFixture()
    f['observability'] = { health: { period_seconds: 0 } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'observability.health.period_seconds' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('rejects non-positive-integer health.grace_minutes', () => {
    const f = validFixture()
    f['observability'] = { health: { grace_minutes: -1 } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'observability.health.grace_minutes' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('rejects non-object observability', () => {
    const f = validFixture()
    f['observability'] = 'invalid'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'observability' && e.code === 'TypeMismatch')).toBe(true)
  })

  it('does not accept an alert_webhook field (deferred per ADR 0023 §9)', () => {
    const f = validFixture()
    // Including alert_webhook is silently ignored — validator doesn't gate on
    // unknown keys, but the field doesn't appear in the typed output.
    f['observability'] = { alert_webhook: 'https://example.com/webhook' }
    const r = validate(f)
    if (!r.ok) throw new Error('expected ok (unknown keys ignored)')
    // The typed shape has no alert_webhook field.
    expect('alert_webhook' in (r.value.observability as unknown as Record<string, unknown>)).toBe(
      false
    )
  })
})

// -----------------------------------------------------------------------------
// Enum violations
// -----------------------------------------------------------------------------

describe('validate — EnumViolation', () => {
  it('rejects unknown vertical', () => {
    const f = validFixture()
    f['vertical'] = 'pet-grooming'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EnumViolation')
  })

  it('rejects unknown user role', () => {
    const f = validFixture()
    ;(f['users'] as Array<{ role: string }>)[0].role = 'janitor'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.code === 'EnumViolation' && e.path.includes('users'))).toBe(true)
  })

  it('rejects legacy skill trust_ceiling', () => {
    const f = validFixture()
    ;(f['personas'] as Array<{ skills: Array<Record<string, unknown>> }>)[0].skills[0][
      'trust_ceiling'
    ] = 'YOLO'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.code === 'LegacyEntitlementField' && e.path.includes('trust_ceiling'))
    ).toBe(true)
  })

  it('rejects unknown persona status', () => {
    const f = validFixture()
    ;(f['personas'] as Array<{ status: string }>)[0].status = 'on-vacation'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.code === 'EnumViolation' && e.path.includes('personas[0].status'))
    ).toBe(true)
  })

  it('rejects machine.memory_mb outside range', () => {
    const f = validFixture()
    ;(f['machine'] as { memory_mb: number }).memory_mb = 16384
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'machine.memory_mb')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Slug validation
// -----------------------------------------------------------------------------

describe('validate — InvalidSlug', () => {
  it('rejects customer_id with uppercase', () => {
    const f = validFixture()
    f['customer_id'] = 'Smith-PI-Firm'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects customer_id with leading dash', () => {
    const f = validFixture()
    f['customer_id'] = '-smith'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects customer_id over 32 chars', () => {
    const f = validFixture()
    f['customer_id'] = 'a'.repeat(33)
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects persona.slug with uppercase', () => {
    const f = validFixture()
    ;(f['personas'] as Array<{ slug: string }>)[0].slug = 'Marcus'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })
})

// -----------------------------------------------------------------------------
// Personas: array invariants
// -----------------------------------------------------------------------------

describe('validate — skills[].settings (ADR 0075 scalar knobs, #2005)', () => {
  const skillsOf = (f: Record<string, unknown>) =>
    (f['personas'] as Array<{ skills: Array<Record<string, unknown>> }>)[0].skills

  it('accepts a scalar settings map and carries it through verbatim', () => {
    const f = validFixture()
    skillsOf(f)[0]['settings'] = { escalate_after_attempts: 3, treatment_gap_flag_days: 45 }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.personas[0].skills[0].settings).toEqual({
      escalate_after_attempts: 3,
      treatment_gap_flag_days: 45,
    })
  })

  it('omits settings entirely when unauthored (byte-stable absence, not {})', () => {
    const f = validFixture()
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect('settings' in r.value.personas[0].skills[0]).toBe(false)
  })

  it('rejects a nested (non-scalar) settings value — the overlay would silently drop it', () => {
    const f = validFixture()
    skillsOf(f)[0]['settings'] = { cadence: { days: 5 } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects settings authored as a non-object', () => {
    const f = validFixture()
    skillsOf(f)[0]['settings'] = 'escalate_after_attempts=3'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })
})

describe('validate — personas[]', () => {
  it('rejects personas as a singular object (ADR 0011 enforcement)', () => {
    const f = validFixture()
    f['personas'] = { slug: 'marcus', status: 'active', name: 'Marcus' }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects an empty personas array', () => {
    const f = validFixture()
    f['personas'] = []
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EmptyList')
  })

  it('rejects personas with no active entry', () => {
    const f = validFixture()
    ;(f['personas'] as Array<{ status: string }>)[0].status = 'archived'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('MissingActivePersona')
  })

  it('rejects duplicate persona slugs', () => {
    const f = validFixture()
    const personas = f['personas'] as Array<Record<string, unknown>>
    personas.push({
      ...personas[0],
      name: 'Marcus Clone',
    })
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('DuplicatePersonaSlug')
  })

  it('accepts multiple personas when slugs are unique', () => {
    const f = validFixture()
    const personas = f['personas'] as Array<Record<string, unknown>>
    personas.push({
      slug: 'casey',
      status: 'archived',
      name: 'Casey',
      tone: ['warm'],
      entitlements: { exposure: { external_send: 'draft_for_review' } },
      skills: [
        {
          name: 'inbox-triage-and-draft',
          initiation: { manual: true, scheduled: false, webhook: false },
        },
      ],
    })
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.personas).toHaveLength(2)
  })

  it('accepts a plain-object voice_overrides / escalation_overrides', () => {
    const f = validFixture()
    const persona = (f['personas'] as Array<Record<string, unknown>>)[0]
    persona['voice_overrides'] = { greeting: 'Hi' }
    persona['escalation_overrides'] = { after_hours: 'page' }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.personas[0].voice_overrides).toEqual({ greeting: 'Hi' })
    expect(r.value.personas[0].escalation_overrides).toEqual({ after_hours: 'page' })
  })

  it('absent overrides resolve to null (no error)', () => {
    const f = validFixture()
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.personas[0].voice_overrides).toBeNull()
    expect(r.value.personas[0].escalation_overrides).toBeNull()
  })

  it('rejects a non-object override (the gate the old `unknown` typing skipped)', () => {
    const f = validFixture()
    const persona = (f['personas'] as Array<Record<string, unknown>>)[0]
    persona['voice_overrides'] = 'not-an-object'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'personas[0].voice_overrides' && e.code === 'TypeMismatch')
    ).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Connectors: capability union + backend prefix + token_ref
// -----------------------------------------------------------------------------

describe('validate — connectors', () => {
  it('rejects an unknown capability name', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, unknown>)['LoyaltyProgram'] = {
      adapter: 'foo',
      backend: 'mcp:foo',
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownCapability')
  })

  it('rejects an unknown backend prefix', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, string>>)['Email'].backend =
      'mystery:foo-bar'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidBackend')
  })

  it('accepts all documented backend prefixes', () => {
    const prefixes = ['mcp:foo/bar', 'build:wrapper', 'synthetic:fixture', 'native:brave-free']
    for (const backend of prefixes) {
      const f = validFixture()
      ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email'] = {
        adapter: 'x',
        backend,
      }
      const r = validate(f)
      if (!r.ok) {
        const has = r.errors.some(
          (e) => e.code === 'InvalidBackend' && e.path === 'connectors.Email.backend'
        )
        expect(has).toBe(false)
      }
    }
  })

  it('rejects a non-infisical token_ref', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, string>>)['Email'].token_ref =
      'vault://path/that/is/not/infisical'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidTokenRef')
  })

  it('rejects an infisical token_ref with too few segments', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, string>>)['Email'].token_ref =
      'infisical:/short'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidTokenRef')
  })

  // ADR 0070 (native cut): WebSearch is a first-class connector capability bound
  // to Hermes' native web provider (native:brave-free), not an MCP server.
  it('accepts a WebSearch connector on the native:brave-free backend', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['WebSearch'] = {
      adapter: 'brave',
      backend: 'native:brave-free',
      enabled: true,
    }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.connectors.WebSearch?.backend).toBe('native:brave-free')
    expect(r.value.connectors.WebSearch?.enabled).toBe(true)
  })

  it('rejects a WebSearch connector on an unknown backend (fail-closed)', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['WebSearch'] = {
      adapter: 'brave',
      backend: 'http:brave.com',
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    const has = r.errors.some(
      (e) => e.code === 'InvalidBackend' && e.path === 'connectors.WebSearch.backend'
    )
    expect(has).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Memory: isolation invariants
// -----------------------------------------------------------------------------

describe('validate — memory isolation', () => {
  it('rejects d1_namespace that does not equal customer_id', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, string>)['d1_namespace'] = 'someone-else'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('IsolationViolation')
  })

  it('rejects r2_vault_path that does not equal vaults/{id}/', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, string>)['r2_vault_path'] = 'vaults/other/'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('IsolationViolation')
  })

  it('rejects vectorize_index that does not match the expected name', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, string>)['vectorize_index'] = 'global-shared-vault'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('IsolationViolation')
  })
})

// -----------------------------------------------------------------------------
// Escalation
// -----------------------------------------------------------------------------

describe('validate — escalation', () => {
  it('rejects empty red_flag_recipients', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['red_flag_recipients'] = []
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'escalation.red_flag_recipients')).toBe(true)
  })

  it('rejects empty failure_recipients', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['failure_recipients'] = []
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'escalation.failure_recipients')).toBe(true)
  })

  it("case_alert_routing absent parses to null (= central, today's behavior) (#2004)", () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.escalation.case_alert_routing).toBeNull()
  })

  it('accepts matter_staff routing; fallback optional and may be empty (fail-closed posture)', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['case_alert_routing'] = { mode: 'matter_staff' }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.escalation.case_alert_routing).toEqual({
      mode: 'matter_staff',
      fallback_recipients: [],
    })
  })

  it('accepts an authored fallback list', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['case_alert_routing'] = {
      mode: 'matter_staff',
      fallback_recipients: ['admin@firm.com'],
    }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.escalation.case_alert_routing?.fallback_recipients).toEqual(['admin@firm.com'])
  })

  it('rejects an unknown routing mode', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['case_alert_routing'] = { mode: 'per-matter' }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'escalation.case_alert_routing.mode')).toBe(true)
  })

  it('rejects a non-object routing block', () => {
    const f = validFixture()
    ;(f['escalation'] as Record<string, unknown>)['case_alert_routing'] = 'matter_staff'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'escalation.case_alert_routing')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Schema version
// -----------------------------------------------------------------------------

describe('validate — schema_version', () => {
  it('rejects unsupported schema_version', () => {
    const f = validFixture()
    f['schema_version'] = 99
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('SchemaVersionUnsupported')
  })

  it('rejects non-integer schema_version', () => {
    const f = validFixture()
    f['schema_version'] = '1'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })
})

// -----------------------------------------------------------------------------
// Secret detection through validate()
// -----------------------------------------------------------------------------

describe('validate — secret detection integration', () => {
  it('rejects when a banned field name appears anywhere', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email']['client_secret'] =
      'irrelevant'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('BannedFieldName')
  })

  it('rejects when a provider-shaped key appears in a value', () => {
    const f = validFixture()
    ;(f['personas'] as Array<Record<string, unknown>>)[0]['notes'] = [
      'sk',
      'live',
      'abcdefghijklmnopqrstuvwxyz12345678',
    ].join('_')
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('SecretDetected')
  })

  it('runs scanRawYaml when rawText is supplied (catches malformed YAML)', () => {
    const garbageYaml = '::: not valid :::\napi_key: anything\n'
    // Pass a structurally minimal object so the structural pass also has
    // something to chew on; the raw scan must STILL find the leak.
    const r = validate(validFixture(), { rawText: garbageYaml })
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('BannedFieldName')
  })

  it('NEVER echoes the matched secret in error messages', () => {
    const secret = ['sk', 'live', 'abcdefghijklmnopqrstuvwxyz12345678'].join('_')
    const f = validFixture()
    ;(f['personas'] as Array<Record<string, unknown>>)[0]['notes'] = secret
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    const serialized = JSON.stringify(r.errors)
    expect(serialized).not.toContain(secret)
  })
})

// -----------------------------------------------------------------------------
// Aggregate behavior
// -----------------------------------------------------------------------------

describe('validate — aggregate error behavior', () => {
  it('returns multiple errors from a single broken fixture (no short-circuit)', () => {
    const f = validFixture()
    // Break multiple unrelated fields at once.
    delete f['customer_name']
    f['vertical'] = 'pet-grooming'
    ;(f['memory'] as Record<string, string>)['d1_namespace'] = 'wrong'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    // We expect at minimum: MissingField (customer_name), EnumViolation
    // (vertical), IsolationViolation (memory.d1_namespace).
    expect(r.errors.length).toBeGreaterThanOrEqual(3)
    const codes = codesOf(r.errors)
    expect(codes).toContain('MissingField')
    expect(codes).toContain('EnumViolation')
    expect(codes).toContain('IsolationViolation')
  })

  it('rejects a non-object root', () => {
    const r = validate(['not', 'an', 'object'])
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors[0].code).toBe('TypeMismatch')
    expect(r.errors[0].path).toBe('$')
  })

  it('rejects null root', () => {
    const r = validate(null)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors[0].code).toBe('TypeMismatch')
  })

  it('never throws on adversarial input', () => {
    const adversarial: unknown[] = [
      null,
      undefined,
      'string',
      123,
      [],
      { customer_id: null },
      { connectors: 'not-an-object' },
      { personas: 'not-an-array' },
    ]
    for (const input of adversarial) {
      expect(() => validate(input)).not.toThrow()
    }
  })
})

// -----------------------------------------------------------------------------
// hermes_ref upstream-pin enforcement (ADR 0024)
// -----------------------------------------------------------------------------

describe('validate — hermes_ref upstream-pin enforcement (ADR 0024)', () => {
  it('accepts a valid upstream pin (v{date}@{40-hex-sha})', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0'
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.hermes_ref).toBe('v2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0')
  })

  it('accepts another valid upstream pin at a different release', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.28@0123456789abcdef0123456789abcdef01234567'
    expect(validate(f).ok).toBe(true)
  })

  it('rejects a retired -smd.N fork tag', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16-smd.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a retired -smd.security.N fork tag', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16-smd.security.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a bare date-tag (no @sha)', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'hermes_ref' && e.code === 'InvalidFormat')).toBe(true)
  })

  it('rejects legacy SemVer-style tags (year is not 4 digits)', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v0.14.0@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a missing v-prefix', () => {
    const f = validFixture()
    f['hermes_ref'] = '2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a short SHA (fewer than 40 hex chars)', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@a91a57fa'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a long SHA (more than 40 hex chars)', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@a91a57fa5a13d516c38b07a141a9ce8a3daabeb0ff'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a non-hex SHA', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@zz1a57fa5a13d516c38b07a141a9ce8a3daabeb0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects an uppercase SHA (must be lowercase hex)', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@A91A57FA5A13D516C38B07A141A9CE8A3DAABEB0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a date-tag with an empty @sha', () => {
    const f = validFixture()
    f['hermes_ref'] = 'v2026.5.16@'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects a bare content-hash SHA (no v{date}@ prefix)', () => {
    const f = validFixture()
    f['hermes_ref'] = '7ce6b504a269ac3f9aed5b406b7a18c432e2fdb5'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
  })

  it('rejects the empty string with MissingField/EmptyField, not InvalidFormat', () => {
    const f = validFixture()
    f['hermes_ref'] = ''
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    // The required-string check fires first and exclusively when the value
    // is empty; the fork-tag check intentionally no-ops on the empty case
    // so authors only see one error per defect.
    expect(codesOf(r.errors)).toContain('EmptyField')
    expect(codesOf(r.errors)).not.toContain('InvalidFormat')
  })

  it('rejects a missing field with MissingField, not InvalidFormat', () => {
    const f = validFixture()
    delete f['hermes_ref']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('MissingField')
    expect(codesOf(r.errors)).not.toContain('InvalidFormat')
  })
})

// -----------------------------------------------------------------------------
// users[].voice_profile_id — multi-user voice (#858)
//
// Per-user voice profiles let a customer attach distinct writing-voice
// signatures to individual reviewers (partner Sarah vs. associate Mike
// vs. paralegal Jane). The field is optional and backwards-compatible:
// customers without per-user voice ship the field absent and every
// reviewer inherits the customer-level general voice.
// -----------------------------------------------------------------------------

describe('validate — users[].voice_profile_id (#858)', () => {
  it('accepts users without voice_profile_id (backwards compat)', () => {
    const r = validate(validFixture())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.users[0].voice_profile_id).toBeNull()
    expect(r.value.users[1].voice_profile_id).toBeNull()
  })

  it('accepts users with a well-formed voice_profile_id slug', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'partner-sarah'
    ;(f['users'] as Array<Record<string, unknown>>)[1].voice_profile_id = 'paralegal-mike'
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.users[0].voice_profile_id).toBe('partner-sarah')
    expect(r.value.users[1].voice_profile_id).toBe('paralegal-mike')
  })

  it('rejects a non-string voice_profile_id', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 42
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.code === 'TypeMismatch' && e.path === 'users[0].voice_profile_id')
    ).toBe(true)
  })

  it('rejects voice_profile_id with uppercase', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'Partner-Sarah'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.code === 'InvalidSlug' && e.path === 'users[0].voice_profile_id')
    ).toBe(true)
  })

  it('rejects voice_profile_id with leading dash', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = '-sarah'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects voice_profile_id over 32 chars', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'a'.repeat(33)
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects duplicate voice_profile_id across users', () => {
    // Two users sharing a profile would defeat the per-user attribution
    // model — the transform could not tell which reviewer's voice was
    // actually applied. Treat as an authoring error.
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'shared-slug'
    ;(f['users'] as Array<Record<string, unknown>>)[1].voice_profile_id = 'shared-slug'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('DuplicateVoiceProfileId')
    expect(r.errors.some((e) => e.path === 'users[1].voice_profile_id')).toBe(true)
  })

  it('allows mix of users with and without voice_profile_id', () => {
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'partner-sarah'
    // users[1] left without voice_profile_id — inherits general voice
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.users[0].voice_profile_id).toBe('partner-sarah')
    expect(r.value.users[1].voice_profile_id).toBeNull()
  })

  it('does not echo voice_profile_id values in unrelated error messages', () => {
    // The slug itself is not secret, but the validator should not
    // surface it in errors unrelated to it. Smoke test that duplicate
    // detection cites the field by path, not by mixing it into other
    // error messages.
    const f = validFixture()
    ;(f['users'] as Array<Record<string, unknown>>)[0].voice_profile_id = 'real-slug'
    ;(f['users'] as Array<Record<string, unknown>>)[1].voice_profile_id = 'real-slug'
    delete f['vertical']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    const verticalErr = r.errors.find((e) => e.path === 'vertical')
    expect(verticalErr?.message).not.toContain('real-slug')
  })
})

// -----------------------------------------------------------------------------
// voice_cohorts — per-recipient cohort taxonomy (#857)
//
// Customers declare the cohort vocabulary their voice samples are
// partitioned into. Omission accepts the BASE_VOICE_COHORTS default;
// presence requires a non-empty slug list with unique entries.
// -----------------------------------------------------------------------------

describe('validate — voice_cohorts (#857)', () => {
  it('accepts customer.yaml with no voice_cohorts block (defaults to base)', () => {
    const r = validate(validFixture())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.voice_cohorts).toBeNull()
  })

  it('accepts a customer-extended cohort list', () => {
    const f = validFixture()
    f['voice_cohorts'] = {
      cohorts: ['client', 'opposing-counsel', 'court', 'internal', 'mediator'],
    }
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.voice_cohorts?.cohorts).toEqual([
      'client',
      'opposing-counsel',
      'court',
      'internal',
      'mediator',
    ])
    expect(r.value.voice_cohorts?.min_samples_per_cohort).toBeNull()
  })

  it('accepts a customer-dropped cohort list (no court for transactional firms)', () => {
    const f = validFixture()
    f['voice_cohorts'] = {
      cohorts: ['client', 'opposing-counsel', 'internal'],
    }
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.voice_cohorts?.cohorts).toHaveLength(3)
  })

  it('accepts voice_cohorts with min_samples_per_cohort override', () => {
    const f = validFixture()
    f['voice_cohorts'] = {
      cohorts: ['client', 'opposing-counsel'],
      min_samples_per_cohort: 12,
    }
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.voice_cohorts?.min_samples_per_cohort).toBe(12)
  })

  it('rejects voice_cohorts as a non-object', () => {
    const f = validFixture()
    f['voice_cohorts'] = 'client,opposing-counsel'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'voice_cohorts' && e.code === 'TypeMismatch')).toBe(true)
  })

  it('rejects voice_cohorts without a cohorts field', () => {
    const f = validFixture()
    f['voice_cohorts'] = { min_samples_per_cohort: 8 }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'voice_cohorts.cohorts' && e.code === 'MissingField')
    ).toBe(true)
  })

  it('rejects voice_cohorts.cohorts that is empty', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EmptyList')
  })

  it('rejects voice_cohorts.cohorts entries that are not strings', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: ['client', 42, 'court'] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'voice_cohorts.cohorts[1]' && e.code === 'TypeMismatch')
    ).toBe(true)
  })

  it('rejects cohort slugs that fail the slug pattern', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: ['Client', 'opposing_counsel'] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidSlug')
  })

  it('rejects duplicate cohort slugs', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: ['client', 'opposing-counsel', 'client'] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('DuplicateVoiceCohort')
    expect(r.errors.some((e) => e.path === 'voice_cohorts.cohorts[2]')).toBe(true)
  })

  it('rejects min_samples_per_cohort ≤ 0', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: ['client'], min_samples_per_cohort: 0 }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'voice_cohorts.min_samples_per_cohort' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('rejects non-integer min_samples_per_cohort', () => {
    const f = validFixture()
    f['voice_cohorts'] = { cohorts: ['client'], min_samples_per_cohort: 5.5 }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })
})

// -----------------------------------------------------------------------------
// resolveCohortVocabulary — small helper that materializes the default
// -----------------------------------------------------------------------------

describe('resolveCohortVocabulary (#857)', () => {
  it('returns BASE_VOICE_COHORTS when voice_cohorts is null', async () => {
    const { resolveCohortVocabulary, BASE_VOICE_COHORTS } =
      await import('../src/lib/operator/customer-yaml')
    expect(resolveCohortVocabulary(null)).toEqual(BASE_VOICE_COHORTS)
  })

  it('returns the customer cohort list when present', async () => {
    const { resolveCohortVocabulary } = await import('../src/lib/operator/customer-yaml')
    const resolved = resolveCohortVocabulary({
      cohorts: ['client', 'mediator'],
      min_samples_per_cohort: null,
    })
    expect(resolved).toEqual(['client', 'mediator'])
  })
})

// -----------------------------------------------------------------------------
// memory.retention block — audit-retention.md (#893)
// -----------------------------------------------------------------------------

describe('validate — memory.retention', () => {
  function withRetention(retention: Record<string, unknown>): Record<string, unknown> {
    const f = validFixture()
    const memory = f['memory'] as Record<string, unknown>
    memory['retention'] = retention
    return f
  }

  it('accepts a customer.yaml with no retention block', () => {
    const r = validate(validFixture())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.memory.retention).toBeNull()
  })

  it('accepts retention.audit_log_days equal to the law-firm default (2555)', () => {
    const r = validate(withRetention({ audit_log_days: 2555 }))
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.memory.retention?.audit_log_days).toBe(2555)
  })

  it('accepts retention.audit_log_days above the law-firm default (override-up)', () => {
    const r = validate(withRetention({ audit_log_days: 3650 }))
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.memory.retention?.audit_log_days).toBe(3650)
  })

  it('rejects retention.audit_log_days below the law-firm default', () => {
    const r = validate(withRetention({ audit_log_days: 1825 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('RetentionOverrideBelowDefault')
    expect(r.errors.some((e) => e.path === 'memory.retention.audit_log_days')).toBe(true)
  })

  it('rejects retention.audit_log_days above the sanity cap (>36500)', () => {
    const r = validate(withRetention({ audit_log_days: 73000 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('RetentionOverrideUnreasonable')
  })

  it('rejects non-positive retention.audit_log_days', () => {
    const r = validate(withRetention({ audit_log_days: 0 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects non-integer retention.audit_log_days', () => {
    const r = validate(withRetention({ audit_log_days: 2555.5 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects non-object retention block', () => {
    const f = validFixture()
    const memory = f['memory'] as Record<string, unknown>
    memory['retention'] = 'never'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'memory.retention')).toBe(true)
  })

  it('uses the marketing-agency default (1095) when vertical=marketing-agency', () => {
    // 1095 is the marketing-agency floor; 2555 is the law-firm floor.
    // For marketing-agency, 1095 passes; for law-firm, 1095 would be rejected.
    const f = validFixture()
    f['vertical'] = 'marketing-agency'
    delete f['practice_areas']
    const memory = f['memory'] as Record<string, unknown>
    memory['retention'] = { audit_log_days: 1095 }
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.memory.retention?.audit_log_days).toBe(1095)
  })

  it('rejects below-marketing-agency-default audit_log_days for marketing-agency vertical', () => {
    const f = validFixture()
    f['vertical'] = 'marketing-agency'
    delete f['practice_areas']
    const memory = f['memory'] as Record<string, unknown>
    memory['retention'] = { audit_log_days: 365 }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('RetentionOverrideBelowDefault')
  })

  it('accepts the other retention.*_days fields as positive integers', () => {
    const r = validate(
      withRetention({
        matters_days: 730,
        documents_days: 365,
        recipients_days: 730,
        voice_samples_days: 365,
        audit_log_days: 2555,
        drafts_days: 90,
      })
    )
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    const ret = r.value.memory.retention
    expect(ret?.matters_days).toBe(730)
    expect(ret?.documents_days).toBe(365)
    expect(ret?.recipients_days).toBe(730)
    expect(ret?.voice_samples_days).toBe(365)
    expect(ret?.drafts_days).toBe(90)
  })

  it('rejects non-positive matters_days', () => {
    const r = validate(withRetention({ matters_days: -1 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'memory.retention.matters_days')).toBe(true)
  })

  it('reports the supplied value and the vertical minimum in the below-default error', () => {
    const r = validate(withRetention({ audit_log_days: 1000 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    const err = r.errors.find((e) => e.code === 'RetentionOverrideBelowDefault')
    expect(err).toBeDefined()
    expect(err?.message).toContain('1000')
    expect(err?.message).toContain('2555')
    expect(err?.message).toContain('law-firm')
  })
})

// -----------------------------------------------------------------------------
// compliance_enabled (#895)
// -----------------------------------------------------------------------------

describe('validate — compliance_enabled (#895)', () => {
  it('defaults to false when the field is omitted', () => {
    const r = validate(validFixture())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.compliance_enabled).toBe(false)
  })

  it('accepts compliance_enabled: true', () => {
    const f = validFixture()
    f['compliance_enabled'] = true
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.compliance_enabled).toBe(true)
  })

  it('accepts compliance_enabled: false explicitly', () => {
    const f = validFixture()
    f['compliance_enabled'] = false
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.compliance_enabled).toBe(false)
  })

  it('rejects non-boolean compliance_enabled', () => {
    const f = validFixture()
    f['compliance_enabled'] = 'yes'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'compliance_enabled' && e.code === 'TypeMismatch')).toBe(
      true
    )
  })

  it('treats null as omitted (defaults to false)', () => {
    const f = validFixture()
    f['compliance_enabled'] = null
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.compliance_enabled).toBe(false)
  })
})

// -----------------------------------------------------------------------------
// mcp_connector block — Operator <-> Claude MCP connector (Phase 1, fail-closed)
// -----------------------------------------------------------------------------

describe('validate — mcp_connector', () => {
  it('defaults to disabled/allowlist/empty when the block is omitted', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector).toEqual({
      enabled: false,
      data_posture: 'open',
      policy: 'allowlist',
      allowed_domains: [],
      default_profile: null,
      ttl_days: 30,
      access: [],
    })
  })

  it('defaults policy to allowlist (fail-closed) when enabled without a policy', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [{ email: 'partner@firm.com', profile: 'marcus' }],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.policy).toBe('allowlist')
  })

  it('accepts an open policy with allowed_domains and an active default_profile', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      policy: 'open',
      allowed_domains: ['Firm.com', 'partners.firm.com'],
      default_profile: 'marcus',
      ttl_days: 7,
      access: [],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.policy).toBe('open')
    expect(r.value.mcp_connector.allowed_domains).toEqual(['firm.com', 'partners.firm.com'])
    expect(r.value.mcp_connector.default_profile).toBe('marcus')
    expect(r.value.mcp_connector.ttl_days).toBe(7)
  })

  it('rejects an open policy with no allowed_domains', () => {
    const f = validFixture()
    f['mcp_connector'] = { enabled: true, policy: 'open', default_profile: 'marcus', access: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.allowed_domains' && e.code === 'MissingField')
    ).toBe(true)
  })

  it('rejects an open policy with no default_profile', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      policy: 'open',
      allowed_domains: ['firm.com'],
      access: [],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.default_profile' && e.code === 'MissingField')
    ).toBe(true)
  })

  it('rejects a default_profile that is not an active persona', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      policy: 'open',
      allowed_domains: ['firm.com'],
      default_profile: 'ghost',
      access: [],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.default_profile' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects an unknown policy value', () => {
    const f = validFixture()
    f['mcp_connector'] = { enabled: true, policy: 'everyone', access: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.policy' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects a malformed allowed_domains entry', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      policy: 'open',
      allowed_domains: ['not a domain'],
      default_profile: 'marcus',
      access: [],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'mcp_connector.allowed_domains[0]' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('rejects a ttl_days above the 90-day ceiling (never infinite)', () => {
    const f = validFixture()
    f['mcp_connector'] = { enabled: true, ttl_days: 365, access: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.ttl_days' && e.code === 'TypeMismatch')
    ).toBe(true)
  })

  it('accepts an enabled connector binding an authored user to an active persona', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      data_posture: 'open',
      access: [{ email: 'partner@firm.com', profile: 'marcus' }],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.enabled).toBe(true)
    expect(r.value.mcp_connector.access).toEqual([{ email: 'partner@firm.com', profile: 'marcus' }])
  })

  it('accepts an explicit customer-scoped Clerk subject', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [
        {
          email: 'partner@firm.com',
          profile: 'marcus',
          clerk_subject: 'user_3E1RPGrTMxkSqciXMTyybUNSJWu',
        },
      ],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.access[0]).toMatchObject({
      clerk_subject: 'user_3E1RPGrTMxkSqciXMTyybUNSJWu',
    })
  })

  it('accepts multiple customer-scoped Clerk subjects', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [
        {
          email: 'partner@firm.com',
          profile: 'marcus',
          clerk_subjects: ['user_primary', 'user_secondary'],
        },
      ],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.access[0]).toMatchObject({
      clerk_subjects: ['user_primary', 'user_secondary'],
    })
  })

  it('rejects duplicate Clerk subjects', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [
        {
          email: 'partner@firm.com',
          profile: 'marcus',
          clerk_subjects: ['user_duplicate', 'user_duplicate'],
        },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'mcp_connector.access[0].clerk_subjects' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('rejects a malformed Clerk subject', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [
        {
          email: 'partner@firm.com',
          profile: 'marcus',
          clerk_subject: 'not-a-clerk-user',
        },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'mcp_connector.access[0].clerk_subject' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })

  it('accepts data_posture: firm_only', () => {
    const f = validFixture()
    f['mcp_connector'] = { enabled: true, data_posture: 'firm_only', access: [] }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.mcp_connector.data_posture).toBe('firm_only')
  })

  it('rejects an unknown data_posture', () => {
    const f = validFixture()
    f['mcp_connector'] = { enabled: true, data_posture: 'public' }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.data_posture' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects an access email that is not an authored user (fail-closed reach)', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [{ email: 'outsider@elsewhere.com', profile: 'marcus' }],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.access[0].email' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects an access profile that is not an active persona slug', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [{ email: 'partner@firm.com', profile: 'ghost' }],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'mcp_connector.access[0].profile' && e.code === 'EnumViolation'
      )
    ).toBe(true)
  })

  it('rejects binding the same email twice', () => {
    const f = validFixture()
    f['mcp_connector'] = {
      enabled: true,
      access: [
        { email: 'partner@firm.com', profile: 'marcus' },
        { email: 'partner@firm.com', profile: 'marcus' },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'mcp_connector.access[1].email' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects a non-mapping mcp_connector block', () => {
    const f = validFixture()
    f['mcp_connector'] = 'on'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'mcp_connector' && e.code === 'TypeMismatch')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// ADR 0021 — Stream D bundles + Stream B cron (per-persona)
// -----------------------------------------------------------------------------

/**
 * Helper: take the base persona and graft a bundles[] / cron[] block onto it.
 * Returns the modified fixture; tests mutate further as needed.
 */
function withBundlesAndCron(): Record<string, unknown> {
  const f = validFixture()
  const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
  ;(persona['skills'] as Array<{ initiation: { scheduled: boolean } }>)[0].initiation.scheduled =
    true
  persona['bundles'] = [
    {
      slug: 'pi-intake',
      description: 'Intake triage + conflict screen',
      skills: ['inbox-triage-and-draft', 'conflict-check'],
    },
  ]
  persona['cron'] = [
    {
      skill: 'inbox-triage-and-draft',
      schedule: '0 9 * * *',
      wake_policy: 'always',
    },
  ]
  return f
}

describe('validate — ADR 0021 bundles', () => {
  it('accepts a valid bundles[] block', () => {
    const r = validate(withBundlesAndCron())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.personas[0].bundles).toHaveLength(1)
    expect(r.value.personas[0].bundles[0].slug).toBe('pi-intake')
    expect(r.value.personas[0].bundles[0].skills).toEqual([
      'inbox-triage-and-draft',
      'conflict-check',
    ])
    expect(r.value.personas[0].bundles[0].instruction).toBeNull()
  })

  it('rejects duplicate bundle slug within a persona', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['bundles'] as unknown[]).push({
      slug: 'pi-intake', // duplicate
      description: 'duplicate',
      skills: ['conflict-check'],
    })
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('DuplicateBundleSlug')
  })

  it('rejects bundle that references unknown skill', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['bundles'] as Record<string, unknown>[])[0]['skills'] = [
      'inbox-triage-and-draft',
      'does-not-exist',
    ]
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownBundleSkill')
  })

  it('rejects bundle referencing a disabled skill', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    // Disable conflict-check
    const skills = persona['skills'] as Record<string, unknown>[]
    skills[1] = { ...skills[1], enabled: false }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownBundleSkill')
  })

  it('rejects bundle missing required description', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    delete (persona['bundles'] as Record<string, unknown>[])[0]['description']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.code === 'MissingField' && e.path.endsWith('.description'))).toBe(
      true
    )
  })
})

describe('validate — ADR 0021 cron', () => {
  it('accepts a valid cron[] block with wake_policy=always', () => {
    const r = validate(withBundlesAndCron())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.personas[0].cron).toHaveLength(1)
    expect(r.value.personas[0].cron[0].wake_policy).toBe('always')
    expect(r.value.personas[0].cron[0].pre_run).toBeNull()
  })

  it('accepts wake_policy=pre_run_decides with a pre_run path', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0] = {
      skill: 'inbox-triage-and-draft',
      schedule: 'every 5m',
      pre_run: 'pre_run.py',
      wake_policy: 'pre_run_decides',
    }
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.personas[0].cron[0].pre_run).toBe('pre_run.py')
  })

  it('rejects unknown cron schedule grammar', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0]['schedule'] = 'not a schedule'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidCronSchedule')
  })

  it('rejects cron referencing an unknown skill', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0]['skill'] = 'does-not-exist'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownCronSkill')
  })

  it('rejects pre_run set alongside wake_policy=always', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0]['pre_run'] = 'pre_run.py'
    // wake_policy stays 'always' from withBundlesAndCron base
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidCronWakePolicy')
  })

  it('rejects wake_policy=pre_run_decides without pre_run', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0]['wake_policy'] = 'pre_run_decides'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.code === 'MissingField' && e.path.endsWith('.pre_run'))).toBe(
      true
    )
  })

  it('rejects invalid wake_policy enum', () => {
    const f = withBundlesAndCron()
    const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
    ;(persona['cron'] as Record<string, unknown>[])[0]['wake_policy'] = 'maybe'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidCronWakePolicy')
  })
})

// -----------------------------------------------------------------------------
// ADR 0021 — Stream E webhook_url + webhook_triggers
// -----------------------------------------------------------------------------

function withWebhooks(): Record<string, unknown> {
  const f = withBundlesAndCron()
  const persona = (f['personas'] as unknown[])[0] as Record<string, unknown>
  ;(persona['skills'] as Array<{ initiation: { webhook: boolean } }>)[0].initiation.webhook = true
  const connectors = f['connectors'] as Record<string, Record<string, unknown>>
  connectors['PracticeManagement']['webhook_url'] =
    'https://hermes-smith-pi-firm.fly.dev/webhooks/practice_management'
  f['webhook_triggers'] = [
    {
      source: 'filevine',
      event_type: 'matter.created',
      skill: 'inbox-triage-and-draft',
      persona: 'marcus',
    },
  ]
  return f
}

describe('validate — ADR 0021 webhook_url', () => {
  it('accepts a valid connector webhook_url', () => {
    const r = validate(withWebhooks())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.connectors.PracticeManagement?.webhook_url).toBe(
      'https://hermes-smith-pi-firm.fly.dev/webhooks/practice_management'
    )
  })

  it('rejects webhook_url that does not match the customer-bound pattern', () => {
    const f = withWebhooks()
    const connectors = f['connectors'] as Record<string, Record<string, unknown>>
    connectors['PracticeManagement']['webhook_url'] = 'https://example.com/webhook'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidWebhookUrl')
  })

  it('rejects webhook_url pointing at a different customer slug (isolation)', () => {
    const f = withWebhooks()
    const connectors = f['connectors'] as Record<string, Record<string, unknown>>
    connectors['PracticeManagement']['webhook_url'] =
      'https://hermes-other-firm.fly.dev/webhooks/practice_management'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('IsolationViolation')
  })
})

describe('validate — ADR 0021 webhook_triggers', () => {
  it('accepts a valid webhook_triggers list', () => {
    const r = validate(withWebhooks())
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.webhook_triggers).toHaveLength(1)
    expect(r.value.webhook_triggers[0].source).toBe('filevine')
    expect(r.value.webhook_triggers[0].event_type).toBe('matter.created')
  })

  it('rejects trigger whose source has no connector with webhook_url', () => {
    const f = withWebhooks()
    ;(f['webhook_triggers'] as Record<string, unknown>[])[0]['source'] = 'microsoft-graph'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownWebhookSource')
  })

  // ADR 0078 / email-channel-seam D1: the msgraph mail seam has "no public
  // webhook endpoint for mail at all" — inbound is the overlay delta poller,
  // which stamps source: msgraph / message.received into the same gate→router
  // path. So the trigger is the authored wake-path routing declaration and there
  // is no webhook_url to pair it against. Requiring one would force the config
  // to fabricate a nonexistent endpoint (this rejection broke the smd-staging
  // projection on main, 2026-07-24).
  it('accepts a msgraph trigger source with no webhook_url (poll-driven inbound)', () => {
    const f = withWebhooks()
    const connectors = f['connectors'] as Record<string, Record<string, unknown>>
    connectors['Email'] = {
      adapter: 'msgraph',
      backend: 'mcp:msgraph-mail',
      enabled: true,
      msgraph_auth: {
        tenant_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        client_id: '11111111-2222-3333-4444-555555555555',
        mailbox: 'operator@clientdomain.com',
        secret_ref: 'fly-secret:MSGRAPH_CLIENT_SECRET',
      },
      poll_seconds: 45,
    }
    ;(f['webhook_triggers'] as Record<string, unknown>[])[0]['source'] = 'msgraph'
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.webhook_triggers[0].source).toBe('msgraph')
    expect(r.value.connectors.Email?.webhook_url).toBeNull()
  })

  // The cadence field is optional under msgraph (unauthored ⇒ the overlay
  // poller applies its 45s default), so eligibility must key on the adapter,
  // not on poll_seconds being present.
  it('accepts a msgraph trigger source when poll_seconds is unauthored', () => {
    const f = withWebhooks()
    const connectors = f['connectors'] as Record<string, Record<string, unknown>>
    connectors['Email'] = {
      adapter: 'msgraph',
      backend: 'mcp:msgraph-mail',
      enabled: true,
      msgraph_auth: {
        tenant_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        client_id: '11111111-2222-3333-4444-555555555555',
        mailbox: 'operator@clientdomain.com',
        secret_ref: 'fly-secret:MSGRAPH_CLIENT_SECRET',
      },
    }
    ;(f['webhook_triggers'] as Record<string, unknown>[])[0]['source'] = 'msgraph'
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.connectors.Email?.poll_seconds).toBeNull()
  })

  it('rejects trigger whose persona does not exist', () => {
    const f = withWebhooks()
    ;(f['webhook_triggers'] as Record<string, unknown>[])[0]['persona'] = 'ghost-persona'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownWebhookPersona')
  })

  it('rejects trigger whose skill is not on the target persona', () => {
    const f = withWebhooks()
    ;(f['webhook_triggers'] as Record<string, unknown>[])[0]['skill'] = 'not-a-skill'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownWebhookSkill')
  })

  it('treats missing webhook_triggers as empty list', () => {
    const f = validFixture()
    const r = validate(f)
    if (!r.ok) {
      throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    }
    expect(r.value.webhook_triggers).toEqual([])
  })
})

// -----------------------------------------------------------------------------
// Vertical pinned form + addons + extends (ADR 0022 Stream 1)
// -----------------------------------------------------------------------------

describe('validate — vertical pinned form (ADR 0022)', () => {
  it('accepts bare vertical (back-compat path)', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.vertical).toBe('law-firm')
    expect(r.value.vertical_version).toBeNull()
  })

  it('accepts pinned vertical (law-firm@1.4.0)', () => {
    const f = validFixture()
    f['vertical'] = 'law-firm@1.4.0'
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.vertical).toBe('law-firm')
    expect(r.value.vertical_version).toBe('1.4.0')
  })

  it('rejects malformed semver (missing patch)', () => {
    const f = validFixture()
    f['vertical'] = 'law-firm@1.4'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidVerticalSpec')
  })

  it('rejects pre-release suffix (1.4.0-rc1)', () => {
    const f = validFixture()
    f['vertical'] = 'law-firm@1.4.0-rc1'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidVerticalSpec')
  })

  it('rejects empty version (law-firm@)', () => {
    const f = validFixture()
    f['vertical'] = 'law-firm@'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidVerticalSpec')
  })

  it('rejects empty vertical (@1.4.0)', () => {
    const f = validFixture()
    f['vertical'] = '@1.4.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidVerticalSpec')
  })

  it('rejects unknown vertical in pinned form', () => {
    const f = validFixture()
    f['vertical'] = 'petshop@1.0.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EnumViolation')
  })

  it('rejects non-string vertical', () => {
    const f = validFixture()
    f['vertical'] = 42
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })
})

describe('validate — addons array (ADR 0022)', () => {
  it('accepts omitted addons (defaults to empty list)', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.addons).toEqual([])
  })

  it('accepts empty addons array', () => {
    const f = validFixture()
    f['addons'] = []
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.addons).toEqual([])
  })

  it('accepts single registered addon (law-firm/pi@2.1.0)', () => {
    const f = validFixture()
    f['addons'] = ['law-firm/pi@2.1.0']
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.addons).toEqual([{ vertical: 'law-firm', addon: 'pi', version: '2.1.0' }])
  })

  it('rejects addons as non-array', () => {
    const f = validFixture()
    f['addons'] = 'law-firm/pi@2.1.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects addon entry missing slash', () => {
    const f = validFixture()
    f['addons'] = ['law-firm-pi@2.1.0']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidAddonSpec')
  })

  it('rejects addon entry missing version', () => {
    const f = validFixture()
    f['addons'] = ['law-firm/pi']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidAddonSpec')
  })

  it('rejects addon entry with malformed semver', () => {
    const f = validFixture()
    f['addons'] = ['law-firm/pi@2.1']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidAddonSpec')
  })

  it('rejects addon referencing unknown vertical', () => {
    const f = validFixture()
    f['addons'] = ['petshop/pi@1.0.0']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EnumViolation')
  })

  it('rejects addon slug not registered under the parent vertical', () => {
    const f = validFixture()
    f['addons'] = ['law-firm/notreal@1.0.0']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('UnknownAddon')
  })

  it('rejects duplicate addon entries', () => {
    const f = validFixture()
    f['addons'] = ['law-firm/pi@2.1.0', 'law-firm/pi@2.1.1']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidAddonSpec')
  })
})

describe('validate — extends reserved (ADR 0022)', () => {
  it('rejects top-level extends with explicit error', () => {
    const f = validFixture()
    f['extends'] = 'law-firm@1.0.0'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('ExtendsReserved')
    expect(r.errors.find((e) => e.code === 'ExtendsReserved')?.message).toMatch(/reserved/i)
  })

  it('does not flag extends when absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    // No ExtendsReserved error in the success path.
    expect(r.ok).toBe(true)
  })
})

describe('validate — memory.r2_skill_bodies_* known-optional (ADR 0022)', () => {
  it('accepts memory block without skill_bodies fields', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.memory.r2_skill_bodies_bucket).toBeNull()
    expect(r.value.memory.r2_skill_bodies_prefix).toBeNull()
  })

  it('accepts memory block with skill_bodies fields populated', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, unknown>)['r2_skill_bodies_bucket'] =
      'smd-operator-skill-bodies'
    ;(f['memory'] as Record<string, unknown>)['r2_skill_bodies_prefix'] = 'smith-pi-firm/'
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.memory.r2_skill_bodies_bucket).toBe('smd-operator-skill-bodies')
    expect(r.value.memory.r2_skill_bodies_prefix).toBe('smith-pi-firm/')
  })

  it('rejects skill_bodies_bucket as non-string', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, unknown>)['r2_skill_bodies_bucket'] = 123
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'memory.r2_skill_bodies_bucket' && e.code === 'TypeMismatch')
    ).toBe(true)
  })

  it('rejects skill_bodies_prefix as empty string', () => {
    const f = validFixture()
    ;(f['memory'] as Record<string, unknown>)['r2_skill_bodies_prefix'] = ''
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'memory.r2_skill_bodies_prefix' && e.code === 'EmptyField')
    ).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Telegram block (ADR 0033) — optional; fail-closed allowlist
// -----------------------------------------------------------------------------

describe('validate — telegram block (ADR 0033)', () => {
  it('accepts an enabled block with a non-empty numeric allow_from', () => {
    const f = validFixture()
    f['telegram'] = { enabled: true, allow_from: ['7367659986'], require_mention: false }
    const r = validate(f)
    expect(r.ok).toBe(true)
  })

  it('accepts absence of the block (optional)', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
  })

  it('rejects enabled with an empty allow_from (fail-open trap)', () => {
    const f = validFixture()
    f['telegram'] = { enabled: true, allow_from: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'telegram.allow_from' && e.code === 'MissingField')
    ).toBe(true)
  })

  it('rejects enabled with allow_from omitted', () => {
    const f = validFixture()
    f['telegram'] = { enabled: true }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'telegram.allow_from')).toBe(true)
  })

  it('rejects a non-numeric allow_from entry', () => {
    const f = validFixture()
    f['telegram'] = { enabled: true, allow_from: ['@scott'] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'telegram.allow_from[0]')).toBe(true)
  })

  it('rejects a non-boolean require_mention', () => {
    const f = validFixture()
    f['telegram'] = { enabled: true, allow_from: ['7367659986'], require_mention: 'no' }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'telegram.require_mention')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// google_auth (DWD vs user-OAuth) — ss-console #1213
// -----------------------------------------------------------------------------

describe('validate — google_auth', () => {
  const DWD_SCOPES = [
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/documents',
    'https://www.googleapis.com/auth/spreadsheets',
  ]

  it('defaults google_auth to null when the block is absent (user-OAuth)', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.google_auth).toBeNull()
  })

  it('accepts mode: user_oauth with null subject and empty scopes', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'user_oauth' }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.google_auth).toEqual({
      mode: 'user_oauth',
      subject: null,
      scopes: [],
      managed_mailboxes: [],
    })
  })

  it('accepts a complete dwd block', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'dwd', subject: 'owner@firm.com', scopes: DWD_SCOPES }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.google_auth).toEqual({
      mode: 'dwd',
      subject: 'owner@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [],
    })
  })

  it('rejects an unknown mode', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'service_account', subject: 'owner@firm.com', scopes: DWD_SCOPES }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth.mode' && e.code === 'EnumViolation')).toBe(
      true
    )
  })

  it('fails closed: dwd without a subject', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'dwd', scopes: DWD_SCOPES }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth.subject')).toBe(true)
  })

  it('fails closed: dwd with an empty scopes list', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'dwd', subject: 'owner@firm.com', scopes: [] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth.scopes' && e.code === 'EmptyList')).toBe(
      true
    )
  })

  it('rejects a non-object google_auth', () => {
    const f = validFixture()
    f['google_auth'] = 'dwd'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth' && e.code === 'TypeMismatch')).toBe(true)
  })

  it('accepts a dwd block with an authored managed mailbox', () => {
    const f = validFixture()
    f['google_auth'] = {
      mode: 'dwd',
      subject: 'crane@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [
        {
          address: 'owner@firm.com',
          send_as: ['owner@firm.com', 'team@firm.com'],
        },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.google_auth?.managed_mailboxes).toEqual([
      {
        address: 'owner@firm.com',
        send_as: ['owner@firm.com', 'team@firm.com'],
      },
    ])
  })

  it('defaults managed_mailboxes to [] when absent', () => {
    const f = validFixture()
    f['google_auth'] = { mode: 'dwd', subject: 'crane@firm.com', scopes: DWD_SCOPES }
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.google_auth?.managed_mailboxes).toEqual([])
  })

  it('fails closed: a managed mailbox missing its address', () => {
    const f = validFixture()
    f['google_auth'] = {
      mode: 'dwd',
      subject: 'crane@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [{ send_as: ['owner@firm.com'] }],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth.managed_mailboxes[0].address')).toBe(true)
  })

  it('fails closed: a managed mailbox with an empty send_as list', () => {
    const f = validFixture()
    f['google_auth'] = {
      mode: 'dwd',
      subject: 'crane@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [{ address: 'owner@firm.com', send_as: [] }],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'google_auth.managed_mailboxes[0].send_as' && e.code === 'EmptyList'
      )
    ).toBe(true)
  })

  it('fails closed: a managed mailbox with a non-email send_as entry', () => {
    const f = validFixture()
    f['google_auth'] = {
      mode: 'dwd',
      subject: 'crane@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [{ address: 'owner@firm.com', send_as: ['not-an-email'] }],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'google_auth.managed_mailboxes[0].send_as')).toBe(true)
  })

  it('fails closed: a managed mailbox with legacy action_ceilings', () => {
    const f = validFixture()
    f['google_auth'] = {
      mode: 'dwd',
      subject: 'crane@firm.com',
      scopes: DWD_SCOPES,
      managed_mailboxes: [
        {
          address: 'owner@firm.com',
          send_as: ['owner@firm.com'],
          action_ceilings: { external_send: 'whenever' },
        },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) =>
          e.path === 'google_auth.managed_mailboxes[0].action_ceilings' &&
          e.code === 'LegacyEntitlementField'
      )
    ).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Authority posture (ADR 0041)
// -----------------------------------------------------------------------------

describe('validate — authority posture', () => {
  it('defaults to managed/no-overrides when the block is absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.authority).toEqual({ default: 'managed', overrides: {} })
  })

  it('accepts a valid authority block with per-domain overrides', () => {
    const f = validFixture()
    f['authority'] = {
      default: 'managed',
      overrides: { people_access: 'client', connectors: 'client' },
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.authority.default).toBe('managed')
    expect(r.value.authority.overrides.people_access).toBe('client')
    expect(r.value.authority.overrides.connectors).toBe('client')
  })

  it('accepts default: self_managed with an override pinning a domain back to managed', () => {
    const f = validFixture()
    f['authority'] = { default: 'self_managed', overrides: { connectors: 'managed' } }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.authority.default).toBe('self_managed')
    expect(r.value.authority.overrides.connectors).toBe('managed')
  })

  it('rejects an unknown authority.default value', () => {
    const f = validFixture()
    f['authority'] = { default: 'fully_managed' }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'authority.default' && e.code === 'EnumViolation')).toBe(
      true
    )
  })

  it('rejects an unknown override domain', () => {
    const f = validFixture()
    f['authority'] = { default: 'managed', overrides: { not_a_domain: 'client' } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'authority.overrides.not_a_domain' && e.code === 'UnknownAuthorityDomain'
      )
    ).toBe(true)
  })

  it('rejects an SMD-only domain used as a client switch', () => {
    const f = validFixture()
    f['authority'] = { default: 'managed', overrides: { cost: 'client' } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'authority.overrides.cost' && e.code === 'UnknownAuthorityDomain'
      )
    ).toBe(true)
  })

  it('rejects an invalid override value', () => {
    const f = validFixture()
    f['authority'] = { default: 'managed', overrides: { connectors: 'smd' } }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'authority.overrides.connectors' && e.code === 'EnumViolation'
      )
    ).toBe(true)
  })

  it('rejects a non-object authority block', () => {
    const f = validFixture()
    f['authority'] = 'managed'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'authority' && e.code === 'TypeMismatch')).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// Credential custody (ADR 0042)
// -----------------------------------------------------------------------------

describe('validate — credential custody', () => {
  it('defaults credential_custody_default to delegated when absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.credential_custody_default).toBe('delegated')
  })

  it('per-connector credential_custody defaults to null (inherit) when absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.connectors.Email?.credential_custody).toBeNull()
  })

  it('accepts an explicit client-level default and per-connector override', () => {
    const f = validFixture()
    f['credential_custody_default'] = 'self_held'
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email']['credential_custody'] =
      'delegated'
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.credential_custody_default).toBe('self_held')
    expect(r.value.connectors.Email?.credential_custody).toBe('delegated')
  })

  it('rejects an invalid client-level default', () => {
    const f = validFixture()
    f['credential_custody_default'] = 'smd-holds'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'credential_custody_default' && e.code === 'EnumViolation')
    ).toBe(true)
  })

  it('rejects an invalid per-connector custody value', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email']['credential_custody'] =
      'shared'
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'connectors.Email.credential_custody' && e.code === 'EnumViolation'
      )
    ).toBe(true)
  })
})

// -----------------------------------------------------------------------------
// relationship: block — authored behavioral lane (ADR 0048)
// -----------------------------------------------------------------------------

describe('validate — relationship block (ADR 0048)', () => {
  it('defaults to { people: [] } when the block is absent', () => {
    const r = validate(validFixture())
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.relationship).toEqual({ people: [] })
  })

  it('accepts a well-formed people list and normalizes optional fields', () => {
    const f = validFixture()
    f['relationship'] = {
      people: [
        {
          id: 'scott-durgan',
          name: 'Scott Durgan',
          role: 'Principal',
          prefers: ['Lead with the material change and its consequence'],
          avoid: ['Inventing time/effort estimates for unscoped work'],
        },
        { id: 'office-manager', name: 'Office Manager' },
      ],
    }
    const r = validate(f)
    if (!r.ok) throw new Error(`expected ok; got: ${JSON.stringify(r.errors)}`)
    expect(r.value.relationship.people).toHaveLength(2)
    expect(r.value.relationship.people[0]).toEqual({
      id: 'scott-durgan',
      name: 'Scott Durgan',
      role: 'Principal',
      prefers: ['Lead with the material change and its consequence'],
      avoid: ['Inventing time/effort estimates for unscoped work'],
    })
    // Optional fields normalize: absent role → null, absent lists → [].
    expect(r.value.relationship.people[1]).toEqual({
      id: 'office-manager',
      name: 'Office Manager',
      role: null,
      prefers: [],
      avoid: [],
    })
  })

  it('rejects a non-mapping relationship block', () => {
    const f = validFixture()
    f['relationship'] = ['not', 'a', 'map']
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'relationship' && e.code === 'TypeMismatch')).toBe(true)
  })

  it('requires id and name on each person', () => {
    const f = validFixture()
    f['relationship'] = { people: [{ role: 'Partner' }] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'relationship.people[0].id' && e.code === 'MissingField')
    ).toBe(true)
    expect(
      r.errors.some((e) => e.path === 'relationship.people[0].name' && e.code === 'MissingField')
    ).toBe(true)
  })

  it('rejects a non-kebab-case id (InvalidSlug)', () => {
    const f = validFixture()
    f['relationship'] = { people: [{ id: 'Scott Durgan', name: 'Scott' }] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some((e) => e.path === 'relationship.people[0].id' && e.code === 'InvalidSlug')
    ).toBe(true)
  })

  it('rejects duplicate person ids', () => {
    const f = validFixture()
    f['relationship'] = {
      people: [
        { id: 'chris', name: 'Chris A' },
        { id: 'chris', name: 'Chris B' },
      ],
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.code === 'DuplicateRelationshipPersonId')).toBe(true)
  })

  it('rejects non-string / empty preference items', () => {
    const f = validFixture()
    f['relationship'] = { people: [{ id: 'p1', name: 'P', prefers: ['', 42] }] }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(
      r.errors.some(
        (e) => e.path === 'relationship.people[0].prefers[0]' && e.code === 'EmptyField'
      )
    ).toBe(true)
    expect(
      r.errors.some(
        (e) => e.path === 'relationship.people[0].prefers[1]' && e.code === 'TypeMismatch'
      )
    ).toBe(true)
  })
})

describe('validate — scope.outbound_roster (ADR 0075)', () => {
  function withOutbound(roster: unknown, inbound?: unknown): Record<string, unknown> {
    const f = validFixture()
    const scope = f['scope'] as Record<string, unknown>
    scope['outbound_roster'] = roster
    if (inbound !== undefined) scope['inbound_allow_from'] = inbound
    return f
  }

  it('accepts a valid client + records_vendor roster and carries it through', () => {
    const r = validate(
      withOutbound([
        { address: 'jane@gmail.com', class: 'client', note: 'PI client on gmail' },
        { address: 'records@radiology.com', class: 'records_vendor' },
      ])
    )
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.scope.outbound_roster).toEqual([
        { address: 'jane@gmail.com', class: 'client', note: 'PI client on gmail' },
        { address: 'records@radiology.com', class: 'records_vendor' },
      ])
    }
  })

  it('accepts an EXACT address at a public-mail provider (PI client on gmail)', () => {
    const r = validate(withOutbound([{ address: 'jane@gmail.com', class: 'client' }]))
    expect(r.ok).toBe(true)
  })

  it('rejects a whole-@domain grant at a public-mail provider', () => {
    const r = validate(withOutbound([{ address: '@gmail.com', class: 'client' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOutboundRoster')
  })

  it('accepts an @domain grant at a firm/vendor domain', () => {
    const r = validate(withOutbound([{ address: '@records-vendor.com', class: 'records_vendor' }]))
    expect(r.ok).toBe(true)
  })

  it('rejects a class outside the closed vocabulary', () => {
    const r = validate(withOutbound([{ address: 'a@b.com', class: 'opposing_counsel' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('EnumViolation')
  })

  it('rejects a malformed address', () => {
    const r = validate(withOutbound([{ address: 'not-an-email', class: 'client' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOutboundRoster')
  })

  it('rejects one address typed as more than one class', () => {
    const r = validate(
      withOutbound([
        { address: 'x@firm-vendor.com', class: 'client' },
        { address: 'x@firm-vendor.com', class: 'records_vendor' },
      ])
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOutboundRoster')
  })

  // ss#2263 — this pair used to be a single "rejects an address also present in
  // inbound_allow_from" case, and that rejection was the defect. It read the reply
  // list as a statement of class. It is not one: it says who the Operator may
  // autonomously REPLY to. Forbidding the overlap meant the only way to make a
  // firm's own client reply-able was to leave them classified as staff — exempt
  // from the content floor (ADR 0072) and the matter-identity gate (ss#2167) — and
  // it made the gate's reply-lane branch unreachable in every authorable config
  // (ss#2271).
  it('accepts a reply-authorized address that also carries a typed class', () => {
    const r = validate(
      withOutbound([{ address: 'client@example.com', class: 'client' }], ['client@example.com'])
    )
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.scope.outbound_roster).toEqual([
        { address: 'client@example.com', class: 'client' },
      ])
      expect(r.value.scope.inbound_allow_from).toEqual(['client@example.com'])
    }
  })

  it('accepts firm_staff, the authored form of "is firm staff"', () => {
    const r = validate(withOutbound([{ address: 'paralegal@firm.example', class: 'firm_staff' }]))
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.scope.outbound_roster).toEqual([
        { address: 'paralegal@firm.example', class: 'firm_staff' },
      ])
    }
  })

  it('still rejects one address typed as more than one class, overlap or not', () => {
    const r = validate(
      withOutbound(
        [
          { address: 'client@example.com', class: 'client' },
          { address: 'client@example.com', class: 'firm_staff' },
        ],
        ['client@example.com']
      )
    )
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOutboundRoster')
  })

  it('rejects a non-list outbound_roster', () => {
    const r = validate(withOutbound('nope'))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('defaults to [] when unauthored', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.outbound_roster).toEqual([])
  })
})

describe('validate — scope.admins (ADR 0085 §2)', () => {
  function withAdmins(admins: unknown): Record<string, unknown> {
    const f = validFixture()
    const scope = f['scope'] as Record<string, unknown>
    scope['admins'] = admins
    return f
  }

  it('accepts a person list and carries it through canonicalized', () => {
    const r = validate(withAdmins(['Dana@Example-Firm.com', 'lee@example-firm.com']))
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.scope.admins).toEqual(['dana@example-firm.com', 'lee@example-firm.com'])
    }
  })

  it('rejects an @domain grant — an admin is a person, never a domain', () => {
    const r = validate(withAdmins(['@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidAdminList')
  })

  it('rejects a display-name form', () => {
    const r = validate(withAdmins(['Dana Reed <dana@example-firm.com>']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidAdminList')
  })

  it('rejects a duplicate address', () => {
    const r = validate(withAdmins(['dana@example-firm.com', 'DANA@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidAdminList')
  })

  it('rejects a malformed address', () => {
    const r = validate(withAdmins(['not-an-email']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidAdminList')
  })

  it('rejects a non-string entry', () => {
    const r = validate(withAdmins([{ email: 'dana@example-firm.com' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('MissingField')
  })

  it('rejects a non-list admins', () => {
    const r = validate(withAdmins('dana@example-firm.com'))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('defaults to [] when unauthored (fail-closed: no admins exist)', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.admins).toEqual([])
  })

  it('does not require an admin to be on the inbound roster', () => {
    const f = withAdmins(['dana@example-firm.com'])
    const scope = f['scope'] as Record<string, unknown>
    scope['inbound_allow_from'] = []
    expect(validate(f).ok).toBe(true)
  })
})

describe('validate — scope.ops_reply_from (ss-console#2546)', () => {
  function withOpsReply(list: unknown): Record<string, unknown> {
    const f = validFixture()
    const scope = f['scope'] as Record<string, unknown>
    scope['ops_reply_from'] = list
    return f
  }

  it('accepts SMD person addresses at either domain, canonicalized', () => {
    const r = validate(
      withOpsReply(['Scott@SMD.services', 'team@smd.services', 'smdurgan@smdurgan.com'])
    )
    expect(r.ok).toBe(true)
    if (r.ok)
      expect(r.value.scope.ops_reply_from).toEqual([
        'scott@smd.services',
        'team@smd.services',
        'smdurgan@smdurgan.com',
      ])
  })

  // The rule the key exists for. The list decides whose answer resolves an
  // operations request, so a config that could name a third party would hand
  // the answering power away from SMD entirely.
  it("rejects an address outside SMD's own mail domains", () => {
    const r = validate(withOpsReply(['christa@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(codesOf(r.errors)).toContain('InvalidOpsReplyFrom')
      expect(r.errors.some((e) => e.message.includes('not at an SMD domain'))).toBe(true)
    }
  })

  it('rejects a lookalike domain rather than matching on a suffix', () => {
    // notsmd.services ends with the same characters; the check is on the @
    // boundary, not on the tail of the string.
    const r = validate(withOpsReply(['scott@notsmd.services']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOpsReplyFrom')
  })

  it('rejects an @domain grant — an answer comes from a person', () => {
    const r = validate(withOpsReply(['@smd.services']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOpsReplyFrom')
  })

  it('rejects a duplicate, so the authored list is the count of who answers', () => {
    const r = validate(withOpsReply(['scott@smd.services', 'SCOTT@smd.services']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOpsReplyFrom')
  })

  it('rejects a malformed address', () => {
    const r = validate(withOpsReply(['not-an-email']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidOpsReplyFrom')
  })

  it('rejects a non-string entry', () => {
    const r = validate(withOpsReply([{ email: 'scott@smd.services' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('MissingField')
  })

  it('rejects a non-list ops_reply_from', () => {
    const r = validate(withOpsReply('scott@smd.services'))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('defaults to [] when unauthored, so no reply resolves anything', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.ops_reply_from).toEqual([])
  })

  // It is NOT the admin list and NOT the roster. An SMD address that answers
  // operations requests is not thereby an Operator admin, and nothing here may
  // make it one.
  it('does not require the answering address to be an admin or on the roster', () => {
    const f = withOpsReply(['team@smd.services'])
    const scope = f['scope'] as Record<string, unknown>
    scope['admins'] = []
    scope['inbound_allow_from'] = []
    const r = validate(f)
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.scope.admins).toEqual([])
      expect(r.value.scope.inbound_allow_from).not.toContain('team@smd.services')
    }
  })
})

describe('validate — scope.rule_requests_to (ss-console#2546)', () => {
  const ADMINS = ['dana@example-firm.com', 'lee@example-firm.com']

  function withRouting(routing: unknown, admins: unknown = ADMINS): Record<string, unknown> {
    const f = validFixture()
    const scope = f['scope'] as Record<string, unknown>
    scope['admins'] = admins
    scope['rule_requests_to'] = routing
    return f
  }

  it('accepts a subset of the admin list, canonicalized', () => {
    const r = validate(withRouting(['Dana@Example-Firm.com']))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.rule_requests_to).toEqual(['dana@example-firm.com'])
  })

  it('accepts every admin, which is the no-split default a firm may author', () => {
    const r = validate(withRouting(ADMINS))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.rule_requests_to).toEqual(ADMINS)
  })

  // The rule the key exists for. Routing may narrow who is PAGED; it may never
  // name somebody who could not act on the request, and it may never widen
  // authority by naming a non-admin.
  it('rejects an address that is not on scope.admins', () => {
    const r = validate(withRouting(['sarah@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(codesOf(r.errors)).toContain('InvalidRuleRequestsTo')
      expect(r.errors.some((e) => e.message.includes('not on scope.admins'))).toBe(true)
    }
  })

  it('rejects an @domain grant — a request goes to a person, not to a building', () => {
    const r = validate(withRouting(['@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidRuleRequestsTo')
  })

  it('rejects a duplicate, so the authored list is the count of who is paged', () => {
    const r = validate(withRouting(['dana@example-firm.com', 'DANA@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidRuleRequestsTo')
  })

  it('rejects a malformed address', () => {
    const r = validate(withRouting(['not-an-email']))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidRuleRequestsTo')
  })

  it('rejects a non-string entry', () => {
    const r = validate(withRouting([{ email: 'dana@example-firm.com' }]))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('MissingField')
  })

  it('rejects a non-list rule_requests_to', () => {
    const r = validate(withRouting('dana@example-firm.com'))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('defaults to [] when unauthored, so nothing claims an admin was asked', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.scope.rule_requests_to).toEqual([])
  })

  // An admin list that is itself invalid drops the offending entries, and the
  // subset check must then refuse the routing rather than silently accept it
  // against a shorter list than the author wrote.
  it('refuses routing to an address the admin list rejected', () => {
    const r = validate(withRouting(['dana@example-firm.com'], ['@example-firm.com']))
    expect(r.ok).toBe(false)
    if (!r.ok) {
      expect(codesOf(r.errors)).toContain('InvalidAdminList')
      expect(codesOf(r.errors)).toContain('InvalidRuleRequestsTo')
    }
  })
})

describe('validate — send exposure classes (ADR 0075)', () => {
  function withExposure(exposure: Record<string, unknown>): Record<string, unknown> {
    const f = validFixture()
    const persona = (f['personas'] as Record<string, unknown>[])[0]
    persona['entitlements'] = { exposure }
    return f
  }

  it('accepts external_send_client / external_send_vendor, and confirm on them', () => {
    const r = validate(
      withExposure({
        external_send_client: 'autonomous',
        external_send_vendor: 'confirm',
      })
    )
    expect(r.ok).toBe(true)
    if (r.ok) {
      expect(r.value.personas[0].entitlements.exposure.external_send_client).toBe('autonomous')
      expect(r.value.personas[0].entitlements.exposure.external_send_vendor).toBe('confirm')
    }
  })

  it('rejects confirm on a non-send class', () => {
    const r = validate(withExposure({ internal_write: 'confirm' }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidActionCeiling')
  })

  // ss-console#2536. `commitment: confirm` is the one non-send class that may
  // be authored as confirm, and it may be authored that way on the EXPOSURE
  // only. A commitment is the firm's own record gaining something, an admin can
  // be shown exactly what it will be and can answer; the ceiling map is the
  // entitlement dial's Machine-side clamp, derived from the routine grid's send
  // tiers, and commitment has none to derive from.
  it('accepts confirm on commitment in exposure', () => {
    const r = validate(withExposure({ commitment: 'confirm' }))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.personas[0].entitlements.exposure.commitment).toBe('confirm')
  })

  it('rejects confirm on commitment in exposure_ceiling', () => {
    const f = validFixture()
    const persona = (f['personas'] as Record<string, unknown>[])[0]
    persona['entitlements'] = {
      exposure: { commitment: 'confirm' },
      exposure_ceiling: { commitment: 'confirm' },
    }
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidActionCeiling')
  })

  it('still rejects confirm on destructive', () => {
    // A destructive act REMOVES something, and the read-back cannot show the
    // admin what would be lost. It stays refused-or-drafted until somebody
    // argues otherwise in writing.
    const r = validate(withExposure({ destructive: 'confirm' }))
    expect(r.ok).toBe(false)
    if (!r.ok) expect(codesOf(r.errors)).toContain('InvalidActionCeiling')
  })
})

// -----------------------------------------------------------------------------
// Email connector: msgraph_auth + poll_seconds (ADR 0078 / email-channel-seam D5)
// -----------------------------------------------------------------------------

describe('validate — connectors.Email msgraph_auth (ADR 0078 D5)', () => {
  const TENANT = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'
  const CLIENT = '11111111-2222-3333-4444-555555555555'

  function validMsgraphAuth(): Record<string, unknown> {
    return {
      tenant_id: TENANT,
      client_id: CLIENT,
      mailbox: 'operator@clientdomain.com',
      secret_ref: 'fly-secret:MSGRAPH_CLIENT_SECRET',
    }
  }

  /** Replace the fixture Email connector with an msgraph-bound one. */
  function withMsgraphEmail(
    mutate?: (auth: Record<string, unknown>, conn: Record<string, unknown>) => void
  ): Record<string, unknown> {
    const f = validFixture()
    const auth = validMsgraphAuth()
    const conn: Record<string, unknown> = {
      adapter: 'msgraph',
      backend: 'mcp:msgraph-mail',
      enabled: true,
      msgraph_auth: auth,
    }
    mutate?.(auth, conn)
    ;(f['connectors'] as Record<string, unknown>)['Email'] = conn
    return f
  }

  it('accepts a valid msgraph Email connector and carries the block through', () => {
    const r = validate(withMsgraphEmail())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.connectors.Email?.msgraph_auth).toEqual(validMsgraphAuth())
    // poll_seconds unauthored ⇒ null (overlay applies the 45s default)
    expect(r.value.connectors.Email?.poll_seconds).toBeNull()
  })

  it('accepts an authored poll_seconds and carries it through', () => {
    const r = validate(withMsgraphEmail((_a, c) => (c['poll_seconds'] = 30)))
    expect(r.ok).toBe(true)
    if (r.ok) expect(r.value.connectors.Email?.poll_seconds).toBe(30)
  })

  it('requires msgraph_auth when the adapter is msgraph', () => {
    const r = validate(withMsgraphEmail((_a, c) => delete c['msgraph_auth']))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('MissingField')
    expect(r.errors.some((e) => e.path === 'connectors.Email.msgraph_auth')).toBe(true)
  })

  it('rejects an msgraph_auth block on a non-msgraph adapter (no dead config)', () => {
    const f = validFixture()
    // fixture Email adapter is "microsoft-graph", not "msgraph"
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email']['msgraph_auth'] =
      validMsgraphAuth()
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
    expect(r.errors.some((e) => e.path === 'connectors.Email.msgraph_auth')).toBe(true)
  })

  it('rejects poll_seconds on a non-msgraph adapter (no dead config)', () => {
    const f = validFixture()
    ;(f['connectors'] as Record<string, Record<string, unknown>>)['Email']['poll_seconds'] = 45
    const r = validate(f)
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'connectors.Email.poll_seconds')).toBe(true)
  })

  it('rejects a non-integer / non-positive poll_seconds under msgraph', () => {
    for (const bad of [0, -1, 2.5, '30']) {
      const r = validate(withMsgraphEmail((_a, c) => (c['poll_seconds'] = bad)))
      expect(r.ok).toBe(false)
    }
  })

  it('rejects a malformed tenant_id / client_id (must be a GUID)', () => {
    for (const key of ['tenant_id', 'client_id']) {
      const r = validate(withMsgraphEmail((a) => (a[key] = 'not-a-guid')))
      expect(r.ok).toBe(false)
      if (r.ok) continue
      expect(codesOf(r.errors)).toContain('InvalidFormat')
      expect(r.errors.some((e) => e.path === `connectors.Email.msgraph_auth.${key}`)).toBe(true)
    }
  })

  it('rejects a mailbox that is not an email address', () => {
    const r = validate(withMsgraphEmail((a) => (a['mailbox'] = 'operator-no-domain')))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'connectors.Email.msgraph_auth.mailbox')).toBe(true)
  })

  it('rejects a secret_ref that is not a fly-secret reference', () => {
    // infisical: is the token_ref channel; msgraph custody is a per-seat Fly secret
    for (const bad of ['infisical:/operator/x/y', 'MSGRAPH_CLIENT_SECRET', 'fly-secret:']) {
      const r = validate(withMsgraphEmail((a) => (a['secret_ref'] = bad)))
      expect(r.ok).toBe(false)
      if (r.ok) continue
      expect(r.errors.some((e) => e.path === 'connectors.Email.msgraph_auth.secret_ref')).toBe(true)
    }
  })

  it('rejects a partial msgraph_auth block (fail-closed, never a silent default)', () => {
    const r = validate(withMsgraphEmail((a) => delete a['secret_ref']))
    expect(r.ok).toBe(false)
  })
})

// -----------------------------------------------------------------------------
// Persona send_as: provider-neutral send_identity + agentmail_identity back-compat
// (ADR 0078 §4 / email-channel-seam D5)
// -----------------------------------------------------------------------------

describe('validate — persona send_as normalization (ADR 0078 §4)', () => {
  function withSendAs(sendAs: unknown): Record<string, unknown> {
    const f = validFixture()
    ;(f['personas'] as Record<string, unknown>[])[0]['send_as'] = sendAs
    return f
  }

  it('accepts a provider-neutral send_identity (msgraph) and carries it verbatim', () => {
    const r = validate(
      withSendAs({ send_identity: { provider: 'msgraph', address: 'operator@clientdomain.com' } })
    )
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.personas[0].send_as?.send_identity).toEqual({
      provider: 'msgraph',
      address: 'operator@clientdomain.com',
    })
    // no agentmail mirror for a non-agentmail provider
    expect(r.value.personas[0].send_as?.agentmail_identity).toBeUndefined()
  })

  it('emits an idempotent send_identity-only shape for an agentmail identity', () => {
    const r = validate(
      withSendAs({
        send_identity: { provider: 'agentmail', address: 'ops@firm.agents.smd.services' },
      })
    )
    expect(r.ok).toBe(true)
    if (!r.ok) return
    // output carries ONLY send_identity — the deprecated field is never emitted
    // (the toEqual asserts the exact shape), so re-validating never trips the
    // both-set guard.
    expect(r.value.personas[0].send_as).toEqual({
      send_identity: { provider: 'agentmail', address: 'ops@firm.agents.smd.services' },
    })
  })

  it('normalizes a legacy agentmail_identity into send_identity (back-compat)', () => {
    const r = validate(
      withSendAs({ agentmail_identity: 'marcus@smith-pi-firm.agents.smd.services' })
    )
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.personas[0].send_as).toEqual({
      send_identity: { provider: 'agentmail', address: 'marcus@smith-pi-firm.agents.smd.services' },
    })
  })

  it('re-validating a normalized value is idempotent (no both-set error)', () => {
    const first = validate(
      withSendAs({ agentmail_identity: 'marcus@smith-pi-firm.agents.smd.services' })
    )
    expect(first.ok).toBe(true)
    if (!first.ok) return
    const roundTrip = withSendAs(first.value.personas[0].send_as)
    expect(validate(roundTrip).ok).toBe(true)
  })

  it('rejects authoring both send_identity and the legacy field (ambiguous)', () => {
    const r = validate(
      withSendAs({
        send_identity: { provider: 'agentmail', address: 'a@b.c' },
        agentmail_identity: 'a@b.c',
      })
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('InvalidFormat')
    expect(r.errors.some((e) => e.path === 'personas[0].send_as')).toBe(true)
  })

  it('rejects an unknown send_identity.provider', () => {
    const r = validate(withSendAs({ send_identity: { provider: 'gmail', address: 'a@b.c' } }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('EnumViolation')
    expect(r.errors.some((e) => e.path === 'personas[0].send_as.send_identity.provider')).toBe(true)
  })

  it('rejects a send_identity missing its address', () => {
    const r = validate(withSendAs({ send_identity: { provider: 'msgraph' } }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'personas[0].send_as.send_identity.address')).toBe(true)
  })

  it('rejects a send_as with neither send_identity nor agentmail_identity', () => {
    const r = validate(withSendAs({}))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('MissingField')
  })

  it('rejects an empty agentmail_identity string', () => {
    const r = validate(withSendAs({ agentmail_identity: '' }))
    expect(r.ok).toBe(false)
  })
})

// -----------------------------------------------------------------------------
// send_policy (#2070) — reply-channel send-rate caps
// -----------------------------------------------------------------------------

describe('send_policy', () => {
  function withSendPolicy(block: unknown): Record<string, unknown> {
    const f = validFixture()
    f['send_policy'] = block
    return f
  }

  it('accepts an absent block (platform defaults apply on-box)', () => {
    expect(validate(validFixture()).ok).toBe(true)
  })

  it('accepts the full authored block', () => {
    const r = validate(
      withSendPolicy({
        reply: {
          internal_exempt: true,
          per_sender_max: 3,
          per_sender_window_seconds: 600,
          global_max: 20,
          global_window_seconds: 3600,
          backstop_max: 60,
          backstop_window_seconds: 3600,
        },
        held_release: { enabled: true, ttl_seconds: 86400 },
      })
    )
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.send_policy?.reply?.internal_exempt).toBe(true)
    expect(r.value.send_policy?.reply?.backstop_max).toBe(60)
    expect(r.value.send_policy?.held_release?.enabled).toBe(true)
  })

  it('accepts a partial block (dialogue exemption only)', () => {
    const r = validate(withSendPolicy({ reply: { internal_exempt: true } }))
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.send_policy?.reply?.per_sender_max).toBeNull()
    expect(r.value.send_policy?.held_release).toBeNull()
  })

  it('rejects a non-object block', () => {
    const r = validate(withSendPolicy('nope'))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(codesOf(r.errors)).toContain('TypeMismatch')
  })

  it('rejects unknown keys at every level', () => {
    const r = validate(
      withSendPolicy({
        bogus: 1,
        reply: { mystery: 2 },
        held_release: { nonsense: 3 },
      })
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    const paths = r.errors.map((e) => e.path)
    expect(paths).toContain('send_policy.bogus')
    expect(paths).toContain('send_policy.reply.mystery')
    expect(paths).toContain('send_policy.held_release.nonsense')
  })

  it('rejects a non-boolean internal_exempt', () => {
    const r = validate(withSendPolicy({ reply: { internal_exempt: 1 } }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.some((e) => e.path === 'send_policy.reply.internal_exempt')).toBe(true)
  })

  it('rejects negative and non-integer counts', () => {
    const r = validate(
      withSendPolicy({ reply: { per_sender_max: -1, global_max: 2.5, backstop_max: 'many' } })
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    const paths = r.errors.map((e) => e.path)
    expect(paths).toContain('send_policy.reply.per_sender_max')
    expect(paths).toContain('send_policy.reply.global_max')
    expect(paths).toContain('send_policy.reply.backstop_max')
  })

  it('rejects non-positive windows', () => {
    const r = validate(
      withSendPolicy({ reply: { per_sender_window_seconds: 0, backstop_window_seconds: -3 } })
    )
    expect(r.ok).toBe(false)
    if (r.ok) return
    const paths = r.errors.map((e) => e.path)
    expect(paths).toContain('send_policy.reply.per_sender_window_seconds')
    expect(paths).toContain('send_policy.reply.backstop_window_seconds')
  })

  it('rejects a malformed held_release', () => {
    const r = validate(withSendPolicy({ held_release: { enabled: 'yes', ttl_seconds: 0 } }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    const paths = r.errors.map((e) => e.path)
    expect(paths).toContain('send_policy.held_release.enabled')
    expect(paths).toContain('send_policy.held_release.ttl_seconds')
  })
})

// -----------------------------------------------------------------------------
// personas[].signature — the authored chase-mail signature block
// (outbound-quality track; consumed by the chase skills' rendered sign-off per
// _shared-chase-voice.md "Salutation and signature")
// -----------------------------------------------------------------------------

describe('personas[].signature', () => {
  function withSignature(signature: unknown): Record<string, unknown> {
    const f = validFixture()
    const personas = f['personas'] as Record<string, unknown>[]
    personas[0]['signature'] = signature
    return f
  }

  it('is optional: an unauthored persona carries null (degrades to customer_name)', () => {
    const r = validate(validFixture())
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.value.personas[0].signature).toBeNull()
  })

  it('accepts an authored firm_line + closing', () => {
    const r = validate(withSignature({ firm_line: 'Smith PI Firm', closing: 'Thank you.' }))
    if (!r.ok) throw new Error(JSON.stringify(r.errors))
    expect(r.value.personas[0].signature).toEqual({
      firm_line: 'Smith PI Firm',
      closing: 'Thank you.',
    })
  })

  it('accepts a partial authoring (firm_line only)', () => {
    const r = validate(withSignature({ firm_line: 'Smith PI Firm' }))
    if (!r.ok) throw new Error(JSON.stringify(r.errors))
    expect(r.value.personas[0].signature).toEqual({ firm_line: 'Smith PI Firm', closing: null })
  })

  it('refuses a non-mapping authoring rather than silently dropping it', () => {
    const r = validate(withSignature('Smith PI Firm'))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.map((e) => e.path)).toContain('personas[0].signature')
  })

  it('refuses an unknown key so a typo cannot author nothing', () => {
    const r = validate(withSignature({ firm_name: 'Smith PI Firm' }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.map((e) => e.path)).toContain('personas[0].signature.firm_name')
  })

  it('refuses a floor-trigger word in firm_line, naming the word (finding 3)', () => {
    // The block renders verbatim into every chase body; the ADR 0031 floor
    // would hold each one as a draft. Refused where authored, correctively.
    const r = validate(withSignature({ firm_line: 'Smith, Attorneys at Law' }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    const error = r.errors.find((e) => e.path === 'personas[0].signature.firm_line')
    expect(error).toBeDefined()
    expect(error!.message).toContain('"attorney"')
    expect(error!.message).toContain('content-floor')
    expect(error!.message).toContain('_shared-chase-voice.md')
  })

  it('refuses a floor-trigger word in closing too', () => {
    const r = validate(withSignature({ closing: 'Please sign and return promptly.' }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.map((e) => e.path)).toContain('personas[0].signature.closing')
  })

  it('word boundaries hold: a clean firm_line with an embedded substring passes', () => {
    // "Signal" is not "sign"; the gate must refuse words, not substrings.
    const r = validate(withSignature({ firm_line: 'Signal Hill LLP' }))
    if (!r.ok) throw new Error(JSON.stringify(r.errors))
    expect(r.value.personas[0].signature?.firm_line).toBe('Signal Hill LLP')
  })

  it('refuses non-string field values', () => {
    const r = validate(withSignature({ firm_line: 42 }))
    expect(r.ok).toBe(false)
    if (r.ok) return
    expect(r.errors.map((e) => e.path)).toContain('personas[0].signature.firm_line')
  })
})

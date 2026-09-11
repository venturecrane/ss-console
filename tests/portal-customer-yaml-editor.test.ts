/**
 * Tests for the customer.yaml editor resolver + validation library
 * (src/lib/portal/operator/customer-yaml-editor.ts).
 *
 * Coverage:
 *   - locked-field policy: exact paths, wildcard children, wildcard
 *     suffix matching
 *   - projection: CustomerYaml → ResolvedEditableConfig is faithful;
 *     token_ref values move to the locked surface, never editable
 *   - merger: locked fields pulled from `current`, never from input;
 *     personas length truncated to 1 (ADR 0011); skill list preserves
 *     Captain-managed fields (cost_estimate, scope, version)
 *   - validation: locked-field violations surface BannedFieldName
 *     before the structural pass runs; legitimate changes pass through
 *   - diff: section-level change paths; identical snapshots produce
 *     empty list
 *   - hash: deterministic; differs when content differs; same when
 *     identical
 *   - audit metadata: shape matches the issue contract (changed_fields,
 *     before_hash, after_hash, actor_id)
 */

import { describe, it, expect } from 'vitest'
import {
  applyEditableChanges,
  computeChangedFields,
  hashEditableConfig,
  projectEditableConfig,
  validateEditableChanges,
  type EditableCustomerConfig,
} from '../src/lib/portal/operator/customer-yaml-editor'
import { buildAuditMetadata } from '../src/lib/portal/operator/customer-yaml-audit'
import { validate, type CustomerYaml } from '../src/lib/operator/customer-yaml'

// -----------------------------------------------------------------------------
// Fixture builder — mirrors the validator test fixture
// -----------------------------------------------------------------------------

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
    machine: { size: 'performance-1x', memory_mb: 1024 },
    users: [{ email: 'partner@firm.com', role: 'principal', full_name: 'Jane Smith' }],
    personas: [
      {
        slug: 'marcus',
        status: 'active',
        name: 'Marcus',
        title: 'AI Associate',
        signature_html: '<p>Marcus | AI Associate</p>',
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
            enabled: true,
            initiation: { manual: true, scheduled: false, webhook: false },
            cost_estimate: {
              tokens_in_per_run: 2000,
              tokens_out_per_run: 800,
              tool_calls_per_run: 4,
              runs_per_day_typical: 30,
            },
            scope: ['high-priority'],
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
      PracticeManagement: {
        adapter: 'filevine',
        backend: 'build:filevine-mcp',
        scopes: ['matters:read', 'contacts:read'],
      },
    },
    scope: {
      email_folders_visible: ['Inbox', 'Clients'],
      email_folders_blind: ['Strategy'],
      email_keyword_blocks: ['PRIVILEGED'],
      domain_blocks: [],
    },
    escalation: {
      red_flag_recipients: ['partner@firm.com'],
      failure_recipients: ['partner@firm.com'],
    },
    memory: {
      d1_namespace: 'smith-pi-firm',
      r2_vault_path: 'vaults/smith-pi-firm/',
      vectorize_index: 'hermes-smith-pi-firm-vault',
    },
  }
}

function validYaml(): CustomerYaml {
  const result = validate(validFixture())
  if (!result.ok) {
    throw new Error(`fixture should validate: ${JSON.stringify(result.errors)}`)
  }
  return result.value
}

// -----------------------------------------------------------------------------
// Projection
// -----------------------------------------------------------------------------

describe('projectEditableConfig', () => {
  it('moves connector token_ref values to the locked surface', () => {
    const resolved = projectEditableConfig(validYaml())
    expect(resolved.editable.connectors.Email).not.toHaveProperty('token_ref')
    expect(resolved.locked.connector_token_refs.Email).toBe(
      'infisical:/operator/smith-pi-firm/email/refresh'
    )
  })

  it('preserves persona editable fields', () => {
    const resolved = projectEditableConfig(validYaml())
    expect(resolved.editable.personas).toHaveLength(1)
    const persona = resolved.editable.personas[0]
    expect(persona.name).toBe('Marcus')
    expect(persona.title).toBe('AI Associate')
    expect(persona.tone).toEqual(['warm-but-professional', 'concise'])
    expect(persona.send_as?.agentmail_identity).toBe('marcus@smith-pi-firm.agents.smd.services')
  })

  it('exposes locked identity fields for display', () => {
    const resolved = projectEditableConfig(validYaml())
    expect(resolved.locked.customer_id).toBe('smith-pi-firm')
    expect(resolved.locked.schema_version).toBe(1)
    expect(resolved.locked.memory.d1_namespace).toBe('smith-pi-firm')
  })

  it('does NOT surface signature_html in editable persona shape', () => {
    const resolved = projectEditableConfig(validYaml())
    expect(resolved.editable.personas[0]).not.toHaveProperty('signature_html')
  })
})

// -----------------------------------------------------------------------------
// Merger — locked fields always pulled from current
// -----------------------------------------------------------------------------

describe('applyEditableChanges', () => {
  it('preserves locked identity fields even when input would change them', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    // Simulate a malformed POST trying to inject token_ref into editable.
    const tampered: EditableCustomerConfig = {
      ...editable,
      connectors: {
        ...editable.connectors,
        Email: {
          ...editable.connectors.Email,
          // @ts-expect-error — testing the merger's lock discipline
          token_ref: 'infisical:/attacker/path',
        },
      },
    }
    const merged = applyEditableChanges(current, tampered)
    expect(merged.connectors.Email?.token_ref).toBe(
      'infisical:/operator/smith-pi-firm/email/refresh'
    )
  })

  it('truncates personas to length 1 (ADR 0011 v1 invariant)', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const tampered: EditableCustomerConfig = {
      ...editable,
      personas: [editable.personas[0], editable.personas[0]],
    }
    const merged = applyEditableChanges(current, tampered)
    expect(merged.personas).toHaveLength(1)
  })

  it('preserves Captain-managed skill fields (cost_estimate, scope)', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    // Modify the editable skill (toggle enabled flag).
    const updated: EditableCustomerConfig = {
      ...editable,
      personas: [
        {
          ...editable.personas[0],
          skills: [{ ...editable.personas[0].skills[0], enabled: false }],
        },
      ],
    }
    const merged = applyEditableChanges(current, updated)
    expect(merged.personas[0].skills[0].enabled).toBe(false)
    expect(merged.personas[0].skills[0].cost_estimate).not.toBeNull()
    expect(merged.personas[0].skills[0].scope).toEqual(['high-priority'])
  })

  it('applies editable escalation changes', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const updated: EditableCustomerConfig = {
      ...editable,
      escalation: {
        red_flag_recipients: ['partner@firm.com', 'owner@firm.com'],
        failure_recipients: ['partner@firm.com'],
        acknowledgement_window_minutes: 30,
      },
    }
    const merged = applyEditableChanges(current, updated)
    expect(merged.escalation.red_flag_recipients).toEqual(['partner@firm.com', 'owner@firm.com'])
    expect(merged.escalation.acknowledgement_window_minutes).toBe(30)
  })

  it('preserves persona signature_html (Captain-managed)', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const merged = applyEditableChanges(current, editable)
    expect(merged.personas[0].signature_html).toBe('<p>Marcus | AI Associate</p>')
  })
})

// -----------------------------------------------------------------------------
// Validation
// -----------------------------------------------------------------------------

describe('validateEditableChanges', () => {
  it('passes on a clean round-trip', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const result = validateEditableChanges(current, editable)
    expect(result.ok).toBe(true)
  })

  it('rejects personas longer than 1 with BannedFieldName', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const tampered: EditableCustomerConfig = {
      ...editable,
      personas: [editable.personas[0], editable.personas[0]],
    }
    const result = validateEditableChanges(current, tampered)
    if (result.ok) throw new Error('expected validation failure')
    expect(
      result.errors.some((e) => e.code === 'BannedFieldName' && e.path === 'personas.length')
    ).toBe(true)
  })

  it('rejects a connector with smuggled token_ref', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const tampered: EditableCustomerConfig = {
      ...editable,
      connectors: {
        ...editable.connectors,
        Email: {
          ...editable.connectors.Email,
          // @ts-expect-error — testing lock-check discipline
          token_ref: 'infisical:/attacker/path',
        },
      },
    }
    const result = validateEditableChanges(current, tampered)
    if (result.ok) throw new Error('expected validation failure')
    expect(
      result.errors.some(
        (e) => e.code === 'BannedFieldName' && e.path === 'connectors.Email.token_ref'
      )
    ).toBe(true)
  })

  it('surfaces structural errors from the merged YAML', () => {
    const current = validYaml()
    const editable = projectEditableConfig(current).editable
    const tampered: EditableCustomerConfig = {
      ...editable,
      escalation: {
        // Empty rosters violate the schema's "at least one" requirement.
        red_flag_recipients: [],
        failure_recipients: [],
        acknowledgement_window_minutes: null,
      },
    }
    const result = validateEditableChanges(current, tampered)
    if (result.ok) throw new Error('expected validation failure')
    expect(result.errors.length).toBeGreaterThan(0)
  })
})

// -----------------------------------------------------------------------------
// Diff
// -----------------------------------------------------------------------------

describe('computeChangedFields', () => {
  it('returns empty list for identical snapshots', () => {
    const editable = projectEditableConfig(validYaml()).editable
    expect(computeChangedFields(editable, editable)).toEqual([])
  })

  it('detects escalation roster changes', () => {
    const before = projectEditableConfig(validYaml()).editable
    const after: EditableCustomerConfig = {
      ...before,
      escalation: {
        ...before.escalation,
        red_flag_recipients: ['someone@firm.com'],
      },
    }
    const changed = computeChangedFields(before, after)
    expect(changed).toContain('escalation.red_flag_recipients')
  })

  it('detects persona shifts', () => {
    const before = projectEditableConfig(validYaml()).editable
    const after: EditableCustomerConfig = {
      ...before,
      personas: [{ ...before.personas[0], name: 'Marcus II' }],
    }
    expect(computeChangedFields(before, after)).toContain('personas[0]')
  })

  it('detects scope envelope changes', () => {
    const before = projectEditableConfig(validYaml()).editable
    const after: EditableCustomerConfig = {
      ...before,
      scope: { ...before.scope, email_folders_visible: ['Inbox'] },
    }
    expect(computeChangedFields(before, after)).toContain('scope.email_folders_visible')
  })
})

// -----------------------------------------------------------------------------
// Hash
// -----------------------------------------------------------------------------

describe('hashEditableConfig', () => {
  it('returns the same hash for identical snapshots', () => {
    const editable = projectEditableConfig(validYaml()).editable
    expect(hashEditableConfig(editable)).toBe(hashEditableConfig(editable))
  })

  it('returns different hashes for different snapshots', () => {
    const a = projectEditableConfig(validYaml()).editable
    const b: EditableCustomerConfig = {
      ...a,
      escalation: {
        ...a.escalation,
        red_flag_recipients: ['different@firm.com'],
      },
    }
    expect(hashEditableConfig(a)).not.toBe(hashEditableConfig(b))
  })

  it('returns an 8-character hex string', () => {
    const editable = projectEditableConfig(validYaml()).editable
    const hash = hashEditableConfig(editable)
    expect(hash).toMatch(/^[a-f0-9]{8}$/)
  })
})

// -----------------------------------------------------------------------------
// Audit metadata
// -----------------------------------------------------------------------------

describe('buildAuditMetadata', () => {
  it('returns the audit shape with changed_fields, hashes, actor_id', () => {
    const before = projectEditableConfig(validYaml()).editable
    const after: EditableCustomerConfig = {
      ...before,
      escalation: {
        ...before.escalation,
        red_flag_recipients: ['updated@firm.com'],
      },
    }
    const meta = buildAuditMetadata(before, after, 'usr_principal_123')
    expect(meta.actor_id).toBe('usr_principal_123')
    expect(meta.changed_fields).toContain('escalation.red_flag_recipients')
    expect(meta.before_hash).toMatch(/^[a-f0-9]{8}$/)
    expect(meta.after_hash).toMatch(/^[a-f0-9]{8}$/)
    expect(meta.before_hash).not.toBe(meta.after_hash)
  })

  it('returns empty changed_fields for a no-op save', () => {
    const editable = projectEditableConfig(validYaml()).editable
    const meta = buildAuditMetadata(editable, editable, 'usr_x')
    expect(meta.changed_fields).toEqual([])
    expect(meta.before_hash).toBe(meta.after_hash)
  })
})

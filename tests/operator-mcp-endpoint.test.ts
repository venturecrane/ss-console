import { beforeEach, describe, expect, it } from 'vitest'
import {
  createTestD1,
  discoverNumericMigrations,
  runMigrations,
} from '@venturecrane/crane-test-harness'
import type { D1Database } from '@cloudflare/workers-types'
import { createLocalJWKSet, exportJWK, generateKeyPair, jwtVerify, SignJWT } from 'jose'
import { resolve } from 'node:path'
import { ORG_ID } from '../src/lib/constants'
import {
  buildMcpMetadataPath,
  buildMcpResourcePath,
  loadMcpCustomer,
  parseMcpMetadataResource,
  parseMcpResourcePath,
  type ResolvedMcpCustomer,
} from '../src/lib/operator/mcp/customer-resolution'
import { dispatchMcpRequest, parseMcpBody } from '../src/lib/operator/mcp/mcp-handler'
import { handleMcpPost } from '../src/lib/operator/mcp/mcp-route'
import { buildProtectedResourceMetadata } from '../src/lib/operator/mcp/oauth-metadata'
import {
  extractBearerToken,
  validateMcpToken,
  type McpTokenVerifier,
} from '../src/lib/operator/mcp/token-validation'
import type { McpToolContext } from '../src/lib/operator/mcp/tools'
import type { RuntimeReadResult } from '../src/lib/operator/runtime-read'
import { POST as legacyMcpPost } from '../src/pages/api/mcp'

const migrationsDir = resolve(process.cwd(), 'migrations')
const RESOURCE_URI = 'https://smd.services/api/operator/smd/mcp'
const ISSUER = 'https://clerk.smd.services'
const ENTITY_ID = 'entity-mcp-test'
const LOCAL_USER_ID = 'user-mcp-test'
const CLERK_USER_ID = 'user_clerk_123'

function migrationBasename(file: string): string {
  return file.split('/').pop() ?? file
}

async function freshDb(): Promise<D1Database> {
  const db = createTestD1()
  await runMigrations(db, { files: discoverNumericMigrations(migrationsDir) })
  return db
}

function customerFixture(over: Partial<ResolvedMcpCustomer> = {}): ResolvedMcpCustomer {
  return {
    entityId: ENTITY_ID,
    customerId: 'smd',
    clerkOrgId: null,
    connector: {
      enabled: true,
      data_posture: 'open',
      policy: 'allowlist',
      allowed_domains: [],
      default_profile: null,
      ttl_days: 30,
      access: [{ email: 'pilot@example.com', profile: 'crane' }],
    },
    clerk: {
      issuer: ISSUER,
      resourceUri: RESOURCE_URI,
      clientId: null,
      clerkAppId: null,
    },
    principals: [
      {
        localUserId: LOCAL_USER_ID,
        clerkUserId: CLERK_USER_ID,
        email: 'pilot@example.com',
        profile: 'crane',
      },
    ],
    ...over,
  }
}

function claims(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sub: CLERK_USER_ID,
    iss: ISSUER,
    aud: RESOURCE_URI,
    email: 'pilot@example.com',
    ...over,
  }
}

function claimsVerifier(value: unknown): McpTokenVerifier {
  return async () => value
}

async function signedVerifier(
  payload: Record<string, unknown>
): Promise<{ token: string; verifier: McpTokenVerifier }> {
  const { privateKey, publicKey } = await generateKeyPair('RS256')
  const jwk = await exportJWK(publicKey)
  const token = await new SignJWT(payload)
    .setProtectedHeader({ alg: 'RS256', kid: 'test-key' })
    .setIssuedAt()
    .setExpirationTime('5m')
    .sign(privateKey)
  const keySet = createLocalJWKSet({ keys: [{ ...jwk, kid: 'test-key', alg: 'RS256' }] })
  return {
    token,
    verifier: async (candidate) => {
      const result = await jwtVerify(candidate, keySet, { algorithms: ['RS256'] })
      return result.payload
    },
  }
}

async function seedCustomer(db: D1Database, clerkOrgId: string | null = null): Promise<void> {
  await db
    .prepare('INSERT INTO entities (id, org_id, name, slug, clerk_org_id) VALUES (?, ?, ?, ?, ?)')
    .bind(ENTITY_ID, ORG_ID, 'MCP Test', 'smd', clerkOrgId)
    .run()
  await db
    .prepare(
      'INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id) ' +
        'VALUES (?, ?, ?, ?, ?, ?, ?)'
    )
    .bind(
      LOCAL_USER_ID,
      ORG_ID,
      'pilot@example.com',
      'Pilot User',
      'client',
      ENTITY_ID,
      CLERK_USER_ID
    )
    .run()
  await db
    .prepare(
      'INSERT INTO customer_configs ' +
        '(entity_id, org_id, customer_slug, schema_version, personas_json, ' +
        'mcp_connector_json, git_sha, synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
    )
    .bind(
      ENTITY_ID,
      ORG_ID,
      'smd',
      '1',
      '[]',
      JSON.stringify({
        enabled: true,
        data_posture: 'open',
        access: [{ email: 'pilot@example.com', profile: 'crane' }],
      }),
      'test-sha',
      '2026-06-15T00:00:00Z'
    )
    .run()
  await db
    .prepare(
      'INSERT INTO mcp_clerk_bindings ' +
        '(entity_id, customer_slug, issuer, resource_uri) VALUES (?, ?, ?, ?)'
    )
    .bind(ENTITY_ID, 'smd', ISSUER, RESOURCE_URI)
    .run()
}

describe('customer-specific MCP resources', () => {
  it('builds and parses only canonical customer resource paths', () => {
    expect(buildMcpResourcePath('smd')).toBe('/api/operator/smd/mcp')
    expect(buildMcpMetadataPath('smd')).toBe(
      '/.well-known/oauth-protected-resource/api/operator/smd/mcp'
    )
    expect(parseMcpResourcePath('/api/operator/smd/mcp')).toBe('smd')
    expect(parseMcpResourcePath('/api/mcp')).toBeNull()
    expect(parseMcpMetadataResource('api/operator/smd/mcp')).toBe('smd')
    expect(parseMcpMetadataResource('api/mcp')).toBeNull()
  })

  it('publishes one authorization server for the exact customer resource', () => {
    expect(buildProtectedResourceMetadata(RESOURCE_URI, [ISSUER])).toEqual({
      resource: RESOURCE_URI,
      authorization_servers: [ISSUER],
      scopes_supported: ['openid', 'profile', 'email'],
      bearer_methods_supported: ['header'],
    })
  })

  it('requests Clerk organization claims for organization-bound customers', () => {
    expect(buildProtectedResourceMetadata(RESOURCE_URI, [ISSUER], true)).toMatchObject({
      scopes_supported: ['openid', 'profile', 'email', 'user:org:read'],
    })
  })

  it('retires the shared legacy endpoint', async () => {
    const response = await legacyMcpPost({} as never)
    expect(response.status).toBe(410)
  })
})

describe('extractBearerToken', () => {
  it('accepts a bearer token and rejects malformed authorization headers', () => {
    expect(extractBearerToken('Bearer abc.def.ghi')).toBe('abc.def.ghi')
    expect(extractBearerToken('bearer xyz')).toBe('xyz')
    expect(extractBearerToken(null)).toBeNull()
    expect(extractBearerToken('Basic abc')).toBeNull()
    expect(extractBearerToken('Bearer ')).toBeNull()
  })
})

describe('validateMcpToken', () => {
  it('accepts a locally signed token for the exact resource and stable subject', async () => {
    const signed = await signedVerifier(claims())
    const result = await validateMcpToken(signed.token, customerFixture(), signed.verifier)
    expect(result).toMatchObject({
      ok: true,
      subject: CLERK_USER_ID,
      tokenAudience: [RESOURCE_URI],
      localUserId: LOCAL_USER_ID,
      profile: 'crane',
    })
  })

  it.each([
    ['wrong_issuer', claims({ iss: 'https://other.example' })],
    ['wrong_audience', claims({ aud: 'https://smd.services/api/operator/other/mcp' })],
    ['claims_invalid', claims({ sub: undefined })],
    ['identity_not_authored', claims({ sub: 'user_unknown' })],
  ])('rejects %s', async (reason, payload) => {
    const result = await validateMcpToken('token', customerFixture(), claimsVerifier(payload))
    expect(result).toMatchObject({ ok: false, reason })
  })

  it('accepts Clerk DCR tokens without an audience after issuer and subject checks', async () => {
    const result = await validateMcpToken(
      'token',
      customerFixture(),
      claimsVerifier(claims({ aud: undefined }))
    )
    expect(result).toMatchObject({
      ok: true,
      subject: CLERK_USER_ID,
      tokenAudience: [],
    })
  })

  it('returns the verified audience when the resource audience is wrong', async () => {
    const result = await validateMcpToken(
      'token',
      customerFixture(),
      claimsVerifier(claims({ aud: ['dynamic-client-id', 'secondary-audience'] }))
    )
    expect(result).toMatchObject({
      ok: false,
      reason: 'wrong_audience',
      tokenAudience: ['dynamic-client-id', 'secondary-audience'],
    })
  })

  it('requires the exact Clerk organization when the entity is organization-bound', async () => {
    const customer = customerFixture({ clerkOrgId: 'org_expected' })
    const missing = await validateMcpToken('token', customer, claimsVerifier(claims()))
    const wrong = await validateMcpToken(
      'token',
      customer,
      claimsVerifier(claims({ org_id: 'org_wrong' }))
    )
    const valid = await validateMcpToken(
      'token',
      customer,
      claimsVerifier(claims({ org_id: 'org_expected' }))
    )
    expect(missing).toMatchObject({ ok: false, reason: 'organization_mismatch' })
    expect(wrong).toMatchObject({ ok: false, reason: 'organization_mismatch' })
    expect(valid.ok).toBe(true)
  })

  it('fails closed on missing, invalid, disabled, and unprovisioned identities', async () => {
    expect(await validateMcpToken(null, customerFixture())).toMatchObject({
      ok: false,
      reason: 'missing_token',
    })
    expect(
      await validateMcpToken('token', customerFixture(), async () => {
        throw new Error('bad signature')
      })
    ).toMatchObject({ ok: false, reason: 'signature_invalid' })
    expect(await validateMcpToken('token', customerFixture())).toMatchObject({
      ok: false,
      reason: 'token_not_jwt',
    })
    expect(
      await validateMcpToken('one.two.three', customerFixture(), async () => {
        throw new Error('bad signature')
      })
    ).toMatchObject({ ok: false, reason: 'signature_invalid' })
    expect(
      await validateMcpToken(
        'token',
        customerFixture({
          connector: {
            enabled: false,
            data_posture: 'open',
            policy: 'allowlist',
            allowed_domains: [],
            default_profile: null,
            ttl_days: 30,
            access: [],
          },
        }),
        claimsVerifier(claims())
      )
    ).toMatchObject({ ok: false, reason: 'connector_disabled' })
    expect(
      await validateMcpToken('token', customerFixture({ principals: [] }), claimsVerifier(claims()))
    ).toMatchObject({ ok: false, reason: 'identity_not_authored' })
  })
})

describe('loadMcpCustomer and migration 0072', () => {
  let db: D1Database

  beforeEach(async () => {
    db = await freshDb()
    await seedCustomer(db)
  })

  it('loads the strict resource binding and resolves email authoring to a Clerk subject', async () => {
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer).toMatchObject({
      entityId: ENTITY_ID,
      customerId: 'smd',
      clerkOrgId: null,
      clerk: { issuer: ISSUER, resourceUri: RESOURCE_URI },
      principals: [
        {
          localUserId: LOCAL_USER_ID,
          clerkUserId: CLERK_USER_ID,
          email: 'pilot@example.com',
          profile: 'crane',
        },
      ],
    })
  })

  it('fails closed when an authored user has no Clerk subject', async () => {
    await db.prepare('UPDATE users SET clerk_user_id = NULL WHERE id = ?').bind(LOCAL_USER_ID).run()
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals).toEqual([])
  })

  it('uses an explicit customer-scoped Clerk subject over the local user identity', async () => {
    await db
      .prepare('UPDATE customer_configs SET mcp_connector_json = ? WHERE entity_id = ?')
      .bind(
        JSON.stringify({
          enabled: true,
          data_posture: 'open',
          access: [
            {
              email: 'pilot@example.com',
              profile: 'crane',
              clerk_subject: 'user_externalaccount',
            },
          ],
        }),
        ENTITY_ID
      )
      .run()
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals[0]).toMatchObject({
      localUserId: LOCAL_USER_ID,
      clerkUserId: 'user_externalaccount',
      email: 'pilot@example.com',
    })
  })

  it('resolves multiple approved Clerk subjects to one local customer user', async () => {
    await db
      .prepare('UPDATE customer_configs SET mcp_connector_json = ? WHERE entity_id = ?')
      .bind(
        JSON.stringify({
          enabled: true,
          data_posture: 'open',
          access: [
            {
              email: 'pilot@example.com',
              profile: 'crane',
              clerk_subjects: ['user_primary', 'user_secondary'],
            },
          ],
        }),
        ENTITY_ID
      )
      .run()
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals.map((principal) => principal.clerkUserId)).toEqual([
      'user_primary',
      'user_secondary',
    ])
    expect(customer?.principals.every((principal) => principal.localUserId === LOCAL_USER_ID)).toBe(
      true
    )
  })

  // ADR 0057 — dynamic access grants (mcp_issued_grants) merged into principals.
  const insertGrant = (
    db: D1Database,
    grant: {
      clerk_user_id: string
      email: string
      profile: string
      expires_at: string
      revoked_at?: string | null
    }
  ) =>
    db
      .prepare(
        'INSERT INTO mcp_issued_grants ' +
          '(customer_slug, clerk_user_id, email, profile, expires_at, revoked_at) ' +
          'VALUES (?, ?, ?, ?, ?, ?)'
      )
      .bind(
        'smd',
        grant.clerk_user_id,
        grant.email,
        grant.profile,
        grant.expires_at,
        grant.revoked_at ?? null
      )
      .run()

  it('authorizes a live grant for a subject not in the authored access list', async () => {
    await insertGrant(db, {
      clerk_user_id: 'user_grantonly',
      email: 'grantee@example.com',
      profile: 'operator',
      expires_at: '2999-01-01T00:00:00.000Z',
    })
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals).toContainEqual({
      localUserId: 'user_grantonly',
      clerkUserId: 'user_grantonly',
      email: 'grantee@example.com',
      profile: 'operator',
    })
  })

  it('resolves a granted subject that has a users row to that users.id', async () => {
    await db
      .prepare(
        'INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id) ' +
          'VALUES (?, ?, ?, ?, ?, ?, ?)'
      )
      .bind('user-granted-portal', ORG_ID, 'gp@example.com', 'GP', 'client', ENTITY_ID, 'user_gp')
      .run()
    await insertGrant(db, {
      clerk_user_id: 'user_gp',
      email: 'gp@example.com',
      profile: 'operator',
      expires_at: '2999-01-01T00:00:00.000Z',
    })
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals).toContainEqual({
      localUserId: 'user-granted-portal',
      clerkUserId: 'user_gp',
      email: 'gp@example.com',
      profile: 'operator',
    })
  })

  it('does not authorize an expired grant', async () => {
    await insertGrant(db, {
      clerk_user_id: 'user_expired',
      email: 'expired@example.com',
      profile: 'operator',
      expires_at: '2000-01-01T00:00:00.000Z',
    })
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals.map((p) => p.clerkUserId)).not.toContain('user_expired')
  })

  it('does not authorize a revoked grant', async () => {
    await insertGrant(db, {
      clerk_user_id: 'user_revoked',
      email: 'revoked@example.com',
      profile: 'operator',
      expires_at: '2999-01-01T00:00:00.000Z',
      revoked_at: '2026-01-01T00:00:00.000Z',
    })
    const customer = await loadMcpCustomer(db, 'smd')
    expect(customer?.principals.map((p) => p.clerkUserId)).not.toContain('user_revoked')
  })

  it('lets an authored principal win over a grant for the same Clerk subject', async () => {
    await insertGrant(db, {
      clerk_user_id: CLERK_USER_ID, // already authored as pilot@example.com / crane
      email: 'someone-else@example.com',
      profile: 'operator',
      expires_at: '2999-01-01T00:00:00.000Z',
    })
    const customer = await loadMcpCustomer(db, 'smd')
    const matches = customer?.principals.filter((p) => p.clerkUserId === CLERK_USER_ID)
    expect(matches).toHaveLength(1)
    expect(matches?.[0]).toMatchObject({
      localUserId: LOCAL_USER_ID,
      email: 'pilot@example.com',
      profile: 'crane',
    })
  })

  it('returns null for unknown or malformed customer slugs', async () => {
    expect(await loadMcpCustomer(db, 'unknown')).toBeNull()
    expect(await loadMcpCustomer(db, '../smd')).toBeNull()
  })

  it('backfills deployed bindings and rejects future bindings without a resource URI', async () => {
    const isolated = createTestD1()
    const migrations = discoverNumericMigrations(migrationsDir)
    await runMigrations(isolated, {
      files: migrations.filter((file) => {
        const name = migrationBasename(file)
        return !name.startsWith('0072_') && !name.startsWith('0073_')
      }),
    })
    await isolated
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind('entity-existing', ORG_ID, 'Existing Customer', 'existing')
      .run()
    await isolated
      .prepare('INSERT INTO mcp_clerk_bindings (entity_id, customer_slug, issuer) VALUES (?, ?, ?)')
      .bind('entity-existing', 'existing', ISSUER)
      .run()

    await runMigrations(isolated, {
      files: migrations.filter((file) => {
        const name = migrationBasename(file)
        return name.startsWith('0072_') || name.startsWith('0073_')
      }),
    })

    const backfilled = await isolated
      .prepare('SELECT resource_uri FROM mcp_clerk_bindings WHERE entity_id = ?')
      .bind('entity-existing')
      .first<{ resource_uri: string }>()
    expect(backfilled?.resource_uri).toBe('https://smd.services/api/operator/existing/mcp')

    await isolated
      .prepare('INSERT INTO entities (id, org_id, name, slug) VALUES (?, ?, ?, ?)')
      .bind('entity-new', ORG_ID, 'New Customer', 'new')
      .run()
    await expect(
      isolated
        .prepare(
          'INSERT INTO mcp_clerk_bindings (entity_id, customer_slug, issuer) VALUES (?, ?, ?)'
        )
        .bind('entity-new', 'new', ISSUER)
        .run()
    ).rejects.toThrow('resource_uri is required')
  })
})

describe('MCP route authorization and audit', () => {
  let db: D1Database
  let customer: ResolvedMcpCustomer

  beforeEach(async () => {
    db = await freshDb()
    await seedCustomer(db)
    const loaded = await loadMcpCustomer(db, 'smd')
    if (!loaded) throw new Error('test customer did not load')
    customer = loaded
  })

  it('denies a wrong-resource token, audits it, and never invokes runtime reads', async () => {
    let reads = 0
    const response = await handleMcpPost(
      new Request(RESOURCE_URI, {
        method: 'POST',
        headers: { Authorization: 'Bearer token' },
        body: '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"operator_status"}}',
      }),
      new URL(RESOURCE_URI),
      {
        db,
        customer,
        verifier: claimsVerifier(claims({ aud: 'https://wrong.example/mcp' })),
        readRuntime: () => {
          reads += 1
          return Promise.resolve({ ok: false, kind: 'audit_log', reason: 'unreachable' })
        },
      }
    )
    expect(response.status).toBe(401)
    expect(reads).toBe(0)
    const audit = await db
      .prepare('SELECT decision, reason, token_audience, tool FROM operator_mcp_audit')
      .first<{
        decision: string
        reason: string
        token_audience: string | null
        tool: string | null
      }>()
    expect(audit).toEqual({
      decision: 'deny',
      reason: 'wrong_audience',
      token_audience: '["https://wrong.example/mcp"]',
      tool: null,
    })
  })

  it('serves an authenticated tool call and records auth plus tool audit rows', async () => {
    const response = await handleMcpPost(
      new Request(RESOURCE_URI, {
        method: 'POST',
        headers: { Authorization: 'Bearer token' },
        body: '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"operator_status"}}',
      }),
      new URL(RESOURCE_URI),
      {
        db,
        customer,
        verifier: claimsVerifier(claims()),
        readRuntime: (): Promise<RuntimeReadResult> =>
          Promise.resolve({ ok: true, kind: 'audit_log', data: { entries: [], cursor: null } }),
      }
    )
    expect(response.status).toBe(200)
    const rows = await db
      .prepare(
        'SELECT event_type, decision, clerk_subject, token_audience, local_user_id, profile, tool ' +
          'FROM operator_mcp_audit ORDER BY id'
      )
      .all<Record<string, unknown>>()
    expect(rows.results).toEqual([
      {
        event_type: 'auth',
        decision: 'allow',
        clerk_subject: CLERK_USER_ID,
        token_audience: `["${RESOURCE_URI}"]`,
        local_user_id: LOCAL_USER_ID,
        profile: 'crane',
        tool: null,
      },
      {
        event_type: 'tool_call',
        decision: 'allow',
        clerk_subject: CLERK_USER_ID,
        token_audience: `["${RESOURCE_URI}"]`,
        local_user_id: LOCAL_USER_ID,
        profile: 'crane',
        tool: 'operator_status',
      },
    ])
  })

  // --- Open-by-domain JIT (slice 2e) ---
  const STATUS_BODY =
    '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"operator_status"}}'
  const okRead = (): Promise<RuntimeReadResult> =>
    Promise.resolve({ ok: true, kind: 'audit_log', data: { entries: [], cursor: null } })
  const openCustomer = (): ResolvedMcpCustomer => ({
    ...customer,
    connector: {
      ...customer.connector,
      policy: 'open',
      allowed_domains: ['firm.com'],
      default_profile: 'crane',
      ttl_days: 7,
    },
    principals: [], // the caller is a not-yet-authored newcomer
  })
  const postAs = (cust: ResolvedMcpCustomer, claimsOver: Record<string, unknown>) =>
    handleMcpPost(
      new Request(RESOURCE_URI, {
        method: 'POST',
        headers: { Authorization: 'Bearer t' },
        body: STATUS_BODY,
      }),
      new URL(RESOURCE_URI),
      { db, customer: cust, verifier: claimsVerifier(claims(claimsOver)), readRuntime: okRead }
    )
  const grantFor = (clerkUserId: string) =>
    db
      .prepare('SELECT clerk_user_id, profile FROM mcp_issued_grants WHERE clerk_user_id = ?')
      .bind(clerkUserId)
      .first()

  it('open policy: JIT-grants a verified firm-domain newcomer and serves the call', async () => {
    const res = await postAs(openCustomer(), {
      sub: 'user_new',
      email: 'new@firm.com',
      email_verified: true,
    })
    expect(res.status).toBe(200)
    expect(await grantFor('user_new')).toMatchObject({
      clerk_user_id: 'user_new',
      profile: 'crane',
    })
  })

  it('open policy: a JIT grant for a portal user of the firm is audited under their users.id', async () => {
    // A firm employee who is ALSO a portal user (has a users row for this
    // entity) connects over MCP before being authored. The grant is minted
    // under their Clerk subject; the audit actor is their local users.id, the
    // same id the grant read-back path resolves on the next request.
    await db
      .prepare(
        'INSERT INTO users (id, org_id, email, name, role, entity_id, clerk_user_id) ' +
          'VALUES (?, ?, ?, ?, ?, ?, ?)'
      )
      .bind('user-portal-two', ORG_ID, 'two@firm.com', 'Two', 'client', ENTITY_ID, 'user_two')
      .run()
    const res = await postAs(openCustomer(), {
      sub: 'user_two',
      email: 'two@firm.com',
      email_verified: true,
    })
    expect(res.status).toBe(200)
    const audit = await db
      .prepare(
        "SELECT local_user_id FROM operator_mcp_audit WHERE clerk_subject = ? AND event_type = 'auth' AND decision = 'allow'"
      )
      .bind('user_two')
      .first<{ local_user_id: string }>()
    expect(audit?.local_user_id).toBe('user-portal-two')
    // And the read-back path agrees on the very next request.
    const reloaded = await loadMcpCustomer(db, 'smd')
    expect(reloaded?.principals).toContainEqual(
      expect.objectContaining({ clerkUserId: 'user_two', localUserId: 'user-portal-two' })
    )
  })

  it('open policy: a JIT grant for a grant-only newcomer is audited under the Clerk subject', async () => {
    const res = await postAs(openCustomer(), {
      sub: 'user_new3',
      email: 'new3@firm.com',
      email_verified: true,
    })
    expect(res.status).toBe(200)
    const audit = await db
      .prepare(
        "SELECT local_user_id FROM operator_mcp_audit WHERE clerk_subject = ? AND event_type = 'auth' AND decision = 'allow'"
      )
      .bind('user_new3')
      .first<{ local_user_id: string }>()
    expect(audit?.local_user_id).toBe('user_new3')
  })

  it('open policy: denies a non-matching domain and mints nothing', async () => {
    const res = await postAs(openCustomer(), {
      sub: 'user_evil',
      email: 'evil@notfirm.com',
      email_verified: true,
    })
    expect(res.status).toBe(401)
    expect(await grantFor('user_evil')).toBeNull()
  })

  it('open policy: denies an unverified primary email', async () => {
    const res = await postAs(openCustomer(), {
      sub: 'user_unv',
      email: 'unv@firm.com',
      email_verified: false,
    })
    expect(res.status).toBe(401)
    expect(await grantFor('user_unv')).toBeNull()
  })

  it('allowlist policy: never JITs, even on a matching domain', async () => {
    const allowlist: ResolvedMcpCustomer = {
      ...customer,
      connector: { ...customer.connector, policy: 'allowlist' },
      principals: [],
    }
    const res = await postAs(allowlist, {
      sub: 'user_al',
      email: 'al@firm.com',
      email_verified: true,
    })
    expect(res.status).toBe(401)
    expect(await grantFor('user_al')).toBeNull()
  })

  it('open policy: STICKY REVOKE — a revoked subject is denied, not re-minted', async () => {
    await db
      .prepare(
        'INSERT INTO mcp_issued_grants (customer_slug, clerk_user_id, email, profile, expires_at, revoked_at) ' +
          'VALUES (?, ?, ?, ?, ?, ?)'
      )
      .bind(
        'smd',
        'user_rev',
        'user_rev@firm.com',
        'crane',
        '2999-01-01T00:00:00.000Z',
        '2026-06-01T00:00:00.000Z'
      )
      .run()
    const res = await postAs(openCustomer(), {
      sub: 'user_rev',
      email: 'user_rev@firm.com',
      email_verified: true,
    })
    expect(res.status).toBe(401)
    const audit = await db
      .prepare("SELECT reason FROM operator_mcp_audit WHERE decision = 'deny' ORDER BY id DESC")
      .first<{ reason: string }>()
    expect(audit?.reason).toBe('jit_revoked')
  })
})

const unreachableRead = (): Promise<RuntimeReadResult> =>
  Promise.resolve({ ok: false, kind: 'audit_log', reason: 'unreachable' })

const CTX: McpToolContext = {
  customerId: 'smd',
  subject: CLERK_USER_ID,
  email: 'pilot@example.com',
  profile: 'crane',
  readRuntime: unreachableRead,
}

describe('JSON-RPC dispatcher', () => {
  it('initializes and lists the live operator_status tool', async () => {
    const initialized = await dispatchMcpRequest(
      { jsonrpc: '2.0', id: 1, method: 'initialize' },
      CTX
    )
    const initBody = await initialized.json<{ result: { protocolVersion: string } }>()
    expect(initBody.result.protocolVersion).toBeTruthy()

    const listed = await dispatchMcpRequest({ jsonrpc: '2.0', id: 2, method: 'tools/list' }, CTX)
    const listBody = await listed.json<{ result: { tools: { name: string }[] } }>()
    expect(listBody.result.tools.map((tool) => tool.name)).toContain('operator_status')
  })

  it('parses valid JSON-RPC and rejects malformed input', async () => {
    expect(parseMcpBody('{"jsonrpc":"2.0","id":1,"method":"ping"}')).toHaveProperty('req')
    const malformed = parseMcpBody('{not json')
    if (!('error' in malformed)) throw new Error('malformed JSON unexpectedly parsed')
    expect((await malformed.error.json<{ error: { code: number } }>()).error.code).toBe(-32700)

    const invalid = parseMcpBody('{"foo":"bar"}')
    if (!('error' in invalid)) throw new Error('invalid JSON-RPC unexpectedly parsed')
    expect((await invalid.error.json<{ error: { code: number } }>()).error.code).toBe(-32600)
  })

  it('rejects unknown tools without invoking a runtime read', async () => {
    let reads = 0
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 3,
        method: 'tools/call',
        params: { name: 'unknown_tool', arguments: {} },
      },
      {
        ...CTX,
        readRuntime: () => {
          reads += 1
          return unreachableRead()
        },
      }
    )
    expect((await response.json<{ error: { code: number } }>()).error.code).toBe(-32601)
    expect(reads).toBe(0)
  })
})

describe('operator_handoff_task', () => {
  it('lists operator_handoff_task in tools/list', async () => {
    const listed = await dispatchMcpRequest({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, CTX)
    const body = await listed.json<{ result: { tools: { name: string }[] } }>()
    expect(body.result.tools.map((t) => t.name)).toContain('operator_handoff_task')
  })

  it('returns not_configured when sendHandoff is absent', async () => {
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: { name: 'operator_handoff_task', arguments: { task: 'do something' } },
      },
      CTX
    )
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    const payload = JSON.parse(body.result.content[0].text)
    expect(payload).toMatchObject({ ok: false, error: 'not_configured' })
  })

  it('returns task_required when task is missing or empty', async () => {
    for (const args of [{}, { task: '' }, { task: '   ' }]) {
      const response = await dispatchMcpRequest(
        {
          jsonrpc: '2.0',
          id: 3,
          method: 'tools/call',
          params: { name: 'operator_handoff_task', arguments: args },
        },
        { ...CTX, sendHandoff: async () => {} }
      )
      const body = await response.json<{ result: { content: { text: string }[] } }>()
      expect(JSON.parse(body.result.content[0].text)).toMatchObject({
        ok: false,
        error: 'task_required',
      })
    }
  })

  it('calls sendHandoff with the handoff_id and returns accepted on success', async () => {
    const calls: { handoff_id: string; task: string; context?: string }[] = []
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 4,
        method: 'tools/call',
        params: {
          name: 'operator_handoff_task',
          arguments: { task: 'review receipts mailbox', context: 'focus on last 30 days' },
        },
      },
      {
        ...CTX,
        sendHandoff: async (params) => {
          calls.push(params)
        },
      }
    )
    expect(calls).toHaveLength(1)
    expect(calls[0].task).toBe('review receipts mailbox')
    expect(calls[0].context).toBe('focus on last 30 days')
    expect(typeof calls[0].handoff_id).toBe('string')
    expect(calls[0].handoff_id.length).toBeGreaterThan(0)
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    const payload = JSON.parse(body.result.content[0].text)
    expect(payload).toMatchObject({ ok: true, accepted: true, handoff_id: calls[0].handoff_id })
  })

  it('returns delivery_failed when sendHandoff throws', async () => {
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 5,
        method: 'tools/call',
        params: { name: 'operator_handoff_task', arguments: { task: 'do something' } },
      },
      {
        ...CTX,
        sendHandoff: async () => {
          throw new Error('machine unreachable')
        },
      }
    )
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    expect(JSON.parse(body.result.content[0].text)).toMatchObject({
      ok: false,
      error: 'delivery_failed',
    })
  })

  it('routes sendHandoff through mcp-route with auth-bound fields', async () => {
    const db: D1Database = await freshDb()
    await seedCustomer(db)
    const loaded = await loadMcpCustomer(db, 'smd')
    if (!loaded) throw new Error('test customer did not load')

    const handoffs: { handoff_id: string; task: string }[] = []
    const response = await handleMcpPost(
      new Request(RESOURCE_URI, {
        method: 'POST',
        headers: { Authorization: 'Bearer token' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          method: 'tools/call',
          params: { name: 'operator_handoff_task', arguments: { task: 'triage inbox' } },
        }),
      }),
      new URL(RESOURCE_URI),
      {
        db,
        customer: loaded,
        verifier: claimsVerifier(claims()),
        readRuntime: unreachableRead,
        sendHandoff: async (_auth, params) => {
          handoffs.push(params)
        },
      }
    )
    expect(response.status).toBe(200)
    expect(handoffs).toHaveLength(1)
    expect(handoffs[0].task).toBe('triage inbox')
    expect(typeof handoffs[0].handoff_id).toBe('string')
  })
})

describe('ask_operator', () => {
  it('lists ask_operator in tools/list', async () => {
    const listed = await dispatchMcpRequest({ jsonrpc: '2.0', id: 1, method: 'tools/list' }, CTX)
    const body = await listed.json<{ result: { tools: { name: string }[] } }>()
    expect(body.result.tools.map((t) => t.name)).toContain('ask_operator')
  })

  it('returns not_configured when driveTurn is absent', async () => {
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 2,
        method: 'tools/call',
        params: { name: 'ask_operator', arguments: { message: 'what is my status?' } },
      },
      CTX
    )
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    expect(JSON.parse(body.result.content[0].text)).toMatchObject({
      ok: false,
      error: 'not_configured',
    })
  })

  it('returns message_required when message is missing or empty', async () => {
    for (const args of [{}, { message: '' }, { message: '   ' }]) {
      const response = await dispatchMcpRequest(
        {
          jsonrpc: '2.0',
          id: 3,
          method: 'tools/call',
          params: { name: 'ask_operator', arguments: args },
        },
        { ...CTX, driveTurn: async () => ({ reply: 'unreached' }) }
      )
      const body = await response.json<{ result: { content: { text: string }[] } }>()
      expect(JSON.parse(body.result.content[0].text)).toMatchObject({
        ok: false,
        error: 'message_required',
      })
    }
  })

  it('returns the reply and thread_id on success', async () => {
    const calls: { message: string; thread_id?: string }[] = []
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 4,
        method: 'tools/call',
        params: {
          name: 'ask_operator',
          arguments: { message: 'summarize the Ochoa matter', thread_id: 'thr_1' },
        },
      },
      {
        ...CTX,
        driveTurn: async (params) => {
          calls.push(params)
          return { reply: 'The Ochoa matter is in discovery.', thread_id: 'thr_1' }
        },
      }
    )
    expect(calls).toHaveLength(1)
    expect(calls[0].message).toBe('summarize the Ochoa matter')
    expect(calls[0].thread_id).toBe('thr_1')
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    expect(JSON.parse(body.result.content[0].text)).toMatchObject({
      ok: true,
      reply: 'The Ochoa matter is in discovery.',
      thread_id: 'thr_1',
    })
  })

  it('returns delivery_failed when driveTurn throws', async () => {
    const response = await dispatchMcpRequest(
      {
        jsonrpc: '2.0',
        id: 5,
        method: 'tools/call',
        params: { name: 'ask_operator', arguments: { message: 'anything' } },
      },
      {
        ...CTX,
        driveTurn: async () => {
          throw new Error('machine unreachable')
        },
      }
    )
    const body = await response.json<{ result: { content: { text: string }[] } }>()
    expect(JSON.parse(body.result.content[0].text)).toMatchObject({
      ok: false,
      error: 'delivery_failed',
    })
  })

  it('routes driveTurn through mcp-route with auth-bound identity', async () => {
    const db: D1Database = await freshDb()
    await seedCustomer(db)
    const loaded = await loadMcpCustomer(db, 'smd')
    if (!loaded) throw new Error('test customer did not load')

    const turns: { message: string; auth: { subject: string; email: string; profile: string } }[] =
      []
    const response = await handleMcpPost(
      new Request(RESOURCE_URI, {
        method: 'POST',
        headers: { Authorization: 'Bearer token' },
        body: JSON.stringify({
          jsonrpc: '2.0',
          id: 1,
          method: 'tools/call',
          params: { name: 'ask_operator', arguments: { message: 'status please' } },
        }),
      }),
      new URL(RESOURCE_URI),
      {
        db,
        customer: loaded,
        verifier: claimsVerifier(claims()),
        readRuntime: unreachableRead,
        driveTurn: async (auth, params) => {
          turns.push({
            message: params.message,
            auth: { subject: auth.subject, email: auth.email, profile: auth.profile },
          })
          return { reply: 'all clear' }
        },
      }
    )
    expect(response.status).toBe(200)
    expect(turns).toHaveLength(1)
    expect(turns[0].message).toBe('status please')
    expect(typeof turns[0].auth.subject).toBe('string')
    expect(turns[0].auth.subject.length).toBeGreaterThan(0)
  })
})

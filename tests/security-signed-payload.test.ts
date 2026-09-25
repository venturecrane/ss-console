/**
 * The shared signed-token codec (src/lib/security/signed-payload.ts), which
 * OAuth state, signed booking links and assessment sessions all sign and
 * verify through since 2026-09-25 (review 2026-09-25, Code Quality 3). Each
 * caller's own suite (tests/oauth-callback.test.ts, tests/booking/signed-link
 * .test.ts, tests/assessment/session.test.ts) still runs unchanged against it;
 * this file pins the codec's own contract.
 *
 * What would make this false: a payload read before its signature is checked,
 * a malformed token that needs the key to be refused, or an encoder that is
 * not the inverse of its decoder.
 */

import { describe, it, expect, vi } from 'vitest'
import {
  base64UrlDecode,
  base64UrlEncode,
  importSigningKey,
  signPayload,
  verifySignedPayload,
} from '../src/lib/security/signed-payload'

const KEY_B64 = btoa(String.fromCharCode(...new Uint8Array(32).map((_, i) => i + 1)))
const OTHER_B64 = btoa(String.fromCharCode(...new Uint8Array(32).map((_, i) => 200 - i)))
const load = (b64: string) => () => importSigningKey(b64, 'TEST_KEY', 'testing')

describe('signed-payload codec', () => {
  it('base64url round-trips every byte value without padding or +/', () => {
    const bytes = Uint8Array.from({ length: 256 }, (_, i) => i)
    const encoded = base64UrlEncode(bytes)
    expect(encoded).not.toMatch(/[+/=]/)
    expect(base64UrlDecode(encoded)).toEqual(bytes)
  })

  it('verifies what it signed and hands the payload back as data', async () => {
    const token = await signPayload(await load(KEY_B64)(), { v: 1, sid: 'abc', exp: 9 })
    expect(await verifySignedPayload(token, load(KEY_B64))).toEqual({
      ok: true,
      payload: { v: 1, sid: 'abc', exp: 9 },
    })
  })

  it('refuses a token signed with another key, and a tampered payload', async () => {
    const token = await signPayload(await load(OTHER_B64)(), { v: 1 })
    expect(await verifySignedPayload(token, load(KEY_B64))).toEqual({
      ok: false,
      error: 'bad_signature',
    })
    const good = await signPayload(await load(KEY_B64)(), { v: 1, role: 'client' })
    const [, sig] = good.split('.')
    const forged = `${base64UrlEncode(new TextEncoder().encode('{"v":1,"role":"admin"}'))}.${sig}`
    expect(await verifySignedPayload(forged, load(KEY_B64))).toEqual({
      ok: false,
      error: 'bad_signature',
    })
  })

  it('refuses a structurally malformed token without loading the key', async () => {
    const loadKey = vi.fn(load(KEY_B64))
    for (const token of [undefined, '', 'nodot', '.sig', 'payload.', 42]) {
      expect(await verifySignedPayload(token, loadKey)).toEqual({ ok: false, error: 'malformed' })
    }
    expect(loadKey).not.toHaveBeenCalled()
  })

  it('names the secret when the key is missing or not base64', async () => {
    await expect(importSigningKey(undefined, 'SOME_KEY', 'issuing things')).rejects.toThrow(
      'SOME_KEY is not configured. Set it in wrangler env (32 random bytes, base64-encoded) before issuing things.'
    )
    await expect(importSigningKey('%%%', 'SOME_KEY', 'issuing things')).rejects.toThrow(
      'SOME_KEY is not valid base64.'
    )
  })
})

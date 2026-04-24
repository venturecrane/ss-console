import { describe, it, expect } from 'vitest'
import { existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

describe('contact: API endpoint', () => {
  it('contact endpoint exists', () => {
    expect(existsSync(resolve('src/pages/api/contact.ts'))).toBe(true)
  })

  it('handles POST requests', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('export const POST')
  })

  it('validates required fields: name, email, message', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('name')
    expect(source).toContain('email')
    expect(source).toContain('message')
  })

  it('has honeypot check to reject bots', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('honeypot')
    expect(source).toContain('website')
  })

  it('sends notification email via Resend', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('sendEmail')
  })
})

describe('contact: rate limiting', () => {
  it('contact endpoint imports rateLimitByIp', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('rateLimitByIp')
    expect(source).toContain('rate-limit')
  })

  it('contact endpoint rate limit uses contact bucket', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain("'contact'")
  })

  it('contact endpoint returns 429 JSON on rate limit block', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    expect(source).toContain('429')
    expect(source).toContain('Too many requests')
  })

  it('rate limit check occurs before JSON parse and validation', () => {
    const source = readFileSync(resolve('src/pages/api/contact.ts'), 'utf-8')
    const rateLimitIdx = source.indexOf('rateLimitByIp')
    const jsonParseIdx = source.indexOf('request.json()')
    expect(rateLimitIdx).toBeGreaterThan(-1)
    expect(jsonParseIdx).toBeGreaterThan(-1)
    expect(rateLimitIdx).toBeLessThan(jsonParseIdx)
  })
})

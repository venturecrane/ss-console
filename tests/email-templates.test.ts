import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import {
  bookingLinkInviteEmailHtml,
  invoiceSentEmailHtml,
  magicLinkEmailHtml,
} from '../src/lib/email/templates'

const templatePath = resolve('src/lib/email/templates.ts')

describe('email templates: shared shell', () => {
  it('keeps templates.ts within the file line ceiling', () => {
    const lines = readFileSync(templatePath, 'utf-8').split('\n')
    expect(lines.length).toBeLessThanOrEqual(500)
  })

  it('escapes user-authored values in rendered emails', () => {
    const payload = `<script>alert("x")</script>`
    const magic = magicLinkEmailHtml(payload, 'https://portal.example/auth?token=x&next=<bad>')
    const invoice = invoiceSentEmailHtml(
      payload,
      '$1,000 <due>',
      'https://portal.example/invoices/1'
    )
    const invite = bookingLinkInviteEmailHtml({
      contactName: payload,
      businessName: payload,
      bookingUrl: 'https://smd.services/book?t=x&next=<bad>',
    })

    for (const html of [magic, invoice, invite]) {
      expect(html).not.toContain('<script>')
      expect(html).toContain('&lt;script&gt;')
    }
  })
})

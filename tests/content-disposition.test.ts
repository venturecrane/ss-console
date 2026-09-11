/**
 * The transcript download names its attachment from client-uploaded metadata.
 * A header built from that name must survive a quote, a CR/LF, and non-ASCII
 * without splitting or ending the parameter early (2026-09-09 review,
 * Security LOW "unescaped filename in Content-Disposition").
 */
import { describe, expect, it } from 'vitest'
import { contentDispositionAttachment } from '../src/pages/api/admin/assessments/[id]/transcript'

describe('contentDispositionAttachment', () => {
  it('passes a plain name through in both forms', () => {
    expect(contentDispositionAttachment('call-2026-09-10.txt')).toBe(
      `attachment; filename="call-2026-09-10.txt"; filename*=UTF-8''call-2026-09-10.txt`
    )
  })

  it('strips quotes, backslashes, CR and LF so the header cannot be split or ended early', () => {
    const header = contentDispositionAttachment('a"b\\c\r\nSet-Cookie: x=y.txt')
    expect(header).not.toMatch(/[\r\n]/)
    expect(header).not.toContain('"b')
    expect(header).toBe(
      `attachment; filename="abcSet-Cookie: x=y.txt"; filename*=UTF-8''abcSet-Cookie%3A%20x%3Dy.txt`
    )
  })

  it('gives non-ASCII names an ASCII fallback and an RFC 5987 encoded full name', () => {
    expect(contentDispositionAttachment('entrevista café.txt')).toBe(
      `attachment; filename="entrevista caf_.txt"; filename*=UTF-8''entrevista%20caf%C3%A9.txt`
    )
  })

  it('falls back to transcript.txt when nothing printable survives', () => {
    expect(contentDispositionAttachment('"\r\n')).toBe(
      `attachment; filename="transcript.txt"; filename*=UTF-8''transcript.txt`
    )
  })
})

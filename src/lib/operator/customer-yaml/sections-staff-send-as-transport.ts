import type { ValidationError } from './types'
import { canonRosterAddress, isPlainObject } from './helpers'

/**
 * Validate the transport a `scope.staff_send_as` list authors (ADR 0089
 * section 9). Mirrors `bootstrap/validate_staff_send_as.py`
 * (`validate_staff_send_as_transport`); the parity fixtures pin the two.
 *
 * `via` is `msgraph` (absent means this, every entry authored before section 9)
 * or `gmail`. One transport per seat: a mixed list is refused, never guessed at.
 *
 * On `via: gmail` every precondition the broker's Gmail transport needs is
 * checked here, so a seat that could not send safely is refused at authoring:
 *
 * - `google_auth.mode: dwd`, because the broker sends by impersonating the mailbox;
 * - scopes that let the key both send and search the owner's Sent mail (the
 *   forgery guard): `gmail.modify`, or `mail.google.com`, or `gmail.send` with
 *   `gmail.readonly`;
 * - `connectors.Email.adapter: agentmail`, because the approval email and the
 *   owner's reply ride the seat's AgentMail inbox;
 * - each entry's `mailbox` is the DWD subject or a managed mailbox, and its
 *   `address` is that mailbox or one of its authored `send_as` addresses.
 */
const VIAS = ['msgraph', 'gmail'] as const

const GMAIL = 'https://www.googleapis.com/auth/'
const GMAIL_SCOPE_SETS: readonly (readonly string[])[] = [
  [`${GMAIL}gmail.modify`],
  ['https://mail.google.com/'],
  [`${GMAIL}gmail.send`, `${GMAIL}gmail.readonly`],
]

const PATH = 'scope.staff_send_as'

function canon(raw: unknown): string | null {
  return typeof raw === 'string' ? canonRosterAddress(raw) : null
}

function push(errors: ValidationError[], path: string, message: string): void {
  errors.push({ code: 'InvalidStaffSendAs', path, message })
}

export function checkStaffSendAsTransport(
  root: Record<string, unknown>,
  errors: ValidationError[]
): void {
  const scope = root['scope']
  const raw = isPlainObject(scope) ? scope['staff_send_as'] : undefined
  if (!Array.isArray(raw)) return
  const vias = new Set<string>()
  for (let i = 0; i < raw.length; i++) {
    const entry: unknown = raw[i]
    if (!isPlainObject(entry)) continue
    const via = entry['via'] === undefined ? 'msgraph' : entry['via']
    if (typeof via !== 'string' || !(VIAS as readonly string[]).includes(via)) {
      push(errors, `${PATH}[${i}].via`, `via must be one of ${VIAS.join(', ')}`)
      return
    }
    vias.add(via)
    if (via === 'msgraph' && 'mailbox' in entry) {
      push(errors, `${PATH}[${i}].mailbox`, 'only a via: gmail entry names a mailbox')
    }
  }
  if (vias.size > 1) {
    push(errors, PATH, 'every entry must author the same via (one transport per seat)')
    return
  }
  if (vias.has('gmail')) checkGmail(root, raw, errors)
}

function checkGmail(
  root: Record<string, unknown>,
  raw: unknown[],
  errors: ValidationError[]
): void {
  const google = root['google_auth']
  if (!isPlainObject(google) || google['mode'] !== 'dwd') {
    push(errors, PATH, 'via: gmail needs google_auth.mode: dwd')
    return
  }
  const scopes = new Set(
    (Array.isArray(google['scopes']) ? google['scopes'] : [])
      .filter((s): s is string => typeof s === 'string')
      .map((s) => s.trim())
  )
  if (!GMAIL_SCOPE_SETS.some((set) => set.every((s) => scopes.has(s)))) {
    push(
      errors,
      'google_auth.scopes',
      'via: gmail needs gmail.modify (or mail.google.com, or gmail.send with gmail.readonly): the broker must send and search the Sent mail'
    )
  }
  const connectors = root['connectors']
  const email = isPlainObject(connectors) ? connectors['Email'] : undefined
  if (!isPlainObject(email) || email['adapter'] !== 'agentmail') {
    push(errors, 'connectors.Email.adapter', 'via: gmail needs the agentmail Email adapter')
  }
  const sendAs = authoredSendAs(google)
  raw.forEach((entry, i) => {
    if (isPlainObject(entry)) checkGmailEntry(entry, `${PATH}[${i}]`, sendAs, errors)
  })
}

function authoredSendAs(google: Record<string, unknown>): Map<string, Set<string>> {
  const out = new Map<string, Set<string>>()
  const subject = canon(google['subject'])
  if (subject) out.set(subject, new Set([subject]))
  const managed = Array.isArray(google['managed_mailboxes']) ? google['managed_mailboxes'] : []
  for (const mb of managed) {
    const address = isPlainObject(mb) ? canon(mb['address']) : null
    if (!address || !isPlainObject(mb)) continue
    const set = out.get(address) ?? new Set<string>()
    set.add(address)
    const aliases = Array.isArray(mb['send_as']) ? mb['send_as'] : []
    for (const a of aliases) {
      const c = canon(a)
      if (c) set.add(c)
    }
    out.set(address, set)
  }
  return out
}

function checkGmailEntry(
  entry: Record<string, unknown>,
  path: string,
  sendAs: Map<string, Set<string>>,
  errors: ValidationError[]
): void {
  const mailbox = canon(entry['mailbox'])
  if (mailbox === null || mailbox.startsWith('@')) {
    push(errors, `${path}.mailbox`, 'a via: gmail entry needs the exact Google Workspace mailbox')
    return
  }
  const allowed = sendAs.get(mailbox)
  if (!allowed) {
    push(
      errors,
      `${path}.mailbox`,
      `${mailbox} is not google_auth.subject or a managed mailbox, so the broker could never impersonate it`
    )
    return
  }
  const address = canon(entry['address'])
  if (address && !allowed.has(address)) {
    push(errors, `${path}.address`, `${address} is not ${mailbox} or one of its send_as addresses`)
  }
}

import type { DeviceSenderEntry, ValidationError } from './types'
import { isPlainObject } from './helpers'
import { canonRosterAddress } from './sections-scope'

const DEVICE_SENDER_KEYS = new Set(['address', 'replies_to'])

/**
 * Validate `scope.device_senders`: mailboxes that are MACHINES, not people.
 *
 * An office scanner, a fax gateway, a copier: a device on the firm's own
 * domain that emails documents to the Operator. A reply sent back to the
 * device reaches nobody, so each entry names the person the Operator answers
 * instead, `{address, replies_to}`, exactly those two keys.
 *
 * Every rule is a way the redirect could otherwise widen who the seat writes
 * to, and each mirrors `_validate_device_senders` in the overlay validator
 * (`bootstrap/validate.py`), pinned by the shared parity fixtures:
 *
 *   - both are EXACT addresses. A domain grant would turn "this scanner" into
 *     "anything at the firm", and a domain as the target is nobody;
 *   - `address` is covered by `scope.inbound_allow_from`, exactly or by an
 *     `@domain` grant. A device the seat does not answer has no reply to
 *     redirect, so authoring one would be a silent no-op;
 *   - `replies_to` is on `scope.admins`. The redirect sends a reply to somebody
 *     other than who wrote in, so it may only reach a person who already
 *     speaks for the firm. The broker re-checks this on every reply;
 *   - no `address` twice (case-insensitive), so a device has one answerer.
 *
 * Absent/null yields `[]`: no device, every reply goes to whoever wrote in.
 */
export function checkDeviceSenders(
  raw: unknown,
  surface: { inbound_allow_from: string[]; admins: string[] },
  errors: ValidationError[]
): DeviceSenderEntry[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.device_senders',
      message: 'scope.device_senders must be a list of {address, replies_to}',
    })
    return []
  }
  const inbound = canonSet(surface.inbound_allow_from)
  const admins = canonSet(surface.admins)
  const seen = new Set<string>()
  const out: DeviceSenderEntry[] = []
  for (let i = 0; i < raw.length; i++) {
    const path = `scope.device_senders[${i}]`
    const entry = deviceEntry(raw[i], path, inbound, admins, errors)
    if (entry === null) continue
    if (seen.has(entry.address)) {
      errors.push({
        code: 'InvalidDeviceSenders',
        path: `${path}.address`,
        message: `${entry.address} appears more than once in scope.device_senders`,
      })
      continue
    }
    seen.add(entry.address)
    out.push(entry)
  }
  return out
}

function canonSet(entries: string[]): Set<string> {
  const out = new Set<string>()
  for (const value of entries) {
    const canon = canonRosterAddress(value)
    if (canon !== null) out.add(canon)
  }
  return out
}

function deviceEntry(
  raw: unknown,
  path: string,
  inbound: Set<string>,
  admins: Set<string>,
  errors: ValidationError[]
): DeviceSenderEntry | null {
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: 'each device_senders entry must be {address, replies_to}',
    })
    return null
  }
  const extra = Object.keys(raw).filter((k) => !DEVICE_SENDER_KEYS.has(k))
  if (extra.length > 0) {
    errors.push({
      code: 'InvalidDeviceSenders',
      path,
      message: `unknown key(s) ${extra.sort().join(', ')}; a device entry is exactly address and replies_to`,
    })
    return null
  }
  const address = exactAddress(raw['address'], `${path}.address`, errors)
  if (address === null) return null
  const repliesTo = exactAddress(raw['replies_to'], `${path}.replies_to`, errors)
  if (repliesTo === null) return null
  const domain = address.split('@')[1]
  if (!inbound.has(address) && !inbound.has(`@${domain}`)) {
    errors.push({
      code: 'InvalidDeviceSenders',
      path: `${path}.address`,
      message: `${address} is not on scope.inbound_allow_from, so the seat never answers it and there is no reply to redirect`,
    })
    return null
  }
  if (!admins.has(repliesTo)) {
    errors.push({
      code: 'InvalidDeviceSenders',
      path: `${path}.replies_to`,
      message: `${repliesTo} is not on scope.admins; a device's reply may only be redirected to a person who speaks for the firm`,
    })
    return null
  }
  return { address, replies_to: repliesTo }
}

function exactAddress(raw: unknown, path: string, errors: ValidationError[]): string | null {
  const canon = typeof raw === 'string' ? canonRosterAddress(raw) : null
  if (canon === null || canon.startsWith('@')) {
    errors.push({
      code: 'InvalidDeviceSenders',
      path,
      message:
        'device_senders addresses must be exact addresses (local@domain); a device is one mailbox and its answerer is one person',
    })
    return null
  }
  return canon
}

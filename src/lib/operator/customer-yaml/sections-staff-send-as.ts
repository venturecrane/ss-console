import type { OutboundRosterEntry, StaffSendAsEntry, ValidationError } from './types'
import { isPlainObject } from './helpers'
import { canonRosterAddress } from './sections-scope'

/**
 * Validate `scope.staff_send_as` (ADR 0089): the staff members the Operator may
 * send email AS, each only on that person's own emailed approval of the exact
 * draft.
 *
 * WHAT AN ENTRY GRANTS. Nothing on its own. The seat must also author
 * `exposure.external_send_as_staff: confirm`, the firm's Microsoft 365 admin
 * must grant the Operator mailbox Send As on the address, and every send needs
 * that person's reply. The entry names who may be asked, and it names the only
 * person whose answer can send in their name: an administrator can cancel a
 * draft but can never send one as somebody else.
 *
 * Shape rules. Exact person addresses (an `@domain` grant would let the Operator
 * put anyone at the firm's name on a message), a non-empty display name (the
 * approval email addresses the person by it), no duplicates. And every address
 * must already be reachable under the seat's own counterparty surface
 * (`inbound_allow_from`, `admins`, or `outbound_roster`), because the approval
 * email and every notice go through the broker's recipient fence: an entry the
 * fence refuses is a staff member who is never asked, and a draft that silently
 * reaches nobody.
 *
 * Absent/null yields `[]`, which is fail-closed: no draft may carry a From.
 */
export function checkStaffSendAs(
  raw: unknown,
  surface: {
    inbound_allow_from: string[]
    admins: string[]
    outbound_roster: OutboundRosterEntry[]
  },
  errors: ValidationError[]
): StaffSendAsEntry[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.staff_send_as',
      message: 'scope.staff_send_as must be a list of {address, name}',
    })
    return []
  }
  const reachable = reachableSurface(surface)
  const seen = new Set<string>()
  const out: StaffSendAsEntry[] = []
  for (let i = 0; i < raw.length; i++) {
    const entry = staffEntry(raw[i], `scope.staff_send_as[${i}]`, seen, reachable, errors)
    if (entry === null) continue
    seen.add(entry.address)
    out.push(entry)
  }
  return out
}

interface Reachable {
  exact: Set<string>
  domains: Set<string>
}

function reachableSurface(surface: {
  inbound_allow_from: string[]
  admins: string[]
  outbound_roster: OutboundRosterEntry[]
}): Reachable {
  const exact = new Set<string>()
  const domains = new Set<string>()
  const all = [
    ...surface.inbound_allow_from,
    ...surface.admins,
    ...surface.outbound_roster.map((e) => e.address),
  ]
  for (const value of all) {
    const canon = canonRosterAddress(value)
    if (canon === null) continue
    if (canon.startsWith('@')) domains.add(canon.slice(1))
    else exact.add(canon)
  }
  return { exact, domains }
}

function staffEntry(
  raw: unknown,
  path: string,
  seen: Set<string>,
  reachable: Reachable,
  errors: ValidationError[]
): StaffSendAsEntry | null {
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: 'each staff_send_as entry must be {address, name}',
    })
    return null
  }
  const address = typeof raw['address'] === 'string' ? canonRosterAddress(raw['address']) : null
  if (address === null || address.startsWith('@')) {
    errors.push({
      code: 'InvalidStaffSendAs',
      path: `${path}.address`,
      message:
        "staff_send_as addresses must be exact person addresses (local@domain); a whole-@domain grant would put anyone at the firm's name on a message",
    })
    return null
  }
  const name = raw['name']
  if (typeof name !== 'string' || name.trim().length === 0) {
    errors.push({
      code: 'MissingField',
      path: `${path}.name`,
      message: 'staff_send_as entries need a display name',
    })
    return null
  }
  if (seen.has(address)) {
    errors.push({
      code: 'InvalidStaffSendAs',
      path: `${path}.address`,
      message: `${address} appears more than once in scope.staff_send_as`,
    })
    return null
  }
  const domain = address.split('@')[1]
  if (!reachable.exact.has(address) && !reachable.domains.has(domain)) {
    errors.push({
      code: 'InvalidStaffSendAs',
      path: `${path}.address`,
      message: `${address} is not on scope.inbound_allow_from, scope.admins, or scope.outbound_roster, so the approval email could never reach them`,
    })
    return null
  }
  return { address, name: name.trim() }
}

/**
 * Validate the top-level `staff_mailbox_reads` block against
 * `scope.staff_send_as` (ADR 0089 amendment 5a).
 *
 * WHY THE TWO LISTS MUST NOT MEET. An approver's inbox is the only place an
 * approve link exists: the approval email carries signed Send it buttons and
 * the broker keeps no Sent Items copy. `staff_mailbox_reads` lets the Operator
 * read a named staff member's inbox. A person on both lists would let the
 * Operator read its own approve link through a sanctioned tool and press it.
 * The msgraph-mail connector refuses that read at call time and boot smoke asks
 * Microsoft about every approver; this rule stops the config being authored at
 * all, where the reason is cheapest to read.
 *
 * Shape is checked as the connector reads it: a mapping whose `mailboxes` is a
 * list of address strings. The connector refuses every staff read on a
 * malformed block, so a malformed block is a config that silently reads
 * nothing, and it is refused here too. Absent/null is valid and reads nothing.
 */
export function checkStaffMailboxReads(
  root: Record<string, unknown>,
  staffSendAs: StaffSendAsEntry[],
  errors: ValidationError[]
): void {
  const raw = root['staff_mailbox_reads']
  if (raw === undefined || raw === null) return
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'staff_mailbox_reads',
      message: 'staff_mailbox_reads must be a mapping with a mailboxes list',
    })
    return
  }
  const mailboxes: unknown = raw['mailboxes']
  if (mailboxes === undefined || mailboxes === null) return
  if (!Array.isArray(mailboxes) || !mailboxes.every((m) => typeof m === 'string')) {
    errors.push({
      code: 'TypeMismatch',
      path: 'staff_mailbox_reads.mailboxes',
      message: 'staff_mailbox_reads.mailboxes must be a list of email addresses',
    })
    return
  }
  const approvers = new Set(staffSendAs.map((e) => e.address))
  mailboxes.forEach((value: string, i) => {
    const address = canonRosterAddress(value)
    if (address === null || !approvers.has(address)) return
    errors.push({
      code: 'InvalidStaffSendAs',
      path: `staff_mailbox_reads.mailboxes[${i}]`,
      message: `${address} is on scope.staff_send_as, so their inbox holds the approve links for drafts sent in their name; the Operator may never read an approver's mailbox`,
    })
  })
}

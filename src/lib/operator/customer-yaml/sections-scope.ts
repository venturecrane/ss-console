import {
  OUTBOUND_ROSTER_CLASSES,
  type OutboundRosterClass,
  type OutboundRosterEntry,
  type Scope,
  type ValidationError,
} from './types'
import { canonRosterAddress, isPlainObject, optionalStringList, requireStringList } from './helpers'
import { checkStaffSendAs } from './sections-staff-send-as'

/**
 * Public-mail providers where a whole-@domain grant is meaningless (the domain is
 * shared by millions), so a DOMAIN-form outbound_roster entry is rejected — but an
 * EXACT address at one of these domains is valid (a PI client is a consumer on
 * gmail). Mirrors `_PUBLIC_MAIL_DOMAINS` in the overlay validator.
 */
const PUBLIC_MAIL_DOMAINS = new Set([
  'gmail.com',
  'googlemail.com',
  'outlook.com',
  'hotmail.com',
  'live.com',
  'yahoo.com',
  'icloud.com',
  'me.com',
  'aol.com',
  'proton.me',
  'protonmail.com',
])

export function checkScope(root: Record<string, unknown>, errors: ValidationError[]): Scope {
  const raw = root['scope']
  if (raw === undefined || raw === null) {
    errors.push({ code: 'MissingField', path: 'scope', message: 'scope is required' })
    return emptyScope()
  }
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path: 'scope', message: 'scope must be an object' })
    return emptyScope()
  }
  const inboundAllowFrom = optionalStringList(
    raw,
    'inbound_allow_from',
    'scope.inbound_allow_from',
    errors
  )
  const admins = checkAdmins(raw['admins'], errors)
  const outboundRoster = checkOutboundRoster(raw['outbound_roster'], errors)
  return {
    email_folders_visible: requireStringList(
      raw,
      'email_folders_visible',
      'scope.email_folders_visible',
      errors
    ),
    email_folders_blind: requireStringList(
      raw,
      'email_folders_blind',
      'scope.email_folders_blind',
      errors
    ),
    email_keyword_blocks: requireStringList(
      raw,
      'email_keyword_blocks',
      'scope.email_keyword_blocks',
      errors
    ),
    domain_blocks: requireStringList(raw, 'domain_blocks', 'scope.domain_blocks', errors),
    matter_blocks: optionalStringList(raw, 'matter_blocks', 'scope.matter_blocks', errors),
    inbound_allow_from: inboundAllowFrom,
    outbound_roster: outboundRoster,
    admins,
    rule_requests_to: checkRuleRequestsTo(raw['rule_requests_to'], admins, errors),
    ops_reply_from: checkOpsReplyFrom(raw['ops_reply_from'], errors),
    staff_send_as: checkStaffSendAs(
      raw['staff_send_as'],
      { inbound_allow_from: inboundAllowFrom, admins, outbound_roster: outboundRoster },
      errors
    ),
  }
}

/**
 * The two domains an operations answer may come from. SMD is one firm with two
 * mail domains, and this list is authored here rather than left to the per-seat
 * config because the whole point of the key is that a SEAT cannot be talked into
 * widening it: a customer.yaml that named an arbitrary domain would turn
 * "SMD answers operations requests" into "whoever the config says does".
 */
const OPS_REPLY_DOMAINS = ['smd.services', 'smdurgan.com']

/**
 * Validate `scope.ops_reply_from` (ss-console#2546): whose reply, quoting an
 * `[ops XXXX]` tag, resolves that operations request.
 *
 * WHAT THIS GRANTS, stated narrowly because the grant is narrow. An address here
 * may do exactly one thing: answer a request the Operator itself raised, by
 * quoting the eight-hex tag that request carries, and the whole effect of that
 * answer is one templated notice to the person who asked. It is NOT inbound
 * trust. It does not put the address on `inbound_allow_from`, it does not make
 * the sender an admin, and a message from one of these addresses that quotes no
 * tag is the same untrusted mail it was before.
 *
 * THE TAG IS THE CAPABILITY, and the spoof class is the same for every address
 * on the list. No seat gets an SPF or DKIM verdict on inbound mail (ADR 0085
 * §5), so `scott@` buys nothing over `team@` in forgery terms; what bounds the
 * damage is the effect, not the sender.
 *
 * Two shape rules beyond person-form. Every entry must sit at one of
 * {@link OPS_REPLY_DOMAINS}, so a config cannot hand the answering power to a
 * third party; and an `@domain` grant is refused, because "anyone at SMD" is not
 * a person and this list is read as the people who answer.
 *
 * Absent/null yields `[]`, which is fail-closed: no reply resolves anything, the
 * request lapses at seven days, and the person who asked is told that.
 */
function checkOpsReplyFrom(raw: unknown, errors: ValidationError[]): string[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.ops_reply_from',
      message: 'scope.ops_reply_from must be a list',
    })
    return []
  }
  const seen = new Set<string>()
  const out: string[] = []
  for (let i = 0; i < raw.length; i++) {
    const canon = opsReplyEntry(raw[i], `scope.ops_reply_from[${i}]`, seen, errors)
    if (canon === null) continue
    seen.add(canon)
    out.push(canon)
  }
  return out
}

/** One answering entry: person-shaped, non-duplicate, and at an SMD domain. */
function opsReplyEntry(
  raw: unknown,
  path: string,
  seen: Set<string>,
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.trim().length === 0) {
    errors.push({
      code: 'MissingField',
      path,
      message: 'ops_reply_from entries must be non-empty strings',
    })
    return null
  }
  const canon = canonRosterAddress(raw)
  if (canon === null || canon.startsWith('@')) {
    errors.push({
      code: 'InvalidOpsReplyFrom',
      path,
      message:
        'ops_reply_from must be exact person addresses (local@domain); a whole-@domain grant is not somebody who answers',
    })
    return null
  }
  if (seen.has(canon)) {
    errors.push({
      code: 'InvalidOpsReplyFrom',
      path,
      message: `${canon} appears more than once in scope.ops_reply_from`,
    })
    return null
  }
  if (!OPS_REPLY_DOMAINS.some((d) => canon.endsWith(`@${d}`))) {
    errors.push({
      code: 'InvalidOpsReplyFrom',
      path,
      message: `${canon} is not at an SMD domain (${OPS_REPLY_DOMAINS.join(', ')}); operations requests are answered by SMD`,
    })
    return null
  }
  return canon
}

/**
 * Validate `scope.rule_requests_to` (ss-console#2546): who is EMAILED when a
 * non-admin asks for a firm-level rule. Routing, not authority — every admin
 * keeps the power to apply a rule, and this list only decides whose inbox the
 * request lands in.
 *
 * Same person-address shape as `scope.admins`, plus the one rule that makes the
 * key safe: every entry must already be an admin. Two things fall out of it.
 * A rule request cannot be routed to somebody who could not act on it, and the
 * broker's recipient fence — admins, the inbound roster, the typed outbound
 * roster — already admits every address here, so the send cannot be authored
 * into a refusal.
 *
 * Absent/null yields `[]`. That is fail-closed in the honest direction: no
 * admin is emailed, and nothing anywhere may claim one was.
 */
function checkRuleRequestsTo(raw: unknown, admins: string[], errors: ValidationError[]): string[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.rule_requests_to',
      message: 'scope.rule_requests_to must be a list',
    })
    return []
  }
  const known = new Set(admins)
  const seen = new Set<string>()
  const out: string[] = []
  for (let i = 0; i < raw.length; i++) {
    const canon = ruleRequestEntry(raw[i], `scope.rule_requests_to[${i}]`, seen, known, errors)
    if (canon === null) continue
    seen.add(canon)
    out.push(canon)
  }
  return out
}

/** One routing entry: person-shaped, non-duplicate, and already an admin. */
function ruleRequestEntry(
  raw: unknown,
  path: string,
  seen: Set<string>,
  admins: Set<string>,
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.trim().length === 0) {
    errors.push({
      code: 'MissingField',
      path,
      message: 'rule_requests_to entries must be non-empty strings',
    })
    return null
  }
  const canon = canonRosterAddress(raw)
  if (canon === null || canon.startsWith('@')) {
    errors.push({
      code: 'InvalidRuleRequestsTo',
      path,
      message:
        'rule_requests_to must be exact person addresses (local@domain); a whole-@domain grant routes a request to nobody in particular',
    })
    return null
  }
  if (seen.has(canon)) {
    errors.push({
      code: 'InvalidRuleRequestsTo',
      path,
      message: `${canon} appears more than once in scope.rule_requests_to`,
    })
    return null
  }
  if (!admins.has(canon)) {
    errors.push({
      code: 'InvalidRuleRequestsTo',
      path,
      message: `${canon} is not on scope.admins; a rule request may only be routed to somebody who can apply it`,
    })
    return null
  }
  return canon
}

/**
 * Validate `scope.admins` (ADR 0085 §2): the Operator-admin allow list, a flat
 * list of PERSON email addresses. Canonicalized through the same
 * {@link canonRosterAddress} the classifier uses, so "same address" means the
 * same thing here as it does at runtime.
 *
 * Two rules beyond shape, both deliberate:
 *   - an `@domain` grant is refused outright. Establishment authority attaches
 *     to a person; a whole-domain admin grant would silently make every future
 *     hire at the firm able to rewrite the firm's voice.
 *   - a duplicate canonical address is refused, so the authored list reads as
 *     the authoritative count of who holds the authority.
 *
 * Absent/null yields `[]` — fail-closed, per the ADR: no list, no admins.
 */
function checkAdmins(raw: unknown, errors: ValidationError[]): string[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.admins',
      message: 'scope.admins must be a list',
    })
    return []
  }
  const seen = new Set<string>()
  const out: string[] = []
  for (let i = 0; i < raw.length; i++) {
    const canon = adminEntry(raw[i], `scope.admins[${i}]`, seen, errors)
    if (canon === null) continue
    seen.add(canon)
    out.push(canon)
  }
  return out
}

/** One admin entry: a person-shaped, non-duplicate address, or null on any violation. */
function adminEntry(
  raw: unknown,
  path: string,
  seen: Set<string>,
  errors: ValidationError[]
): string | null {
  if (typeof raw !== 'string' || raw.trim().length === 0) {
    errors.push({ code: 'MissingField', path, message: 'admin entries must be non-empty strings' })
    return null
  }
  const canon = canonRosterAddress(raw)
  if (canon === null || canon.startsWith('@')) {
    errors.push({
      code: 'InvalidAdminList',
      path,
      message:
        'admins must be exact person addresses (local@domain); a whole-@domain grant is not an admin',
    })
    return null
  }
  if (seen.has(canon)) {
    errors.push({
      code: 'InvalidAdminList',
      path,
      message: `${canon} appears more than once in scope.admins`,
    })
    return null
  }
  return canon
}

/**
 * Validate `scope.outbound_roster` (ADR 0075): a list of `{address, class, note?}`
 * where `class` is the closed vocabulary. A whole-@domain grant at a public-mail
 * provider is rejected; a canonical address appearing under more than one class is
 * rejected. Same rules as the overlay validator (`bootstrap/validate.py`).
 *
 * An address on BOTH this roster and `scope.inbound_allow_from` is ALLOWED as of
 * ss#2263. It used to be rejected — "a recipient cannot be both internal and a
 * typed outbound class" — which read the reply list as a statement of class. It is
 * not one: it says who the Operator may autonomously REPLY to. Forbidding the
 * overlap meant a reply-authorized address could never carry a typed class, so a
 * firm's own client could only be made reply-able by leaving them classified as
 * staff (exempt from the content floor and the matter-identity gate), and the
 * gate's reply-lane branch was unreachable in every authorable config (ss#2271).
 * The overlap now resolves deterministically to the typed class: the runtime
 * classifier reads the typed roster first and falls back to the reply list only
 * where the typed roster is silent.
 */
function checkOutboundRoster(raw: unknown, errors: ValidationError[]): OutboundRosterEntry[] {
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'scope.outbound_roster',
      message: 'scope.outbound_roster must be a list',
    })
    return []
  }
  const seenClass = new Map<string, OutboundRosterClass>()
  const out: OutboundRosterEntry[] = []
  for (let i = 0; i < raw.length; i++) {
    const entry = checkOneOutboundEntry(raw[i], i, seenClass, errors)
    if (entry !== null) out.push(entry)
  }
  return out
}

function checkOneOutboundEntry(
  raw: unknown,
  i: number,
  seenClass: Map<string, OutboundRosterClass>,
  errors: ValidationError[]
): OutboundRosterEntry | null {
  const path = `scope.outbound_roster[${i}]`
  if (!isPlainObject(raw)) {
    errors.push({ code: 'TypeMismatch', path, message: 'outbound_roster entries must be objects' })
    return null
  }
  const address = raw['address']
  const cls = raw['class']
  const note = raw['note']
  if (typeof address !== 'string' || address.trim().length === 0) {
    errors.push({ code: 'MissingField', path: `${path}.address`, message: 'address is required' })
    return null
  }
  if (typeof cls !== 'string' || !(OUTBOUND_ROSTER_CLASSES as readonly string[]).includes(cls)) {
    errors.push({
      code: 'EnumViolation',
      path: `${path}.class`,
      message: `class must be one of: ${OUTBOUND_ROSTER_CLASSES.join(', ')}`,
    })
    return null
  }
  if (note !== undefined && note !== null && typeof note !== 'string') {
    errors.push({
      code: 'TypeMismatch',
      path: `${path}.note`,
      message: 'note must be a string when present',
    })
  }
  const canon = canonRosterAddress(address)
  const err = outboundAddressError(canon, cls, seenClass, path)
  if (err !== null) {
    errors.push(err)
    return null
  }
  seenClass.set(canon as string, cls as OutboundRosterClass)
  const entry: OutboundRosterEntry = { address: canon as string, class: cls as OutboundRosterClass }
  if (typeof note === 'string') entry.note = note
  return entry
}

/** The address-shape + collision checks, factored out to keep the entry checker
 * under the complexity ceiling. Returns the first violating error, or null. */
function outboundAddressError(
  canon: string | null,
  cls: string,
  seenClass: Map<string, OutboundRosterClass>,
  path: string
): ValidationError | null {
  const p = `${path}.address`
  if (canon === null) {
    return {
      code: 'InvalidOutboundRoster',
      path: p,
      message: 'address must be an exact address (local@domain) or an @domain grant',
    }
  }
  if (canon.startsWith('@') && PUBLIC_MAIL_DOMAINS.has(canon.slice(1))) {
    return {
      code: 'InvalidOutboundRoster',
      path: p,
      message: `a whole-@domain grant at a public-mail provider (${canon.slice(1)}) is not allowed; author the exact address`,
    }
  }
  const prior = seenClass.get(canon)
  if (prior !== undefined && prior !== cls) {
    return {
      code: 'InvalidOutboundRoster',
      path: p,
      message: `${canon} appears in more than one outbound roster class (${prior}, ${cls})`,
    }
  }
  return null
}

function emptyScope(): Scope {
  return {
    email_folders_visible: [],
    email_folders_blind: [],
    email_keyword_blocks: [],
    domain_blocks: [],
    matter_blocks: [],
    inbound_allow_from: [],
    outbound_roster: [],
    admins: [],
    rule_requests_to: [],
    ops_reply_from: [],
    staff_send_as: [],
  }
}

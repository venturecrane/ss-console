/**
 * Client identities stay in authored config; code and fixtures use synthetic data.
 *
 * THE LINE. Prose documentation MAY name a client: an ADR recording that a
 * decision came from the Ashton & Price pilot is honest engineering history,
 * and redacting it degrades the record. What must never appear outside
 * `operator/customers/` is client IDENTITY DATA -- an email domain or address
 * belonging to a real firm -- because that is personal data sitting in source,
 * in every clone, and in CI logs. The 2026-07-27 audit found real staff
 * addresses hardcoded in `test_recipient_classifier.py` and the firm's domain
 * in adversarial PI fixtures.
 *
 * THE BANNED SET IS DERIVED, NOT LISTED. It comes from the roster identities
 * actually authored in every `operator/customers/<slug>/customer.yaml`, minus
 * the domains WE own. So onboarding a new client extends this gate with no
 * code change, which is the only version of it that keeps working. A
 * hand-maintained list of banned strings protects the client you remembered.
 *
 * @see docs/adr/0081-repository-visibility.md
 */
import { describe, expect, it } from 'vitest'
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { extname, join, relative, resolve } from 'node:path'

const REPO_ROOT = resolve('.')
const CUSTOMERS_ROOT = resolve('operator/customers')

/**
 * Domains WE control. Everything else found in a roster is a third party whose
 * identity must not leak into code. Keep this list to domains the venture owns
 * or provisions; never add a client domain to silence a failure.
 */
const OUR_DOMAINS = new Set([
  'smd.services', // the venture
  'smdurgan.com', // the Captain
  'icloud.com', // the Captain's personal, used as a seat owner
  'agentmail.to', // provisioned Operator mailboxes
  'smd-staging.invalid', // staging seats
  'smdopslab.onmicrosoft.com', // our M365 lab tenant
  'venturecrane.com', // the parent org
])

/**
 * Roots scanned for leaked identity: source, tests, fixtures, scripts.
 *
 * `docs/` and `.stitch/` are scanned for identity DATA only (emails and roster
 * domains), which is all this gate matches; prose may still name a client (see
 * header). Added 2026-09-14 after a client domain was found in the decision stack
 * and two backlog snapshots, where the old roots could not see it.
 * `operator/customers/` is absent because that IS the authored config.
 */
const SCAN_ROOTS = ['src', 'tests', 'scripts', 'workers', 'bin', 'operator', 'docs', '.stitch']
const SCAN_EXCLUDE = [
  resolve('operator/customers'),
  resolve('tests/client-identity-gate.test.ts'), // this file names the allowlist
  resolve('node_modules'),
]
const TEXT_EXT = new Set([
  '.ts',
  '.tsx',
  '.js',
  '.mjs',
  '.cjs',
  '.astro',
  '.py',
  '.sh',
  '.json',
  '.yaml',
  '.yml',
  '.md',
  '.sql',
  '.txt',
])

const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g
const DOMAIN_ENTRY_RE = /^@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

function walk(dir: string): string[] {
  if (!existsSync(dir)) return []
  if (SCAN_EXCLUDE.some((ex) => dir === ex || dir.startsWith(ex + '/'))) return []
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (SCAN_EXCLUDE.some((ex) => full === ex)) return []
    if (name === 'node_modules' || name === '.git') return []
    let st
    try {
      st = statSync(full)
    } catch {
      return []
    }
    if (st.isDirectory()) return walk(full)
    return TEXT_EXT.has(extname(full)) ? [full] : []
  })
}

/** Every domain appearing in an authored roster, across every seat. */
function rosterDomains(): Map<string, string[]> {
  const found = new Map<string, string[]>()
  if (!existsSync(CUSTOMERS_ROOT)) return found

  for (const slug of readdirSync(CUSTOMERS_ROOT)) {
    const yaml = join(CUSTOMERS_ROOT, slug, 'customer.yaml')
    if (!existsSync(yaml)) continue
    const raw = readFileSync(yaml, 'utf-8')

    const domains = new Set<string>()
    for (const addr of raw.match(EMAIL_RE) ?? []) {
      domains.add(
        addr
          .split('@')[1]
          .toLowerCase()
          .replace(/[.,'"]+$/, '')
      )
    }
    // Whole-domain roster grants appear as bare `@domain` entries.
    for (const line of raw.split('\n')) {
      const m = line.match(/'?(@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'?\s*$/)
      if (m && DOMAIN_ENTRY_RE.test(m[1])) domains.add(m[1].slice(1).toLowerCase())
    }

    for (const d of domains) {
      if (OUR_DOMAINS.has(d)) continue
      found.set(d, [...(found.get(d) ?? []), slug])
    }
  }
  return found
}

const CLIENT_DOMAINS = rosterDomains()

describe('client identity gate', () => {
  it('derives a non-empty banned set from authored rosters (sanity)', () => {
    // If this ever empties, the gate below is scanning for nothing and would
    // pass no matter what leaked. That is the vacuous-pass failure class.
    expect(
      CLIENT_DOMAINS.size,
      'no third-party roster domain found in any customer.yaml -- either every seat is ours ' +
        '(then delete this gate) or the parser broke (then fix it); it must not silently scan for nothing'
    ).toBeGreaterThan(0)
  })

  it('no client roster domain appears in source, tests, fixtures, or scripts', () => {
    const files = SCAN_ROOTS.flatMap((r) => walk(resolve(r)))
    expect(files.length, 'scan found no files -- the roots are wrong').toBeGreaterThan(100)

    const violations: string[] = []
    for (const file of files) {
      const src = readFileSync(file, 'utf-8').toLowerCase()
      for (const [domain, slugs] of CLIENT_DOMAINS) {
        if (src.includes(domain)) {
          violations.push(
            `${relative(REPO_ROOT, file)} contains "${domain}" (authored roster of ${slugs.join(', ')})`
          )
        }
      }
    }

    expect(
      violations,
      'Client identity belongs in operator/customers/<slug>/customer.yaml, not in code or ' +
        'fixtures. Use a synthetic domain (firm.example) and synthetic names. Prose in docs/ ' +
        'may name a client; identity DATA may not.\n' +
        violations.join('\n')
    ).toEqual([])
  })
})

// ---------------------------------------------------------------------------
// Second assertion: fixture addresses are synthetic by construction.
//
// The gate above only catches domains we already know about from a roster. A
// fixture inventing `someone@realbusiness.com` would sail past it, so fixture
// addresses must also match a reserved-for-documentation pattern (RFC 2606 /
// RFC 6761) or a domain we own.
//
// Consumer-provider addresses are allowlisted individually: several PI fixtures
// model the firm's own clients, who realistically are on gmail/outlook, and
// swapping those for example.com would weaken the scenario. They are listed
// explicitly so the set cannot grow silently.
// ---------------------------------------------------------------------------
const SYNTHETIC_DOMAIN_RE =
  /(^|\.)(example\.(com|net|org)|firm\.example|flrm\.example|test|invalid|localhost)$/

const ALLOWED_CONSUMER_ADDRESSES = new Set([
  'ana.reyes88@gmail.com',
  'bob@gmail.com',
  'dana.whitfield@outlook.com',
  'delphine.kowalcyk@gmail.com',
  'dparker@gmail.com',
  'jane@gmail.com',
  'jane+x@gmail.com',
  'sarah.k.family@gmail.com',
  'tbryce@gmail.com',
])

/** Invented small-business domains used as fixture counterparties. */
const ALLOWED_FIXTURE_DOMAINS = new Set([
  'gwhitfield.com',
  'risenbakery.com',
  'kleinhardware.com',
  'radiology.com',
  'othercarrier.example',
  'opposingfirm.example',
  'vendor.example',
  'meridiancasualty.example', // the Alvarez fixture matter's carrier (drafting-lane fixtures)
  'barrowkestrel.example', // the Alvarez fixture matter's opposing counsel firm (drafting-lane fixtures)
])

describe('fixture addresses are synthetic', () => {
  const fixtureFiles = walk(resolve('operator/fixtures'))

  it('finds fixtures to scan (sanity)', () => {
    expect(fixtureFiles.length).toBeGreaterThan(0)
  })

  it('every fixture email uses a reserved, owned, or explicitly allowlisted domain', () => {
    const violations: string[] = []
    for (const file of fixtureFiles) {
      for (const addr of readFileSync(file, 'utf-8').match(EMAIL_RE) ?? []) {
        const lower = addr.toLowerCase()
        if (ALLOWED_CONSUMER_ADDRESSES.has(lower)) continue
        const domain = lower.split('@')[1].replace(/[.,'"]+$/, '')
        if (OUR_DOMAINS.has(domain)) continue
        if (ALLOWED_FIXTURE_DOMAINS.has(domain)) continue
        if (SYNTHETIC_DOMAIN_RE.test(domain)) continue
        violations.push(`${relative(REPO_ROOT, file)}: ${addr}`)
      }
    }
    expect(
      violations,
      'Fixture emails must use a reserved-for-documentation domain (example.com, *.test, ' +
        '*.invalid, firm.example), a domain we own, or an explicit allowlist entry above.\n' +
        violations.join('\n')
    ).toEqual([])
  })
})

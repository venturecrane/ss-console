/**
 * Validation of the per-connector fields the marketing seat's connectors read
 * (`mcp:wordpress`, capability Website; `mcp:ga4`, capability Analytics).
 *
 * Mirrors the overlay's `bootstrap/validate.py::_validate_marketing_connector`.
 * Same shape as the msgraph rule: the fields are REQUIRED on the backend that
 * reads them and FORBIDDEN on every other connector (no dead config, which
 * would look authored and do nothing).
 *
 *   mcp:wordpress  site_url  an https URL, the site root (no query, fragment,
 *                            or credentials). Basic auth over plain http would
 *                            put the application password on the wire.
 *                  username  the WordPress user the firm issued.
 *                  secret_ref  optional, `fly-secret:<ENV_NAME>`; provisioning
 *                            reads `<ENV_NAME>__<CUSTOMER_ID>` (default
 *                            WORDPRESS_APP_PASSWORD).
 *   mcp:ga4        property_id  the numeric GA4 property id (not the G- tag).
 */

import { MSGRAPH_SECRET_REF_PATTERN, type Connector, type ValidationError } from './types'

export const WORDPRESS_BACKEND = 'mcp:wordpress'
export const GA4_BACKEND = 'mcp:ga4'

const OWNED_FIELDS: ReadonlyArray<[string, string]> = [
  ['site_url', WORDPRESS_BACKEND],
  ['username', WORDPRESS_BACKEND],
  ['property_id', GA4_BACKEND],
]

export function isHttpsSiteRoot(raw: unknown): boolean {
  if (typeof raw !== 'string' || raw.trim() !== raw || raw.length === 0) return false
  let url: URL
  try {
    url = new URL(raw)
  } catch {
    return false
  }
  return (
    url.protocol === 'https:' &&
    url.hostname.length > 0 &&
    url.username === '' &&
    url.password === '' &&
    url.search === '' &&
    url.hash === '' &&
    !raw.includes('?') &&
    !raw.includes('#')
  )
}

type Issue = [field: string, message: string]

function ownershipIssues(backend: string, value: Record<string, unknown>): Issue[] {
  return OWNED_FIELDS.filter(
    ([field, owner]) => backend !== owner && value[field] !== undefined && value[field] !== null
  ).map(([field, owner]) => [
    field,
    `${field} is only valid when backend is "${owner}" (backend is "${backend}")`,
  ])
}

function wordpressIssues(value: Record<string, unknown>): Issue[] {
  const out: Issue[] = []
  if (!isHttpsSiteRoot(value['site_url'])) {
    out.push([
      'site_url',
      'site_url must be the site root as an https URL (no query, fragment or credentials)',
    ])
  }
  const username = value['username']
  if (typeof username !== 'string' || username.trim() === '' || /[\s:]/.test(username)) {
    out.push(['username', 'username must be the WordPress user name (no spaces or colons)'])
  }
  const ref = value['secret_ref']
  const refBad = typeof ref !== 'string' || !MSGRAPH_SECRET_REF_PATTERN.test(ref)
  if (ref !== undefined && ref !== null && refBad) {
    out.push([
      'secret_ref',
      'secret_ref must reference a per-seat Fly secret as "fly-secret:<ENV_NAME>"',
    ])
  }
  return out
}

function ga4Issues(value: Record<string, unknown>): Issue[] {
  const pid = value['property_id']
  const text = typeof pid === 'number' && Number.isInteger(pid) ? String(pid) : pid
  if (typeof text === 'string' && /^[0-9]{1,20}$/.test(text)) return []
  return [
    [
      'property_id',
      'property_id must be the numeric GA4 property id (Admin, Property details), not the G- measurement id',
    ],
  ]
}

/**
 * The validated marketing fields to carry on the connector value, present only
 * on the backend that owns them (an absent field stays absent). Call after
 * {@link checkMarketingConnectorFields} passed.
 */
export function marketingConnectorFields(
  backend: string,
  value: Record<string, unknown>
): Pick<Connector, 'site_url' | 'username' | 'secret_ref' | 'property_id'> {
  if (backend === WORDPRESS_BACKEND) {
    const ref = value['secret_ref']
    return {
      site_url: value['site_url'] as string,
      username: value['username'] as string,
      ...(typeof ref === 'string' ? { secret_ref: ref } : {}),
    }
  }
  if (backend === GA4_BACKEND) return { property_id: value['property_id'] as string | number }
  return {}
}

/** Returns false when the connector must be dropped. */
export function checkMarketingConnectorFields(
  key: string,
  backend: string,
  value: Record<string, unknown>,
  errors: ValidationError[]
): boolean {
  const issues = [
    ...ownershipIssues(backend, value),
    ...(backend === WORDPRESS_BACKEND ? wordpressIssues(value) : []),
    ...(backend === GA4_BACKEND ? ga4Issues(value) : []),
  ]
  for (const [field, message] of issues) {
    errors.push({ code: 'InvalidFormat', path: `connectors.${key}.${field}`, message })
  }
  return issues.length === 0
}

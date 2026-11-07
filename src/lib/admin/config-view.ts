/**
 * Config-display helpers for the admin Operator console configuration view
 * (`/admin/operator/[customer]/config`) — design §5.2.
 *
 * The configuration surface is SMD's read view of a client's authored
 * customer.yaml projection (personas, skills, scope, business hours, voice,
 * escalation). Authoring writes go through the config write path (the governance
 * surface already ships ceiling writes; broader field editing is a follow-on),
 * so this surface displays what is authored.
 *
 * Several projected sections are opaque JSON (`scope`, `business_hours`,
 * `escalation` are typed `unknown` in the frozen projection).
 * Rather than guess their shape and risk fabricating a rendering, this module
 * answers only the honest question the surface can answer from any value:
 * "is this section configured?" — presence, not invented structure.
 */

export type SectionPresence = 'configured' | 'not set'

/**
 * Is an opaque projected section configured? Null/undefined, an empty object,
 * and an empty array all read as "not set"; any populated value is "configured".
 * Pure and total — never throws on an unexpected shape.
 */
export function sectionPresence(value: unknown): SectionPresence {
  if (value === null || value === undefined) return 'not set'
  if (Array.isArray(value)) return value.length > 0 ? 'configured' : 'not set'
  if (typeof value === 'object') {
    return Object.keys(value).length > 0 ? 'configured' : 'not set'
  }
  // A scalar (string/number/bool) that isn't empty-string counts as configured.
  return value === '' ? 'not set' : 'configured'
}

export interface PresenceBadge {
  label: string
  classes: string
}

const BADGE_STRUCTURE =
  'inline-flex items-center px-2 py-0.5 rounded-[var(--ss-radius-badge)] ' +
  'text-[10px] font-medium uppercase tracking-wide whitespace-nowrap'

export function presenceBadge(p: SectionPresence): PresenceBadge {
  return p === 'configured'
    ? {
        label: 'Configured',
        classes: `${BADGE_STRUCTURE} bg-[color:var(--ss-color-complete)] text-white`,
      }
    : {
        label: 'Not set',
        classes: `${BADGE_STRUCTURE} bg-[color:var(--ss-color-border)] text-[color:var(--ss-color-text-secondary)]`,
      }
}

/** "warm, professional" from a persona tone array, or "—" when none. */
export function toneSummary(tone: readonly string[] | null | undefined): string {
  if (!tone || tone.length === 0) return '—'
  return tone.join(', ')
}

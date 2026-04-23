/**
 * Admin-facing relative-time formatter. Renders short tokens suitable for
 * inline list metadata ("2d ago", "3h ago", "just now").
 *
 * Centralized so admin surfaces don't keep redefining inline variants — the
 * pattern that was drifting across src/pages/admin/index.astro and the
 * generators dashboard before this util existed. Portal-facing date captions
 * live in src/lib/portal/formatters.ts; keep admin and portal formatters
 * separate because their wording and conventions differ.
 *
 * Returns null for invalid / missing input so callers can chain without
 * guarding — and render nothing rather than a placeholder.
 */

export function relativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null
  const d = new Date(iso)
  const ms = d.getTime()
  if (Number.isNaN(ms)) return null

  const hours = (Date.now() - ms) / 3_600_000
  if (hours < 0) return 'just now' // future timestamp — treat as current
  if (hours < 1) return 'just now'
  if (hours < 24) return `${Math.round(hours)}h ago`
  const days = Math.round(hours / 24)
  return `${days}d ago`
}

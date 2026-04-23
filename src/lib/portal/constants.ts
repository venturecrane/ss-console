/**
 * Shared constants for the client portal.
 *
 * Historic label/color maps for client-facing engagement status. The
 * canonical client-friendly label + tone resolver now lives in
 * `./status.ts` (`resolveEngagementLabel` / `resolveEngagementTone`).
 * These maps are kept for any legacy callers and should not be added
 * to — new portal surfaces route through `status.ts`.
 */

/** Client-friendly engagement status labels.
 *  `safety_net` → "Stabilization" per Decision #27 — the raw DB enum
 *  read as jargon on portal surfaces. Other entries preserve the
 *  original client-facing copy. */
export const CLIENT_STATUS_LABELS: Record<string, string> = {
  scheduled: 'Starting Soon',
  active: 'Underway',
  handoff: 'Wrapping Up',
  safety_net: 'Stabilization',
  completed: 'Complete',
  cancelled: 'Cancelled',
}

/** Tailwind classes for engagement status badges. */
export const CLIENT_STATUS_COLORS: Record<string, string> = {
  scheduled: 'bg-slate-100 text-slate-600',
  active: 'bg-blue-100 text-blue-800',
  handoff: 'bg-amber-100 text-amber-800',
  safety_net: 'bg-green-100 text-green-800',
  completed: 'bg-emerald-100 text-emerald-700',
  cancelled: 'bg-slate-100 text-slate-500',
}

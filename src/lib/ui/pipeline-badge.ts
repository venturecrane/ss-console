/**
 * Tailwind class list for a source-pipeline badge. Used anywhere we need
 * to render which lead-gen pipeline (review mining, job monitor, etc.)
 * produced an entity. Shared between the entity list and entity detail
 * surfaces so both read the same colored-badge treatment — see
 * docs/style/UI-PATTERNS.md Rule 5 (typography scale) and Rule 7 (shared
 * primitives for repeated patterns).
 *
 *   <span class={pipelineBadgeClass(entity.source_pipeline)}>
 *     {PIPELINE_LABELS[entity.source_pipeline] ?? entity.source_pipeline}
 *   </span>
 */

const STRUCTURE = 'text-xs px-2 py-0.5 rounded'

const TONE: Record<string, string> = {
  review_mining: 'bg-amber-100 text-amber-700',
  job_monitor: 'bg-blue-100 text-blue-700',
  new_business: 'bg-green-100 text-green-700',
  social_listening: 'bg-purple-100 text-purple-700',
  website_booking: 'bg-indigo-100 text-indigo-700',
  website_scorecard: 'bg-violet-100 text-violet-700',
  website_intake: 'bg-teal-100 text-teal-700',
}

const FALLBACK = 'bg-[color:var(--color-border-subtle)] text-[color:var(--color-text-secondary)]'

export function pipelineBadgeClass(pipeline: string): string {
  return `${STRUCTURE} ${TONE[pipeline] ?? FALLBACK}`
}

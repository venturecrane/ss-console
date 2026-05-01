/**
 * Canonical enrichment module list. Single source of truth used by both the
 * pipeline (src/lib/enrichment/index.ts) and the admin UI panel
 * (src/components/admin/EnrichmentStatusPanel.astro). When the pipeline
 * grows a module, add it here so the panel renders it as `not_yet_run`
 * instead of silently omitting it.
 *
 * `tier` is informational — the runtime ordering still lives in
 * enrichEntity(). It exists so the UI can group modules visually.
 */

export const MODULES = [
  { id: 'google_places', tier: 1, displayName: 'Google Places' },
  { id: 'website_analysis', tier: 1, displayName: 'Website Analysis' },
  { id: 'outscraper', tier: 1, displayName: 'Outscraper' },
  { id: 'acc_filing', tier: 1, displayName: 'ACC Filing' },
  { id: 'roc_license', tier: 1, displayName: 'ROC License' },
  { id: 'review_analysis', tier: 2, displayName: 'Review Analysis' },
  { id: 'competitors', tier: 2, displayName: 'Competitors' },
  { id: 'news_search', tier: 2, displayName: 'News Search' },
  { id: 'deep_website', tier: 3, displayName: 'Deep Website' },
  { id: 'review_synthesis', tier: 3, displayName: 'Review Synthesis' },
  { id: 'linkedin', tier: 3, displayName: 'LinkedIn' },
  { id: 'intelligence_brief', tier: 3, displayName: 'Intelligence Brief' },
  { id: 'outreach_draft', tier: 3, displayName: 'Outreach Draft' },
] as const

export type ModuleId = (typeof MODULES)[number]['id']

export const MODULE_IDS: ModuleId[] = MODULES.map((m) => m.id)

export function isModuleId(value: string): value is ModuleId {
  return (MODULE_IDS as readonly string[]).includes(value)
}

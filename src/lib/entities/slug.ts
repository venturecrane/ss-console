/**
 * Slug computation for entity dedup.
 *
 * Normalizes business names into URL-safe slugs for UNIQUE(org_id, slug)
 * dedup. Slug is name-only as of 2026-05-18 (#751 bug 2).
 *
 * History. Pre-2026-05-18 the slug included the area as a disambiguator
 * ("pirtek-goodyear-az"). That worked when geography was Phoenix-only
 * and area strings were stable. After ADR 0003 made reach statewide,
 * the area in the slug stopped being a meaningful discriminator AND
 * SerpAPI started returning drifting location strings for the same
 * business ("Phoenix, AZ" vs "Phoenix, AZ, Estados Unidos" vs "AZ"),
 * which created N entities for the same business across cron runs.
 * Slug is now name-only; genuine collisions (two different "Joe's
 * Plumbing" businesses in AZ) get caught by the fuzzy-match logger
 * and resolved by the admin Merge action.
 *
 * Known limitation: genuinely different name variants for the same
 * business (e.g., "AZ Comfort Solutions" vs "Arizona Comfort Solutions")
 * will still produce different slugs. The Jaro-Winkler fuzzy-match
 * logger catches these as `candidate_merge_log` rows for admin review.
 */

/** Common business suffixes stripped during normalization. */
const SUFFIX_PATTERN =
  /\b(llc|l\.l\.c|inc|corp|co|company|corporation|incorporated|limited|llp|ltd|pllc|pc|p\.c|lp|plc|dba)\b\.?/gi

/** Parenthetical location/description info stripped from names. */
const PAREN_PATTERN = /\s*\(.*?\)\s*/g

/**
 * Compute a normalized slug from a business name.
 *
 * The `area` parameter is accepted for backwards compatibility with
 * existing callers but no longer influences the slug. See file-level
 * doc comment for history.
 *
 * Examples:
 *   computeSlug("PIRTEK (Goodyear, AZ – Franchise Location)")
 *     → "pirtek"
 *   computeSlug("ProGuard Roofing LLC", "Phoenix, AZ")
 *     → "proguard-roofing"
 *   computeSlug("Smith & Sons Plumbing")
 *     → "smith-sons-plumbing"
 */
export function normalizeBusinessName(name: string): string {
  let s = name.toLowerCase()

  s = s.replace(PAREN_PATTERN, ' ')
  s = s.replace(SUFFIX_PATTERN, '')
  s = s.replace(/\baz\b/g, 'arizona')
  s = s.replace(/[^a-z0-9\s-]/g, ' ')
  return s.trim().replace(/\s+/g, ' ').replace(/-+/g, '-')
}

export function computeSlug(name: string, _area?: string | null): string {
  return normalizeBusinessName(name).replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

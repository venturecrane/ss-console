/**
 * Slug computation for entity dedup.
 *
 * Normalizes business names into URL-safe slugs for UNIQUE(org_id, slug)
 * dedup. When area is provided, it's appended for location disambiguation
 * (e.g., two PIRTEK franchises in different cities).
 *
 * Known limitation: genuinely different name variants for the same business
 * (e.g., "AZ Comfort Solutions" vs "Arizona Comfort Solutions") will create
 * separate entities. Handle with admin merge action at the UI layer.
 */

/** Common business suffixes stripped during normalization. */
const SUFFIX_PATTERN =
  /\b(llc|inc|corp|ltd|co|company|corporation|incorporated|limited|pllc|lp|plc|dba)\b\.?/gi

/** Parenthetical location/description info stripped from names. */
const PAREN_PATTERN = /\s*\(.*?\)\s*/g

/**
 * Compute a normalized slug from a business name and optional area.
 *
 * Examples:
 *   computeSlug("PIRTEK (Goodyear, AZ – Franchise Location)", "Goodyear, AZ")
 *     → "pirtek-goodyear-az"
 *   computeSlug("ProGuard Roofing LLC", "Phoenix, AZ")
 *     → "proguard-roofing-phoenix-az"
 *   computeSlug("Smith & Sons Plumbing")
 *     → "smith-sons-plumbing"
 */
export function computeSlug(name: string, area?: string | null): string {
  let s = name.toLowerCase()

  // Strip parentheticals: "PIRTEK (Goodyear, AZ – Franchise)" → "pirtek"
  s = s.replace(PAREN_PATTERN, ' ')

  // Strip common business suffixes
  s = s.replace(SUFFIX_PATTERN, '')

  // Remove non-alphanumeric (keep spaces and hyphens for now)
  s = s.replace(/[^a-z0-9\s-]/g, '')

  // Collapse whitespace to hyphens
  s = s.trim().replace(/\s+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')

  // Append normalized area for location disambiguation
  if (area) {
    const a = area
      .toLowerCase()
      .replace(/[^a-z0-9\s]/g, '')
      .trim()
      .replace(/\s+/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
    if (a) {
      s = `${s}-${a}`
    }
  }

  return s
}

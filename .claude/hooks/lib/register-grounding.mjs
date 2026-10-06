/**
 * register-grounding.mjs -- the quote gate the obligation register rests on.
 *
 * Split out of register.mjs (2026-09-25) so the CLI fits the repo's 500-line
 * ceiling once lint reached `.claude/**`. Pure string functions: no files, no
 * git, no database, so every refusal is testable on its own. register.mjs
 * re-exports all of it, and callers keep importing from there.
 *
 * Extraction faithfulness tops out around 0.83 -- roughly one statement in six
 * is unsupported by its source. No amount of prompting fixes that, so the
 * control is mechanical: every row carries a verbatim quote, the quote is
 * string-matched against the source, and a quote that is not found is REFUSED.
 */

export const MIN_QUOTE_WORDS = 6

/**
 * Normalize for comparison.
 *
 * Both sides get the same treatment, so a quote that differs from its source
 * only in line wrapping, smart quotes, or markdown emphasis still matches. A
 * quote spanning several lines matches for free: the newline has already
 * become a space by the time we compare.
 *
 * Deliberately NOT stripping punctuation wholesale -- a quote that matches
 * only after its commas are removed is a paraphrase, and paraphrase is exactly
 * what this gate exists to reject.
 */
export function normalize(text) {
  return String(text)
    .normalize('NFKC')
    .replace(/[‘’‛′]/g, "'")
    .replace(/[“”‟″]/g, '"')
    .replace(/[‐-―−]/g, '-')
    .replace(/[*_`]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase()
}

export function wordCount(text) {
  const t = normalize(text)
  return t.length === 0 ? 0 : t.split(' ').length
}

/**
 * Is this quote actually in this source?
 *
 * On failure, returns the highest-overlap window from the source so the drift
 * is visible. An agent that mis-clipped a sentence by two words should be able
 * to see that immediately rather than guess.
 */
export function checkQuote(sourceText, quote) {
  if (wordCount(quote) < MIN_QUOTE_WORDS) {
    return { ok: false, error: 'quote_too_short', words: wordCount(quote) }
  }
  const haystack = normalize(sourceText)
  const needle = normalize(quote)
  if (haystack.includes(needle)) return { ok: true }
  return { ok: false, error: 'quote_not_found', nearest: nearestWindow(haystack, needle) }
}

function nearestWindow(haystack, needle) {
  const terms = needle.split(' ').filter((w) => w.length > 3)
  if (terms.length === 0) return null
  const words = haystack.split(' ')
  const span = needle.split(' ').length
  let best = { score: 0, at: -1 }
  for (let i = 0; i + span <= words.length; i += Math.max(1, Math.floor(span / 4))) {
    const window = words.slice(i, i + span).join(' ')
    const score = terms.filter((term) => window.includes(term)).length
    if (score > best.score) best = { score, at: i }
  }
  if (best.at < 0) return null
  return words
    .slice(best.at, best.at + span * 2)
    .join(' ')
    .slice(0, 200)
}

const MONTHS = [
  'january',
  'february',
  'march',
  'april',
  'may',
  'june',
  'july',
  'august',
  'september',
  'october',
  'november',
  'december',
]

/**
 * The forms a letter might legitimately use to state one date.
 *
 * A client writes "before October 15th", not "2026-10-15". An earlier version
 * required the raw --due string to appear verbatim in the quote, which refused
 * every properly-grounded obligation whose letter used ordinary English -- a
 * false refusal on exactly the cases this gate exists to admit. The gate must
 * be strict about whether the date is ANCHORED in the source, never about the
 * client's formatting.
 *
 * Returns the accepted spellings; the caller passes if the quote contains any.
 * A non-ISO --due (a recurring anchor like "the 15th") is matched literally,
 * which is the right behaviour for a duty with no calendar date.
 */
export function dateQuoteForms(due) {
  const raw = normalize(due)
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw)
  if (!iso) return [raw]

  const month = MONTHS[Number(iso[2]) - 1]
  const day = String(Number(iso[3]))
  const year = iso[1]
  if (!month) return [raw]

  return [
    raw, // 2026-10-15
    `${month} ${day}`, // october 15
    `${month} ${day}st`,
    `${month} ${day}nd`,
    `${month} ${day}rd`,
    `${month} ${day}th`, // october 15th
    `${day} ${month}`, // 15 october
    `${month} ${day}, ${year}`, // october 15, 2026
    `${iso[2]}/${day}/${year}`, // 10/15/2026
    `${iso[2]}/${iso[3]}/${year}`,
  ]
}

function escapeRegExp(text) {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Does the date quote anchor the due date in the source's own words?
 *
 * This is the only guard on an obligation's due_at, and a dated row alerts the
 * Captain, so it must not be satisfied by a near miss (review 2026-10-06 N7).
 * An earlier version used plain substring containment, under which "october 1"
 * anchored inside "october 15" and a year-less form anchored any year.
 *
 * The rule, stated precisely:
 *
 * 1. Word boundaries. A form matches only where it is not glued to a letter or
 *    digit on either side. So "october 1" does not match inside "october 15",
 *    and "10/15/2026" does not match inside "110/15/20260".
 * 2. Adjacent year must agree. When a matched form carries no year and the
 *    quote states a year right after it ("october 15, 2025", "15 october 2025"),
 *    that year must equal the due date's year, or this occurrence does not
 *    anchor the date.
 * 3. A year-less match is refused when the quote names a conflicting year
 *    elsewhere. If the quote names any four-digit year and none of them is the
 *    due date's year, a year-less match is refused: the quote is about a
 *    different year, and "october 15" alone cannot be read as this one. A quote
 *    that names no year at all still anchors on the month and day, because
 *    letters routinely omit the current year.
 *
 * Each occurrence is judged on its own; the quote passes if any occurrence
 * passes. A non-ISO --due (a recurring anchor like "the 15th") keeps the
 * word-boundary match and skips the year rules, since it names no year.
 */
export function dateQuoteAnchorsDate(dateQuote, due) {
  const quote = normalize(dateQuote)
  const iso = /^(\d{4})-(\d{2})-(\d{2})$/.exec(normalize(due))
  const dueYear = iso ? iso[1] : null
  const yearsNamed = quote.match(/(?<![\p{L}\p{N}])(?:19|20)\d{2}(?![\p{L}\p{N}])/gu) ?? []
  const conflictingYearElsewhere =
    dueYear !== null && yearsNamed.length > 0 && !yearsNamed.includes(dueYear)

  return dateQuoteForms(due).some((form) => {
    const formHasYear = dueYear !== null && form.includes(dueYear)
    const pattern = new RegExp(
      `(?<![\\p{L}\\p{N}])${escapeRegExp(form)}(?![\\p{L}\\p{N}])`,
      'gu',
    )
    for (const match of quote.matchAll(pattern)) {
      if (formHasYear || dueYear === null) return true
      const after = quote.slice(match.index + match[0].length)
      const adjacent = /^,? ?(\d{4})(?!\d)/.exec(after)
      if (adjacent) {
        if (adjacent[1] === dueYear) return true
        continue
      }
      if (!conflictingYearElsewhere) return true
    }
    return false
  })
}

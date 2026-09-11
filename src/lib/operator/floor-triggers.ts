/**
 * Content-floor trigger vocabulary, for AUTHORING-TIME refusal of signature
 * text (PR #2651 review, finding 3).
 *
 * The runtime floor is ADR 0031, enforced by the overlay
 * (hermes-smd-overlay `shared/content_floor.py`): an outbound chase body
 * carrying a trigger word (the LEGAL patterns include \battorney\b and
 * \bcounsel\b) is HELD as a draft even under an authored autonomous send. The
 * chase signature block renders `customer_name` / `personas[].signature`
 * VERBATIM into every chase body, so a firm authored as "X, Attorneys at Law"
 * would have every autonomous chase silently downgrade to a held draft -- a
 * config choice that quietly breaks the auto-handle commitment (#1878's
 * failure class, entering through the sign-off instead of the body).
 *
 * So the words are refused where they are AUTHORED, with a corrective message,
 * instead of discovered as a mysterious pile of held drafts.
 *
 * SOURCE OF TRUTH for the word list: the substitution table in
 * `operator/verticals/law-firm/addons/pi/references/_shared-chase-voice.md`
 * ("Floor-clean by construction"), which mirrors the overlay's
 * content_floor.py categories. The overlay file lives in the other repo, so
 * the drift guard here is against the in-repo table:
 * tests/chase-voice-signature.test.ts asserts every word below appears in that
 * table's "Do not write" column and that the table's single-word entries all
 * appear below. A word added to the floor updates the table, this list, and
 * the overlay pattern in the same change.
 *
 * Word-boundary matching with an optional plural `s`: the overlay's
 * \battorney\b would not literally match "attorneys", but "Attorneys at Law"
 * still puts the firm one singular reference away from the floor, and an
 * authoring gate that lets the plural through teaches authors the word is
 * fine. Substrings do NOT match ("Signal Hill" is not "sign";
 * "Fee" does not hide in "Coffee").
 */

/** Single-word triggers, one per floor category the table names. */
export const FLOOR_TRIGGER_WORDS: readonly string[] = [
  // signature category
  'sign',
  'signed',
  'signature',
  'signing',
  // deadline category
  'deadline',
  // legal category (the overlay's LEGAL patterns)
  'attorney',
  'counsel',
  'legal',
  // contract category
  'agreement',
  'contract',
  // money category
  'invoice',
  'payment',
  'fee',
] as const

const FLOOR_TRIGGER_PATTERN = new RegExp(`\\b(${FLOOR_TRIGGER_WORDS.join('|')})s?\\b`, 'i')

/**
 * The first floor-trigger word found in `text`, normalized to its listed
 * form (the optional plural `s` sits outside the capture group); null when
 * the text is floor-clean.
 */
export function findFloorTrigger(text: string): string | null {
  const match = FLOOR_TRIGGER_PATTERN.exec(text)
  return match === null ? null : match[1].toLowerCase()
}

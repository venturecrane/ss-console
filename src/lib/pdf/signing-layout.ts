/**
 * Signing page layout constants.
 *
 * These values define the geometry of the SOW's dedicated signing page (Page 3)
 * and are consumed in two places:
 *
 *   1. The Forme template (sow-template.tsx) — positions the signing block
 *   2. The SignWell field config (field-config.ts) — places interactive fields
 *
 * CRITICAL: If you change these values, the template and the SignWell fields
 * move together. That is the entire point of this module.
 *
 * ## How coordinates were measured
 *
 * The y-values below were measured from an actual rendered PDF, NOT calculated
 * from font metrics. Procedure:
 *
 *   1. Generate a test SOW via renderSow() or the admin UI
 *   2. Open the PDF in Preview > Tools > Show Inspector (or any PDF coordinate tool)
 *   3. Hover over the top-left corner of the signing space on page 3
 *   4. Read the y-coordinate (Preview uses bottom-left origin — convert to
 *      top-left by subtracting from PAGE_HEIGHT)
 *   5. Update the values below
 *
 * Re-measure whenever the signing page layout changes.
 *
 * ## SignWell coordinate system
 *
 * Based on empirical testing:
 * - Origin: top-left of each page (0,0 = top-left corner)
 * - Units: PDF points (1/72 inch)
 * - Pages: 1-indexed (page 1 = first page)
 *
 * @see docs/templates/sow-template.md — Section 5.4 (signing page layout)
 */

// ---------------------------------------------------------------------------
// Page geometry
// ---------------------------------------------------------------------------

export const PAGE_SIZE = { width: 612, height: 792 } as const // US Letter

export const PAGE_MARGINS = {
  top: 54, // 0.75in
  bottom: 54,
  left: 72, // 1in
  right: 72,
} as const

/** Printable area width: page width minus left and right margins */
export const PRINTABLE_WIDTH = PAGE_SIZE.width - PAGE_MARGINS.left - PAGE_MARGINS.right // 468pt

// ---------------------------------------------------------------------------
// Signing page layout
// ---------------------------------------------------------------------------

/** Gap between the two signature columns (CLIENT / SMD SERVICES) */
const COLUMN_GAP = 36

/** Width of each signature column: half of printable width minus gap */
const COLUMN_WIDTH = (PRINTABLE_WIDTH - COLUMN_GAP) / 2 // 216pt

export const SIGNING_PAGE = {
  /** 1-indexed page number where the signing block lives */
  pageNumber: 3,

  /** Height of the empty space reserved for SignWell signature overlay */
  signingSpaceHeight: 60,

  /** Gap between the two signature columns */
  columnGap: COLUMN_GAP,

  /** Width of each signature column */
  columnWidth: COLUMN_WIDTH,

  // ---------------------------------------------------------------------------
  // SignWell field coordinates
  //
  // Initial values derived from the page 3 layout, which has ZERO variable
  // content — every element is fixed-length static text. This makes the
  // y-positions deterministic regardless of SOW content on pages 1-2.
  //
  // Layout (from page top):
  //   54pt  top margin
  //  ~28pt  NEXT STEPS heading (12pt font + 12pt marginBottom + padding/border)
  //  ~52pt  preamble text (2 lines × 14pt + 24pt marginBottom)
  //  ~28pt  AGREEMENT heading (same as NEXT STEPS)
  //  ~44pt  agreement text (2 lines × 14pt + 16pt marginBottom)
  //  ~12pt  "CLIENT" label (9pt font + spacing)
  //   = ~218pt cumulative — signing space starts here
  //  60pt   empty signing space (signingSpaceHeight)
  //   5pt   divider + margin
  //  ~24pt  contact name + optional title
  //  ~16pt  date field area
  //
  // IMPORTANT: These values MUST be verified against the actual rendered PDF
  // before production use. See "How coordinates were measured" above.
  // ---------------------------------------------------------------------------

  /** CLIENT signature field (left column) */
  clientSignature: {
    x: PAGE_MARGINS.left,
    y: 200,
    width: 200,
    height: 50,
  },

  /** CLIENT date field (left column, below signature) */
  clientDate: {
    x: PAGE_MARGINS.left,
    y: 293,
    width: 120,
    height: 20,
  },

  /** SMD SERVICES signature field (right column) — reserved for future use */
  smdSignature: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 200,
    width: 200,
    height: 50,
  },

  /** SMD SERVICES date field (right column) — reserved for future use */
  smdDate: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 293,
    width: 120,
    height: 20,
  },
} as const

/**
 * Signing page layout constants.
 *
 * These values define the geometry of the SOW's dedicated signing page (Page 3)
 * and are consumed in two places:
 *
 *   1. The Forme template (sow-template.tsx) — positions the signing block
 *   2. The text tag injection (inject-signing-tags.ts) — places SignWell tags
 *
 * CRITICAL: If you change these values, the template and the text tags
 * move together. That is the entire point of this module.
 *
 * ## How tag positions were measured
 *
 * The y-values below were measured from the actual Forme-rendered PDF using
 * pdfplumber (not calculated from font metrics):
 *
 *   python3: pdfplumber.open("sow.pdf").pages[2].extract_words()
 *   CLIENT label bottom: y=204.5pt from page top
 *   Date text top:       y=302.1pt from page top
 *
 * Re-measure whenever the signing page layout changes.
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
  // Text tag injection positions (top-left origin, PDF points)
  //
  // These are the positions where pdf-lib injects SignWell text tags.
  // Measured from the actual Forme-rendered PDF via pdfplumber.
  // ---------------------------------------------------------------------------

  /** Where to inject text tags (top-left origin, converted to bottom-left by inject-signing-tags.ts) */
  tagInjection: {
    /** Signature tag position: below CLIENT label, in the 60pt signing space */
    signature: { x: PAGE_MARGINS.left, y: 215 },
    /** Date tag position: at the "Date: ___" text area */
    date: { x: PAGE_MARGINS.left, y: 305 },
  },

  /** SignWell field dimensions (specified in text tag options) */
  sigFieldWidth: 200,
  sigFieldHeight: 50,
  dateFieldWidth: 120,
  dateFieldHeight: 20,

  // ---------------------------------------------------------------------------
  // Coordinate-based fallback (kept per critique recommendation)
  //
  // If text tags fail, sign.ts can fall back to explicit field coordinates.
  // These are the same measured positions used for tag injection.
  // ---------------------------------------------------------------------------

  /** CLIENT signature field (left column) */
  clientSignature: {
    x: PAGE_MARGINS.left,
    y: 259, // From PoC: SignWell auto-detected text tag at y=258.77 — correct position
    width: 200,
    height: 40,
  },

  /** CLIENT date field (left column) */
  clientDate: {
    x: PAGE_MARGINS.left,
    y: 394, // From PoC: SignWell auto-detected text tag at y=393.74 — correct position
    width: 120,
    height: 16,
  },

  /** SMD SERVICES signature field (right column) — reserved for future use */
  smdSignature: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 259,
    width: 200,
    height: 40,
  },

  /** SMD SERVICES date field (right column) — reserved for future use */
  smdDate: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 394,
    width: 120,
    height: 16,
  },
} as const

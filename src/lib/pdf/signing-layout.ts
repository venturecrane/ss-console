/**
 * Signing page layout constants.
 *
 * These values define the geometry of the SOW's dedicated signing page (Page 3)
 * and are consumed in two places:
 *
 *   1. The Forme template (sow-template.tsx) — positions the signing block
 *   2. The sign.ts API route — passes field coordinates to SignWell
 *
 * CRITICAL: If you change these values, the template and the SignWell fields
 * move together. That is the entire point of this module.
 *
 * ## SignWell coordinate system
 *
 * SignWell field coordinates are PDF points (72 DPI) with top-left page origin,
 * mapping 1:1 to the PDF's coordinate space. Verified empirically by creating
 * a calibration document with fields at known positions.
 *
 * The field positions below were determined by visual inspection of the
 * SignWell signing view against the actual Forme-rendered 3-page SOW PDF.
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
  // SignWell field coordinates (top-left origin, PDF points)
  //
  // SignWell coordinates map 1:1 to PDF points with top-left page origin.
  // Verified empirically: created a calibration document with fields at
  // known y positions (50, 150, 250, 350, 450, 600) and confirmed fields
  // land at exactly those PDF-point positions.
  //
  // Previous values (y=259, y=394) were measured from text-tag auto-detection
  // in a PoC and landed at the CLIENT label instead of in the signing space.
  // Corrected by visual inspection of the signing view: the CLIENT label is
  // at ~y=259, so the signing space below it starts at ~y=275.
  // ---------------------------------------------------------------------------

  /** CLIENT signature field (left column) — in the 60pt signing space below CLIENT label */
  clientSignature: {
    x: PAGE_MARGINS.left,
    y: 280,
    width: 200,
    height: 50,
  },

  /** CLIENT date field (left column) — at the "Date: ___" line */
  clientDate: {
    x: PAGE_MARGINS.left,
    y: 400,
    width: 120,
    height: 20,
  },

  /** SMD SERVICES signature field (right column) — reserved for future use */
  smdSignature: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 280,
    width: 200,
    height: 50,
  },

  /** SMD SERVICES date field (right column) — reserved for future use */
  smdDate: {
    x: PAGE_MARGINS.left + COLUMN_WIDTH + COLUMN_GAP,
    y: 400,
    width: 120,
    height: 20,
  },
} as const

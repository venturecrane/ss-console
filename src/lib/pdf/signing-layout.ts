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

/** Width of the client acceptance block on the dedicated signing page */
const SIGNATURE_BLOCK_WIDTH = 216

export const SIGNING_PAGE = {
  /** 1-indexed page number where the signing block lives */
  pageNumber: 3,

  /** Height of the empty space reserved for SignWell signature overlay */
  signingSpaceHeight: 60,

  /** Width of the client acceptance block */
  columnWidth: SIGNATURE_BLOCK_WIDTH,

  // ---------------------------------------------------------------------------
  // PDF field coordinates (top-left origin, PDF points)
  //
  // Page 3 geometry from the rendered PDF:
  // - CLIENT baseline: top ~202.6
  // - Client underline: top ~266.2
  // - Printed signer name baseline: top ~280.2
  // - Printed title baseline: top ~293.0
  // - Printed date baseline: top ~308.4
  //
  // These remain the canonical PDF-space coordinates for the dedicated
  // signing page. Provider-specific coordinate transforms happen at the
  // provider boundary, not here.
  // ---------------------------------------------------------------------------

  /** CLIENT signature field (left column) in PDF points */
  clientSignature: {
    x: PAGE_MARGINS.left,
    y: 214,
    width: SIGNATURE_BLOCK_WIDTH,
    height: 44,
  },

  /** CLIENT date field (left column) in PDF points */
  clientDate: {
    x: PAGE_MARGINS.left + 24,
    y: 300,
    width: 96,
    height: 18,
  },
} as const

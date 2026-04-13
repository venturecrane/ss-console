/**
 * SignWell field placement configurations.
 *
 * signing-layout.ts defines field rectangles in PDF points.
 * SignWell's signer runtime renders PDF pages onto a 96 DPI raster, so
 * we convert the 72 DPI PDF coordinates into SignWell's page-image space
 * at the provider boundary.
 *
 * Each document type exports a function that returns field placements
 * for a given signer. This pattern scales to future document types
 * (proposals, invoices) by adding new functions here and new layout
 * constants in signing-layout.ts.
 *
 * @see src/lib/pdf/signing-layout.ts — layout constants (single source of truth)
 */

import { SIGNING_PAGE } from '../pdf/signing-layout'
import type { SignWellField } from './types'

export interface SigningFieldConfig {
  signature: Omit<SignWellField, 'required' | 'api_id'>
  date: Omit<SignWellField, 'required' | 'api_id'>
}

export const PDF_POINTS_PER_INCH = 72
export const SIGNWELL_RENDER_DPI = 96
export const SIGNWELL_COORDINATE_SCALE = SIGNWELL_RENDER_DPI / PDF_POINTS_PER_INCH

function toSignWellUnits(value: number): number {
  return Math.round(value * SIGNWELL_COORDINATE_SCALE)
}

function toSignWellField(
  field: Pick<SignWellField, 'type' | 'page' | 'x' | 'y' | 'width' | 'height'>
): Omit<SignWellField, 'required' | 'api_id'> {
  return {
    type: field.type,
    page: field.page,
    x: toSignWellUnits(field.x),
    y: toSignWellUnits(field.y),
    width: field.width != null ? toSignWellUnits(field.width) : undefined,
    height: field.height != null ? toSignWellUnits(field.height) : undefined,
  }
}

/**
 * Get SignWell field placements for the SOW document.
 *
 * The CLIENT signature and date fields are placed on the left column
 * of the dedicated signing page's AGREEMENT section (page 3).
 *
 * The layout constants remain in PDF points. This function converts them
 * into the coordinate space SignWell uses in the signer runtime.
 */
export function getSowSigningFields(): SigningFieldConfig {
  return {
    signature: toSignWellField({
      type: 'signature',
      page: SIGNING_PAGE.pageNumber,
      x: SIGNING_PAGE.clientSignature.x,
      y: SIGNING_PAGE.clientSignature.y,
      width: SIGNING_PAGE.clientSignature.width,
      height: SIGNING_PAGE.clientSignature.height,
    }),
    date: toSignWellField({
      type: 'date',
      page: SIGNING_PAGE.pageNumber,
      x: SIGNING_PAGE.clientDate.x,
      y: SIGNING_PAGE.clientDate.y,
      width: SIGNING_PAGE.clientDate.width,
      height: SIGNING_PAGE.clientDate.height,
    }),
  }
}

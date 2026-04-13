import { describe, it, expect } from 'vitest'
import {
  getSowSigningFields,
  PDF_POINTS_PER_INCH,
  SIGNWELL_RENDER_DPI,
  SIGNWELL_COORDINATE_SCALE,
} from '../src/lib/signwell/field-config'
import { PAGE_SIZE, PAGE_MARGINS, SIGNING_PAGE } from '../src/lib/pdf/signing-layout'

describe('signwell: SOW signing field coordinates', () => {
  const fields = getSowSigningFields()

  it('signature field is on the dedicated signing page', () => {
    expect(fields.signature.page).toBe(SIGNING_PAGE.pageNumber)
    expect(fields.signature.page).toBe(3)
  })

  it('date field is on the dedicated signing page', () => {
    expect(fields.date.page).toBe(SIGNING_PAGE.pageNumber)
    expect(fields.date.page).toBe(3)
  })

  it('signature field x starts at left margin', () => {
    expect(fields.signature.x).toBe(Math.round(PAGE_MARGINS.left * SIGNWELL_COORDINATE_SCALE))
  })

  it('uses the SignWell signer raster scale instead of raw PDF points', () => {
    expect(SIGNWELL_RENDER_DPI).toBe(96)
    expect(PDF_POINTS_PER_INCH).toBe(72)
    expect(SIGNWELL_COORDINATE_SCALE).toBeCloseTo(4 / 3)
  })

  it('signature field is derived from the PDF-space layout rectangle', () => {
    expect(fields.signature.x).toBe(
      Math.round(SIGNING_PAGE.clientSignature.x * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.signature.y).toBe(
      Math.round(SIGNING_PAGE.clientSignature.y * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.signature.width).toBe(
      Math.round(SIGNING_PAGE.clientSignature.width * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.signature.height).toBe(
      Math.round(SIGNING_PAGE.clientSignature.height * SIGNWELL_COORDINATE_SCALE)
    )
  })

  it('signature field fits within page bounds', () => {
    expect(fields.signature.x + fields.signature.width!).toBeLessThanOrEqual(
      Math.round((PAGE_SIZE.width - PAGE_MARGINS.right) * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.signature.y + fields.signature.height!).toBeLessThanOrEqual(
      Math.round((PAGE_SIZE.height - PAGE_MARGINS.bottom) * SIGNWELL_COORDINATE_SCALE)
    )
  })

  it('date field is below signature field', () => {
    expect(fields.date.y).toBeGreaterThan(fields.signature.y + fields.signature.height!)
  })

  it('date field starts after the "Date:" label instead of at the left margin', () => {
    expect(fields.date.x).toBe(Math.round(SIGNING_PAGE.clientDate.x * SIGNWELL_COORDINATE_SCALE))
  })

  it('date field is derived from the PDF-space layout rectangle', () => {
    expect(fields.date.y).toBe(Math.round(SIGNING_PAGE.clientDate.y * SIGNWELL_COORDINATE_SCALE))
    expect(fields.date.width).toBe(
      Math.round(SIGNING_PAGE.clientDate.width * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.date.height).toBe(
      Math.round(SIGNING_PAGE.clientDate.height * SIGNWELL_COORDINATE_SCALE)
    )
  })

  it('date field fits within page bounds', () => {
    expect(fields.date.x + fields.date.width!).toBeLessThanOrEqual(
      Math.round((PAGE_SIZE.width - PAGE_MARGINS.right) * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.date.y + fields.date.height!).toBeLessThanOrEqual(
      Math.round((PAGE_SIZE.height - PAGE_MARGINS.bottom) * SIGNWELL_COORDINATE_SCALE)
    )
  })

  it('signature field has reasonable dimensions', () => {
    expect(fields.signature.width!).toBe(
      Math.round(SIGNING_PAGE.columnWidth * SIGNWELL_COORDINATE_SCALE)
    )
    expect(fields.signature.height!).toBe(Math.round(44 * SIGNWELL_COORDINATE_SCALE))
  })

  it('fields do not overlap', () => {
    const sigBottom = fields.signature.y + fields.signature.height!
    expect(fields.date.y).toBeGreaterThanOrEqual(sigBottom)
  })
})

describe('signwell: signing layout completeness', () => {
  it('stores canonical PDF-space signature geometry in the layout module', () => {
    expect(SIGNING_PAGE.clientSignature.x).toBe(PAGE_MARGINS.left)
    expect(SIGNING_PAGE.clientSignature.y).toBe(214)
    expect(SIGNING_PAGE.clientSignature.width).toBe(SIGNING_PAGE.columnWidth)
    expect(SIGNING_PAGE.clientSignature.height).toBe(44)
  })

  it('stores canonical PDF-space date geometry in the layout module', () => {
    expect(SIGNING_PAGE.clientDate.x).toBe(PAGE_MARGINS.left + 24)
    expect(SIGNING_PAGE.clientDate.y).toBe(300)
    expect(SIGNING_PAGE.clientDate.width).toBe(96)
    expect(SIGNING_PAGE.clientDate.height).toBe(18)
  })

  it('defines client signature position', () => {
    expect(SIGNING_PAGE.clientSignature).toBeDefined()
    expect(SIGNING_PAGE.clientSignature.x).toBeGreaterThan(0)
  })

  it('defines client date position', () => {
    expect(SIGNING_PAGE.clientDate).toBeDefined()
    expect(SIGNING_PAGE.clientDate.x).toBeGreaterThan(0)
  })

  it('uses a single fixed-width client acceptance block', () => {
    expect(SIGNING_PAGE.columnWidth).toBe(216)
    expect(SIGNING_PAGE.clientSignature.width).toBe(SIGNING_PAGE.columnWidth)
  })
})

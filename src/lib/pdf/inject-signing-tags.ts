/**
 * Inject SignWell text tags into a Forme-rendered PDF.
 *
 * Forme's WASM renderer does not write text to PDF content streams,
 * so SignWell's text tag scanner can't find tags embedded in JSX templates.
 * pdf-lib writes standard PDF text operations (BT/Tf/Tj/ET) that ARE
 * in the content stream. This module bridges the two.
 *
 * ## How it works
 *
 * 1. renderSow() generates the visual PDF via Forme
 * 2. This function post-processes the PDF via pdf-lib to add hidden text tags
 * 3. SignWell scans the PDF, finds the tags, and auto-places interactive fields
 *
 * ## Text tag syntax
 *
 * SignWell text tags use `{{type:signer:required:label:prefill:api_id:width:height}}`.
 * Empty fields must use a space (not empty string) — `::::` breaks detection,
 * but `: : :` works. This was validated via PoC testing.
 *
 * @see https://developers.signwell.com/reference/adding-text-tags
 * @see https://developers.signwell.com/reference/text-tag-options
 */

import { PDFDocument, rgb, StandardFonts } from 'pdf-lib'
import { SIGNING_PAGE, PAGE_SIZE } from './signing-layout'

/**
 * Inject SignWell text tags into a Forme-rendered SOW PDF.
 *
 * Adds hidden signature and date text tags at the measured signing positions
 * on the dedicated signing page (page 3). SignWell auto-detects these tags
 * when `text_tags: true` is set in the API request.
 *
 * @param pdfBytes - Raw PDF bytes from Forme's renderDocument()
 * @returns Modified PDF bytes with text tags injected
 */
export async function injectSigningTags(pdfBytes: Uint8Array): Promise<Uint8Array> {
  console.log(
    `[inject-signing-tags] Processing ${pdfBytes.length}b PDF, targeting page ${SIGNING_PAGE.pageNumber}`
  )
  const doc = await PDFDocument.load(pdfBytes)
  const signingPage = doc.getPage(SIGNING_PAGE.pageNumber - 1) // 0-indexed
  const font = await doc.embedFont(StandardFonts.Helvetica)

  // Tag text color: white (invisible on white background).
  // SignWell's field widget covers the tag position, so even if visible
  // the signer never sees the raw tag text.
  const tagColor = rgb(1, 1, 1)

  // Signature tag — placed at the empty signing space below "CLIENT" label.
  // Spaces for skipped fields (label, prefill) — empty colons break SignWell's parser.
  signingPage.drawText(
    `{{signature:1:y: : :client_signature:${SIGNING_PAGE.sigFieldWidth}:${SIGNING_PAGE.sigFieldHeight}}}`,
    {
      x: SIGNING_PAGE.tagInjection.signature.x,
      y: PAGE_SIZE.height - SIGNING_PAGE.tagInjection.signature.y,
      size: 6,
      font,
      color: tagColor,
    }
  )

  // Date tag — placed at the "Date: ___" position below contact name/title.
  signingPage.drawText(
    `{{date:1:y: : :client_date:${SIGNING_PAGE.dateFieldWidth}:${SIGNING_PAGE.dateFieldHeight}}}`,
    {
      x: SIGNING_PAGE.tagInjection.date.x,
      y: PAGE_SIZE.height - SIGNING_PAGE.tagInjection.date.y,
      size: 6,
      font,
      color: tagColor,
    }
  )

  const result = new Uint8Array(await doc.save())
  console.log(`[inject-signing-tags] Done: ${pdfBytes.length}b → ${result.length}b`)
  return result
}

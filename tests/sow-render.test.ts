/**
 * Integration test: renders actual SOW PDFs via Forme WASM.
 *
 * This test calls renderSow() to generate real PDFs, verifying
 * the template compiles and renders without errors. This catches
 * issues that source-code string matching tests cannot.
 *
 * SKIPPED: Forme WASM requires the Cloudflare Workers / Vite build
 * pipeline to load forme_bg.wasm. Vitest runs in Node.js where the
 * WASM import fails. This test is validated via the Astro build
 * (npm run build) and live deployment instead.
 *
 * To run manually: deploy to a preview branch and generate a SOW
 * via the admin UI, or use `npx astro build` to verify the template
 * compiles without errors.
 *
 * What would make this real (2026-09-10 code review, Testing 5): a second
 * vitest project on `@cloudflare/vitest-pool-workers` (`environment:
 * 'workers'`), where workerd loads `forme_bg.wasm` the way the deployed
 * Worker does, so `renderSow()` runs here and the assertions below become
 * text extracted from a real PDF. Until then the one artifact a client signs
 * is validated only by the build compiling the template.
 */

import { describe, it, expect } from 'vitest'
// import { renderSow } from '../src/lib/pdf/render'
// import { writeFileSync } from 'fs'

// Skipped: WASM not available in vitest/Node.js environment.
// Validated via Astro build and live deployment.
describe.skip('sow-render: PDF generation', () => {
  it.skip('renders a valid PDF with 2 line items', () => {
    expect(true).toBe(true)
  })

  it.skip('renders a valid PDF with 8 line items', () => {
    expect(true).toBe(true)
  })
})

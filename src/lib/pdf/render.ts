/**
 * Thin wrapper around Forme PDF rendering.
 *
 * Provides a single entry point for generating SOW PDFs from typed props.
 *
 * Cloudflare Workers can't fetch() WASM at runtime — the binary must be
 * imported at build time. We import forme_bg.wasm explicitly and pass
 * the compiled module to init() before any render call. The Astro
 * Cloudflare adapter (cloudflareModules) emits the WASM as a build
 * asset and produces a WebAssembly.Module reference.
 *
 * @see docs/spikes/forme-wasm-pdf.md — Forme spike results
 * @see src/lib/pdf/sow-template.tsx — SOW template component
 */

import { renderDocument } from '@formepdf/core'
import { init } from '@formepdf/core/browser'
import { SOWTemplate } from './sow-template'
import type { SOWTemplateProps } from './sow-template'
import { ScorecardReportTemplate } from './scorecard-template'
import type { ScorecardReportProps } from './scorecard-template'
import { injectSigningTags } from './inject-signing-tags'
import formeWasm from '@formepdf/core/pkg/forme_bg.wasm'

/**
 * Ensure the Forme WASM module is initialized before rendering.
 * Memoizes the init promise; resets on failure so the next call retries.
 */
let wasmReady: Promise<void> | null = null
function ensureWasm(): Promise<void> {
  if (!wasmReady) {
    wasmReady = init(formeWasm).catch((err) => {
      wasmReady = null
      throw err
    })
  }
  return wasmReady
}

/**
 * Render a Statement of Work PDF from quote/client/contact data.
 *
 * @param props - All data needed for the SOW template (see SOWTemplateProps)
 * @returns PDF binary as Uint8Array — suitable for R2 storage or HTTP response
 */
export async function renderSow(props: SOWTemplateProps): Promise<Uint8Array> {
  await ensureWasm()
  const pdf = await renderDocument(SOWTemplate(props))
  // Post-process: inject SignWell text tags for auto field detection.
  // Forme's WASM renderer doesn't write text to PDF content streams,
  // so we use pdf-lib to add tags that SignWell's scanner can find.
  try {
    const tagged = await injectSigningTags(pdf)
    console.log(`[renderSow] Text tag injection: ${pdf.length}b → ${tagged.length}b`)
    return tagged
  } catch (err) {
    console.error('[renderSow] Text tag injection FAILED, returning untagged PDF:', err)
    return pdf
  }
}

/**
 * Render an Operations Health Scorecard report PDF.
 *
 * @param props - Scorecard results data (see ScorecardReportProps)
 * @returns PDF binary as Uint8Array — suitable for email attachment
 */
export async function renderScorecardReport(props: ScorecardReportProps): Promise<Uint8Array> {
  await ensureWasm()
  const pdf = await renderDocument(ScorecardReportTemplate(props))
  return pdf
}

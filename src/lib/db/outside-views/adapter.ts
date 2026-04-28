/**
 * Versioned adapter for `outside_views.artifact_json` (ADR 0002).
 *
 * v1 stores the existing `RenderedReport` shape from
 * `src/lib/diagnostic/render.ts` as JSON. v2 will be the canonical
 * five-field-per-observation contract from ADR 0002 §3.
 *
 * Why version at the boundary, not in the renderer: the diagnostic
 * pipeline (workflow.ts → renderDiagnosticReport) produces
 * `RenderedReport` and is anti-fab-hardened by #617. Re-shaping that
 * output during the v1 ship is exactly what /critique 3 said not to
 * do. Instead we serialize `RenderedReport` straight into v1
 * `artifact_json`, then in v2 we add a forward-only adapter that
 * re-shapes on read. v1 rows continue to read fine; v2 rows read
 * fine; no backfill.
 *
 * Callers should:
 *   - Write via `renderedReportToArtifactJsonV1(report)`.
 *   - Read via `parseArtifactJson(json, version)` which returns a
 *     discriminated union; UI components branch on the `version`
 *     field.
 */

import type { RenderedReport } from '../../diagnostic/render'

export type ArtifactVersion = 1

export interface ArtifactV1 {
  version: 1
  report: RenderedReport
}

/**
 * Serialize a RenderedReport into v1 artifact_json.
 *
 * The on-disk shape is `{ version: 1, report: <RenderedReport> }`.
 * The wrapper preserves the version tag at the JSON root so a future
 * v2 reader can cleanly dispatch without inspecting RenderedReport
 * internals.
 */
export function renderedReportToArtifactJsonV1(report: RenderedReport): string {
  const v1: ArtifactV1 = { version: 1, report }
  return JSON.stringify(v1)
}

/**
 * Parse an artifact_json string into the in-memory representation.
 *
 * Returns null on parse failure or unsupported version. Callers that
 * need to render must handle null (the portal page renders a "we're
 * working on this" placeholder when the artifact is unreadable).
 *
 * The `expectedVersion` parameter is a defensive check against schema
 * drift between the row's `artifact_version` column and the JSON
 * payload; mismatches log and return null rather than rendering
 * partial data.
 */
export function parseArtifactJson(json: string, expectedVersion: number): ArtifactV1 | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(json)
  } catch {
    return null
  }

  if (typeof parsed !== 'object' || parsed === null) {
    return null
  }

  const obj = parsed as { version?: unknown; report?: unknown }

  if (obj.version === 1) {
    if (expectedVersion !== 1) {
      console.warn(`[outside-views/adapter] version mismatch: expected ${expectedVersion}, got 1`)
      return null
    }
    if (typeof obj.report !== 'object' || obj.report === null) {
      return null
    }
    return obj as ArtifactV1
  }

  console.warn(`[outside-views/adapter] unsupported artifact version: ${obj.version}`)
  return null
}

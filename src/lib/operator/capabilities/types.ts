/**
 * The Operator capability vocabulary.
 *
 * Per ADR 0006, `customer.yaml` wires which backend serves which capability,
 * and the `connectors:` map is keyed by the closed union below. That union is
 * the whole of this module now. The typed adapter layer that once sat beside
 * it (per-capability interfaces, `AdapterBase`, `AdapterError`, the
 * conformance harness) was never runtime-wired: the connector platform
 * (ADR 0053, author-built MCP servers under `operator/connectors/`) is what
 * seats actually run, and the TypeScript adapters were removed 2026-09-10
 * after the code review found no route, page, or script reached them.
 */

/**
 * Canonical capability names. The eleven Platform PRD §7.2 capabilities plus
 * WebSearch (ADR 0070), recorded here as a closed string union so adapters
 * cannot register against an unrecognized name. Add new capabilities here only
 * when an ADR records the addition.
 *
 * `WebSearch` is a connector-only capability: it has NO skill-facing adapter
 * interface in this layer (no `web-search.ts`). It exists solely so the
 * `connectors:` map — keyed by this union — can bind a `native:<provider>`
 * backend (ADR 0070, e.g. `native:brave-free`). The search tool is Hermes'
 * NATIVE `web_search` (a bundled web provider), which the agent calls directly;
 * the overlay's translate.py resolves `native:<provider>` to config
 * `web.search_backend`. Its conformance entry (BANNED_METHOD_NAMES) is therefore
 * empty — there is no adapter to conform. (The first ADR 0070 cut wrapped Brave
 * in an MCP server, `mcp:brave`; that redundant layer was retired 2026-07-08.)
 */
export type CapabilityName =
  | 'PracticeManagement'
  | 'Email'
  | 'Calendar'
  | 'DocumentStorage'
  | 'ESign'
  | 'CourtAccess'
  | 'Payments'
  | 'Accounting'
  | 'IntakeCRM'
  | 'CallTracking'
  | 'InternalComms'
  | 'WebSearch'

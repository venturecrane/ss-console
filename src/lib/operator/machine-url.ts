/**
 * The one place the console turns a customer's Fly app name into the base
 * URL of its Operator Machine.
 *
 * Five modules (entitlement change, pause control, sticky-stop clear, the
 * runtime-read transport, the MCP webhook transport) each carried a
 * byte-identical private copy of this function until the 2026-09-10 code
 * review (Code Quality 3). The function addresses the live Machine, so five
 * copies were five places a host-template change could silently miss; the
 * ESLint `no-restricted-syntax` guard now refuses a re-declaration anywhere
 * else under src/.
 *
 * `template` is the host template from the environment (for example
 * `https://{app}.fly.dev` or a per-environment override); when it carries no
 * `{app}` placeholder the Fly default host is used.
 */
export function machineBaseUrl(template: string, app: string): string {
  return template.includes('{app}') ? template.replace('{app}', app) : `https://${app}.fly.dev`
}

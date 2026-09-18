/**
 * Parse the JSON envelope `wrangler d1 execute --json` prints.
 *
 * This lives in a bare `.mjs` for one reason: it has two consumers that cannot
 * share a TypeScript module. `scripts/lib/wrangler-d1.ts` runs under `tsx` in
 * CI; `.claude/hooks/lib/register.mjs` is run by plain `node` from a bash
 * wrapper with no build step. Before this file existed the parse was written
 * twice -- once here, once inline inside `register.mjs`'s `lookupEntityId` --
 * and the two had already drifted (the TS copy threw on garbage, the inline one
 * returned an empty list, so the CLI read "no such customer" where CI read "the
 * database is broken").
 *
 * It lives under `scripts/` rather than `.claude/hooks/lib/` because
 * `eslint.config` ignores `**\/.claude/**`, and a parser that product CI depends
 * on should not sit in the unlinted tree.
 *
 * Wrangler emits two shapes depending on version and command, and both are in
 * use today, so both are handled here rather than at either call site:
 *
 *   [{ results: [...], success: true, meta: {...} }]   (array-wrapped)
 *   { results: [...], success: true }                  (bare object)
 */

/**
 * @param {string} stdout raw stdout from `wrangler d1 execute --json`
 * @returns {Record<string, unknown>[]} the result rows, or [] when the envelope
 *   carries none. Throws only when `stdout` is not JSON at all -- a caller that
 *   wants to treat unparseable output as empty must say so explicitly, because
 *   silently swallowing it is how a broken query reads as a clean table.
 */
export function parseWranglerJson(stdout) {
  const parsed = JSON.parse(stdout)
  if (Array.isArray(parsed)) {
    return parsed[0]?.results ?? []
  }
  return parsed?.results ?? []
}

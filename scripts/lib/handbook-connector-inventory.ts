/**
 * The author-built connector inventory block in docs/handbook/connectors-channels.md.
 *
 * The page went eleven weeks without mentioning a staff-mailbox reader and a
 * letter pipeline the window added to live connectors (2026-09-25 code review,
 * Documentation 2): nothing tied a connector change to the page. This renders
 * the part of the page that is a fact of the tree (each connector under
 * operator/connectors/, its manifest identity, every tool with its action
 * class, and its *_tools.py modules) between two markers, and
 * tests/handbook-integrity.test.ts fails when the committed block differs from
 * what the tree renders. So a PR that adds, removes, or reclassifies a tool, or
 * adds a tools module, cannot merge without touching the page, and the author
 * is standing in it when they regenerate: the prose around the block is theirs
 * to bring along. The api-inventory model (scripts/lib/api-inventory.ts), with
 * the generated part confined to a block because the page is mostly judgment.
 *
 * Parsing is deliberately narrow: the manifests' `[connector]` string keys and
 * the `[connector.tool_classes]` table, one `key = "value"` per line, which is
 * the shape operator/connectors/_sdk validates.
 */

import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'

export const BEGIN =
  '<!-- BEGIN GENERATED: connector inventory. Regenerate with `npm run handbook:connectors -- --write`; tests/handbook-integrity.test.ts fails while it is stale. -->'
export const END = '<!-- END GENERATED: connector inventory -->'

export interface ConnectorEntry {
  dir: string
  name: string
  capability: string
  authModel: string
  tools: Array<{ tool: string; actionClass: string }>
  toolModules: string[]
}

export function parseManifest(text: string): Omit<ConnectorEntry, 'dir' | 'toolModules'> {
  let section = ''
  const connector: Record<string, string> = {}
  const tools: Array<{ tool: string; actionClass: string }> = []
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    const header = line.match(/^\[\[?([^\]]+)\]\]?$/)
    if (header) {
      section = header[1].trim()
      continue
    }
    const kv = line.match(/^([A-Za-z0-9_]+)\s*=\s*"([^"]*)"\s*(?:#.*)?$/)
    if (!kv) continue
    if (section === 'connector') connector[kv[1]] = kv[2]
    if (section === 'connector.tool_classes') tools.push({ tool: kv[1], actionClass: kv[2] })
  }
  return {
    name: connector.name ?? '',
    capability: connector.capability ?? '',
    authModel: connector.auth_model ?? '',
    tools,
  }
}

export function buildConnectorInventory(connectorsRoot: string): ConnectorEntry[] {
  return readdirSync(connectorsRoot, { withFileTypes: true })
    .filter((d) => d.isDirectory() && existsSync(join(connectorsRoot, d.name, 'manifest.toml')))
    .map((d) => d.name)
    .sort()
    .map((dir) => {
      const base = join(connectorsRoot, dir)
      const pkg = readdirSync(base, { withFileTypes: true }).find(
        (e) => e.isDirectory() && existsSync(join(base, e.name, '__init__.py'))
      )
      const toolModules = pkg
        ? readdirSync(join(base, pkg.name))
            .filter((f) => f.endsWith('_tools.py'))
            .sort()
            .map((f) => `${pkg.name}/${f}`)
        : []
      return {
        dir,
        ...parseManifest(readFileSync(join(base, 'manifest.toml'), 'utf8')),
        toolModules,
      }
    })
}

export function renderConnectorInventory(entries: ConnectorEntry[]): string {
  const lines = [BEGIN, '']
  for (const e of entries) {
    lines.push(
      `**\`mcp:${e.name}\`** (\`operator/connectors/${e.dir}/\`): capability \`${e.capability}\`, manifest auth default \`${e.authModel}\`, ${e.tools.length} tools.`,
      ''
    )
    const byClass = new Map<string, string[]>()
    for (const t of e.tools)
      byClass.set(t.actionClass, [...(byClass.get(t.actionClass) ?? []), t.tool])
    for (const cls of [...byClass.keys()].sort()) {
      const names = (byClass.get(cls) ?? []).map((n) => `\`${n}\``).join(', ')
      lines.push(`- ${cls}: ${names}`)
    }
    lines.push(
      `- tool modules: ${e.toolModules.length ? e.toolModules.map((m) => `\`${m}\``).join(', ') : 'none (tools register in server.py)'}`,
      ''
    )
  }
  lines.push(END)
  return lines.join('\n')
}

/** The block as committed in the page, markers included, or null when absent. */
export function committedBlock(page: string): string | null {
  const start = page.indexOf(BEGIN)
  const end = page.indexOf(END)
  if (start === -1 || end === -1 || end < start) return null
  return page.slice(start, end + END.length)
}

/** The page with its block replaced by `rendered`; throws when the markers are missing. */
export function withBlock(page: string, rendered: string): string {
  const current = committedBlock(page)
  if (current === null) throw new Error('connectors-channels.md has no generated-block markers')
  return page.replace(current, () => rendered)
}

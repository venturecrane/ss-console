/**
 * Optional `record_stores:` block validator (ss-console#2793).
 *
 * A record store is a directory on the seat's volume that an inbound-email
 * turn may write into, by name. The overlay's `hermes-smd-record-store` plugin
 * registers `record_store_list` / `record_store_read` / `record_store_write`
 * only on a seat that authors at least one store; the model names a store and a
 * record, never a path, and the write lands inside the authored directory or is
 * refused. This is the only write the webhook platform is ever offered, and
 * why the file toolset can stay off it.
 *
 * The rules here are the overlay's rules (`shared/record_store.py`
 * `path_problem`), restated so a config the console accepts is one the seat
 * will serve:
 *
 *   - `name` is kebab-case and unique; the skill text names the store by it.
 *   - `path` is an absolute, normalized directory under `/opt/data`, outside
 *     the runtime-owned prefixes (profile homes, the attachment spool, the
 *     extraction cache, `.smd`, medchron). A store on a profile home would put
 *     a cron store or a skill body one record name away.
 *   - exactly those two keys.
 *
 * Validate-only (pushes errors); the block is materialized by the overlay and
 * read by the plugin at boot, not consumed from the parsed CustomerYaml.
 */

import type { ValidationError } from './types'
import { isPlainObject } from './helpers'

const STORE_NAME = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/
const VOLUME_ROOT = '/opt/data'
const RESERVED_PREFIXES = [
  '/opt/data/profiles',
  '/opt/data/attachment-spool',
  '/opt/data/smokeball-extract-cache',
  '/opt/data/.smd',
  '/opt/data/medchron',
] as const

/**
 * Why an authored store path is refused, or null when it is fine. Purely
 * lexical, like the overlay's: it judges the authored string, not a disk.
 */
export function recordStorePathProblem(path: string): string | null {
  if (!path.startsWith('/')) return 'must be an absolute path'
  const segments = path.split('/')
  const normalized = segments.every((s, i) =>
    i === 0 ? s === '' : s !== '' && s !== '.' && s !== '..'
  )
  if (!normalized || (path.length > 1 && path.endsWith('/'))) {
    return "must be a normalized absolute path (no '..', '.', doubled or trailing separators)"
  }
  if (path === VOLUME_ROOT) return 'must be a directory under /opt/data, not /opt/data itself'
  if (!path.startsWith(VOLUME_ROOT + '/'))
    return "must live under /opt/data (the seat's persistent volume)"
  for (const reserved of RESERVED_PREFIXES) {
    if (path === reserved || path.startsWith(reserved + '/')) {
      return `must not live under ${reserved} (owned by the runtime)`
    }
  }
  return null
}

export function checkRecordStores(root: Record<string, unknown>, errors: ValidationError[]): void {
  const raw = root['record_stores']
  if (raw === undefined || raw === null) return // optional block
  if (!Array.isArray(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'record_stores',
      message: 'record_stores must be a list of {name, path} entries',
    })
    return
  }
  const seen = new Set<string>()
  raw.forEach((entry, i) => {
    const prefix = `record_stores[${i}]`
    if (!isPlainObject(entry)) {
      errors.push({
        code: 'TypeMismatch',
        path: prefix,
        message: `${prefix} must be a mapping with name and path`,
      })
      return
    }
    const extra = Object.keys(entry)
      .filter((k) => k !== 'name' && k !== 'path')
      .sort()
    if (extra.length > 0) {
      errors.push({
        code: 'InvalidFormat',
        path: prefix,
        message: `${prefix}: unknown key(s) ${JSON.stringify(extra)}; only name and path`,
      })
    }
    const name = entry['name']
    if (typeof name !== 'string' || !STORE_NAME.test(name)) {
      errors.push({
        code: 'InvalidSlug',
        path: `${prefix}.name`,
        message: `${prefix}.name must be kebab-case (a-z, 0-9, -)`,
      })
    } else if (seen.has(name)) {
      errors.push({
        code: 'InvalidFormat',
        path: `${prefix}.name`,
        message: `${prefix}.name: '${name}' is authored twice`,
      })
    } else {
      seen.add(name)
    }
    const path = entry['path']
    if (typeof path !== 'string') {
      errors.push({
        code: 'TypeMismatch',
        path: `${prefix}.path`,
        message: `${prefix}.path must be an absolute path string`,
      })
      return
    }
    const problem = recordStorePathProblem(path)
    if (problem) {
      errors.push({
        code: 'InvalidFormat',
        path: `${prefix}.path`,
        message: `${prefix}.path: ${problem}`,
      })
    }
  })
}

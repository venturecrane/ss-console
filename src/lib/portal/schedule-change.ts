/**
 * When a seat's SCHEDULE last changed, as opposed to when its config was last
 * published (`customer_configs.schedule_changed_at`, migration 0130).
 *
 * Work-health grades scheduled runs only once the Machine has booted into the
 * schedule it is graded against (operator/bin/lib/work_health_sources.py,
 * `schedule_adopted_at`). It used `synced_at` for "when the schedule changed",
 * but `synced_at` moves on EVERY publish: on 2026-10-19 #3332 only removed
 * `hermes_ref` from A&P's customer.yaml, and from then until the next rebuild
 * A&P's scheduled runs were not graded at all, so a missed run would have gone
 * unreported. This module answers the narrower question from git history: the
 * commit time of the newest change to the parts of customer.yaml a running
 * Machine adopts only at boot and that grading depends on.
 *
 * Pure and env-free: scripts/project-customer-config.ts reads the history.
 */

import { parse as parseYaml } from 'yaml'

import { isRecord } from '../api/helpers'

/**
 * The schedule as grading sees it: every `personas[].cron[]` row's skill,
 * schedule and wake_policy (operator/bin/lib/cron_slots.py
 * `authored_cron_rows`; the seat side is `deployed_cron`), plus
 * `business_hours.timezone` (`seat_timezone`: the zone cron fires in). Order
 * and duplicates do not matter; comments, other fields and formatting do not
 * reach it. Lenient by design: the schema validator owns malformedness, and an
 * old revision must not need to satisfy today's schema to be fingerprinted.
 */
export function scheduleFingerprint(config: unknown): string {
  const doc = isRecord(config) ? config : {}
  const rows = new Set<string>()
  const personas = Array.isArray(doc.personas) ? doc.personas : []
  for (const persona of personas) {
    const cron = isRecord(persona) && Array.isArray(persona.cron) ? persona.cron : []
    for (const entry of cron) {
      if (!isRecord(entry)) continue
      const { skill, schedule } = entry
      if (typeof skill !== 'string' || !skill || typeof schedule !== 'string' || !schedule) continue
      const wake = typeof entry.wake_policy === 'string' ? entry.wake_policy : ''
      rows.add(JSON.stringify([skill, schedule, wake]))
    }
  }
  const hours = isRecord(doc.business_hours) ? doc.business_hours : {}
  const timezone = typeof hours.timezone === 'string' ? hours.timezone : ''
  return JSON.stringify({ timezone, cron: [...rows].sort() })
}

export interface Revision {
  /** ISO-8601 committer time of the commit that wrote this revision. */
  committedAt: string
  /** customer.yaml bytes at that commit. */
  text: string
}

/**
 * The committer time of the newest revision whose schedule differs from the
 * revision before it, given the file's revisions NEWEST FIRST (`git log --
 * <path>` order). A file whose schedule never changed returns its first
 * revision's time. A revision that is not parseable YAML could never have been
 * published (the publisher parses and validates first), so it is skipped
 * rather than counted as a change. Null when no revision parses.
 */
export function scheduleChangedAt(revisions: Revision[]): string | null {
  const parsed: { at: string; fp: string }[] = []
  for (const r of revisions) {
    let doc: unknown
    try {
      doc = parseYaml(r.text)
    } catch {
      continue
    }
    parsed.push({ at: r.committedAt, fp: scheduleFingerprint(doc) })
  }
  for (let i = 0; i < parsed.length - 1; i++) {
    if (parsed[i].fp !== parsed[i + 1].fp) return parsed[i].at
  }
  return parsed.length ? parsed[parsed.length - 1].at : null
}

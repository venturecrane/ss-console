/**
 * send_policy section validator for customer.yaml (#2070): the reply-channel
 * send-rate policy — internal dialogue exemption, per-sender/global caps, the
 * reply backstop, and held-reply auto-release.
 *
 * Mirrors the overlay's `bootstrap.validate._validate_send_policy` — keep the
 * two in lockstep (the validator parity fixture contract pins agreement). The
 * Machine's runtime resolver tolerates a malformed block by falling back to
 * the platform defaults (whole-block, fail-closed — the authored exemption is
 * DROPPED); this validator surfaces the typo at authoring time instead of
 * silently changing the authored intent.
 */

import type { SendPolicy, SendPolicyHeldRelease, SendPolicyReply, ValidationError } from './types'
import { isPlainObject } from './helpers'

const TOP_KEYS = new Set(['reply', 'held_release'])
const REPLY_KEYS = new Set([
  'internal_exempt',
  'per_sender_max',
  'per_sender_window_seconds',
  'global_max',
  'global_window_seconds',
  'backstop_max',
  'backstop_window_seconds',
  // The end-of-turn held line and the redraft cap (the overlay's
  // plugins/hermes-smd-reply/held_notice.py, 2026-11-10).
  'held_notice',
  'max_redrafts',
])
const MAX_REDRAFTS_CEILING = 10
const HELD_KEYS = new Set(['enabled', 'ttl_seconds'])

const REPLY_COUNT_KEYS = ['per_sender_max', 'global_max', 'backstop_max'] as const
const REPLY_WINDOW_KEYS = [
  'per_sender_window_seconds',
  'global_window_seconds',
  'backstop_window_seconds',
] as const

export function checkSendPolicy(
  root: Record<string, unknown>,
  errors: ValidationError[]
): SendPolicy | null {
  const raw = root['send_policy']
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'send_policy',
      message: 'send_policy must be an object',
    })
    return null
  }
  rejectUnknownKeys(raw, TOP_KEYS, 'send_policy', errors)
  return {
    reply: checkReply(raw['reply'], errors),
    held_release: checkHeldRelease(raw['held_release'], errors),
  }
}

function checkReply(raw: unknown, errors: ValidationError[]): SendPolicyReply | null {
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'send_policy.reply',
      message: 'send_policy.reply must be an object',
    })
    return null
  }
  rejectUnknownKeys(raw, REPLY_KEYS, 'send_policy.reply', errors)
  checkBool(raw['internal_exempt'], 'send_policy.reply.internal_exempt', errors)
  checkBool(raw['held_notice'], 'send_policy.reply.held_notice', errors)
  checkMaxRedrafts(raw['max_redrafts'], errors)
  const counts: Partial<Record<(typeof REPLY_COUNT_KEYS)[number], number | null>> = {}
  for (const key of REPLY_COUNT_KEYS) {
    counts[key] = checkCount(raw[key], `send_policy.reply.${key}`, errors)
  }
  const windows: Partial<Record<(typeof REPLY_WINDOW_KEYS)[number], number | null>> = {}
  for (const key of REPLY_WINDOW_KEYS) {
    windows[key] = checkWindow(raw[key], `send_policy.reply.${key}`, errors)
  }
  return {
    internal_exempt: raw['internal_exempt'] === true,
    per_sender_max: counts['per_sender_max'] ?? null,
    per_sender_window_seconds: windows['per_sender_window_seconds'] ?? null,
    global_max: counts['global_max'] ?? null,
    global_window_seconds: windows['global_window_seconds'] ?? null,
    backstop_max: counts['backstop_max'] ?? null,
    backstop_window_seconds: windows['backstop_window_seconds'] ?? null,
  }
}

function rejectUnknownKeys(
  raw: Record<string, unknown>,
  known: Set<string>,
  path: string,
  errors: ValidationError[]
): void {
  for (const key of Object.keys(raw)) {
    if (!known.has(key)) {
      errors.push({
        code: 'UnknownSendPolicyField',
        path: `${path}.${key}`,
        message: `${path}.${key}: unknown key`,
      })
    }
  }
}

function checkMaxRedrafts(v: unknown, errors: ValidationError[]): void {
  if (v === undefined || v === null) return
  if (typeof v !== 'number' || !Number.isInteger(v) || v < 0 || v > MAX_REDRAFTS_CEILING) {
    errors.push({
      code: 'TypeMismatch',
      path: 'send_policy.reply.max_redrafts',
      message: `send_policy.reply.max_redrafts must be a whole number from 0 to ${MAX_REDRAFTS_CEILING}`,
    })
  }
}

function checkBool(v: unknown, path: string, errors: ValidationError[]): void {
  if (v === undefined || v === null) return
  if (typeof v !== 'boolean') {
    errors.push({ code: 'TypeMismatch', path, message: `${path} must be a boolean` })
  }
}

function checkHeldRelease(raw: unknown, errors: ValidationError[]): SendPolicyHeldRelease | null {
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'send_policy.held_release',
      message: 'send_policy.held_release must be an object',
    })
    return null
  }
  rejectUnknownKeys(raw, HELD_KEYS, 'send_policy.held_release', errors)
  checkBool(raw['enabled'], 'send_policy.held_release.enabled', errors)
  const ttlRaw = raw['ttl_seconds']
  let ttl: number | null = null
  if (ttlRaw !== undefined && ttlRaw !== null) {
    if (typeof ttlRaw !== 'number' || !Number.isInteger(ttlRaw) || ttlRaw <= 0) {
      errors.push({
        code: 'TypeMismatch',
        path: 'send_policy.held_release.ttl_seconds',
        message: 'send_policy.held_release.ttl_seconds must be a positive integer',
      })
    } else {
      ttl = ttlRaw
    }
  }
  return { enabled: raw['enabled'] === true, ttl_seconds: ttl }
}

function checkCount(v: unknown, path: string, errors: ValidationError[]): number | null {
  if (v === undefined || v === null) return null
  if (typeof v !== 'number' || !Number.isInteger(v) || v < 0) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a non-negative integer`,
    })
    return null
  }
  return v
}

function checkWindow(v: unknown, path: string, errors: ValidationError[]): number | null {
  if (v === undefined || v === null) return null
  if (typeof v !== 'number' || !Number.isFinite(v) || v <= 0) {
    errors.push({
      code: 'TypeMismatch',
      path,
      message: `${path} must be a positive number`,
    })
    return null
  }
  return v
}

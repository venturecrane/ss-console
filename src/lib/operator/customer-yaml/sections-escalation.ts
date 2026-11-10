/**
 * Escalation section validator for customer.yaml: the two required recipient
 * lists, the optional acknowledgement window, and the optional case-alert
 * routing block (#2004). Split from sections-other.ts at the 500-line file
 * ceiling.
 */

import {
  ACCEPTED_CASE_ALERT_ROUTING_MODES,
  type CaseAlertRouting,
  type CaseAlertRoutingMode,
  type Escalation,
  type ValidationError,
} from './types'
import { isPlainObject, requireStringList } from './helpers'

export function checkEscalation(
  root: Record<string, unknown>,
  errors: ValidationError[]
): Escalation {
  const raw = root['escalation']
  const empty: Escalation = {
    red_flag_recipients: [],
    failure_recipients: [],
    acknowledgement_window_minutes: null,
    case_alert_routing: null,
  }
  if (raw === undefined || raw === null) {
    errors.push({
      code: 'MissingField',
      path: 'escalation',
      message: 'escalation is required',
    })
    return empty
  }
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'escalation',
      message: 'escalation must be an object',
    })
    return empty
  }
  const reds = requireStringList(
    raw,
    'red_flag_recipients',
    'escalation.red_flag_recipients',
    errors
  )
  if (reds.length === 0) {
    errors.push({
      code: 'EmptyList',
      path: 'escalation.red_flag_recipients',
      message: 'escalation.red_flag_recipients must contain at least one address',
    })
  }
  const fails = requireStringList(
    raw,
    'failure_recipients',
    'escalation.failure_recipients',
    errors
  )
  if (fails.length === 0) {
    errors.push({
      code: 'EmptyList',
      path: 'escalation.failure_recipients',
      message: 'escalation.failure_recipients must contain at least one address',
    })
  }
  const ack = checkAckWindow(raw['acknowledgement_window_minutes'], errors)
  const routing = checkCaseAlertRouting(raw['case_alert_routing'], errors)
  const cardText = checkRequestCardText(raw['request_card_text'], errors)
  return {
    red_flag_recipients: reds,
    failure_recipients: fails,
    acknowledgement_window_minutes: ack,
    case_alert_routing: routing,
    ...(cardText === null ? {} : { request_card_text: cardText }),
  }
}

/** `escalation.request_card_text`: optional boolean; null when absent or wrong. */
function checkRequestCardText(v: unknown, errors: ValidationError[]): boolean | null {
  if (v === undefined || v === null) return null
  if (typeof v !== 'boolean') {
    errors.push({
      code: 'TypeMismatch',
      path: 'escalation.request_card_text',
      message: 'escalation.request_card_text must be true or false',
    })
    return null
  }
  return v
}

/**
 * Authored case-alert routing (#2004). Absent → null, which every consumer
 * treats as `central` (today's behavior). When present, `mode` is required
 * from the closed vocabulary; `fallback_recipients` is optional and may be
 * empty — an authored `matter_staff` mode with no fallback is a deliberate
 * fail-closed posture (hold + matter flag), not an error.
 */
function checkCaseAlertRouting(raw: unknown, errors: ValidationError[]): CaseAlertRouting | null {
  if (raw === undefined || raw === null) return null
  if (!isPlainObject(raw)) {
    errors.push({
      code: 'TypeMismatch',
      path: 'escalation.case_alert_routing',
      message: 'escalation.case_alert_routing must be an object',
    })
    return null
  }
  const mode = raw['mode']
  if (
    typeof mode !== 'string' ||
    !(ACCEPTED_CASE_ALERT_ROUTING_MODES as readonly string[]).includes(mode)
  ) {
    errors.push({
      code: 'EnumViolation',
      path: 'escalation.case_alert_routing.mode',
      message: `escalation.case_alert_routing.mode must be one of: ${ACCEPTED_CASE_ALERT_ROUTING_MODES.join(', ')}`,
    })
    return null
  }
  const fallback =
    raw['fallback_recipients'] === undefined || raw['fallback_recipients'] === null
      ? []
      : requireStringList(
          raw,
          'fallback_recipients',
          'escalation.case_alert_routing.fallback_recipients',
          errors
        )
  return {
    mode: mode as CaseAlertRoutingMode,
    fallback_recipients: fallback,
  }
}

function checkAckWindow(a: unknown, errors: ValidationError[]): number | null {
  if (a === undefined || a === null) return null
  if (typeof a !== 'number' || !Number.isInteger(a) || a <= 0) {
    errors.push({
      code: 'TypeMismatch',
      path: 'escalation.acknowledgement_window_minutes',
      message: 'acknowledgement_window_minutes must be a positive integer',
    })
    return null
  }
  return a
}

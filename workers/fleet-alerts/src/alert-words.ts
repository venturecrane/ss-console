/**
 * What an alert email says, in words a person can act on (Captain, 2026-11-02:
 * "mostly noise"). Same shape as the request email: what happened, what you need
 * to do, and the machine's own detail below a fold for the agent. Who acts follows
 * docs/handbook/when-something-goes-wrong.md.
 *
 * Only what ./mail-policy lets through reaches here. A condition with no entry
 * still reads: it falls back to its label and "give this to an agent".
 */

import {
  conditionLabel,
  conditionPayload,
  CONNECTOR_DOWN_PREFIX,
  CONNECTOR_TOKEN_EXPIRING_PREFIX,
  TOOL_FAILING_PREFIX,
} from './conditions'
import { escapeHtml } from './html'
import type { FleetCondition } from './index'

export interface AlertWords {
  /** The subject after the seat: what happened, short. */
  subject: string
  happened: string
  todo: string
}

const AGENT_NOW = 'Give this email to an agent now.'
const AGENT = 'Give this email to an agent to find out why and fix it.'
const NOT_ANSWERING: AlertWords = {
  subject: 'the Operator is not answering',
  happened: 'This seat has stopped responding, so requests to it are not being answered.',
  todo: AGENT_NOW,
}
const SCHEDULE: AlertWords = {
  subject: 'scheduled work is not running',
  happened: "This seat's scheduled work is not running, so it may be missing routine work.",
  todo: AGENT,
}

const WORDS: Record<string, AlertWords> = {
  heartbeat_red: {
    subject: 'the Operator may be down',
    happened:
      'This seat has stopped reporting in, so treat it as down until an agent says otherwise.',
    todo: AGENT_NOW,
  },
  hard_stop: {
    subject: 'the Operator stopped itself',
    happened:
      'A cost or safety limit stopped this seat on purpose. Nothing restarts it on its own.',
    todo: 'Give this email to an agent to find out why. If it was a cost limit, restarting it is your decision.',
  },
  gateway_loop_wedged: NOT_ANSWERING,
  gateway_supervisor_refusing: NOT_ANSWERING,
  scheduler_error: SCHEDULE,
  work_overdue: SCHEDULE,
  edge_down: {
    subject: 'the website is down',
    happened: 'The website and portal are not answering from outside.',
    todo: AGENT_NOW,
  },
  enrollment_guessing: {
    subject: 'someone may be guessing invite codes',
    happened:
      'A seat that takes invite codes (the product guide or the demo seat) is receiving many wrong ones, or has locked itself and is refusing every code, real ones included.',
    todo: 'Give this email to an agent today to see who is sending them and revoke any code that leaked.',
  },
}

export function alertWords(condition: string): AlertWords {
  const known = WORDS[condition]
  if (known) return known
  const down = conditionPayload(condition, CONNECTOR_DOWN_PREFIX)
  if (down !== null) {
    return {
      subject: `the ${down} connection is failing`,
      happened: `The connection to ${down} is failing, so work that needs it may have stopped.`,
      todo: 'Give this email to an agent to check it. If the client has to sign in again, the agent will say so and that email is yours to send.',
    }
  }
  const expiring = conditionPayload(condition, CONNECTOR_TOKEN_EXPIRING_PREFIX)
  if (expiring !== null) {
    return {
      subject: `the ${expiring} sign-in is about to expire`,
      happened: `The client's ${expiring} sign-in is about to expire. When it does, work that needs it stops.`,
      todo: 'Email the client and ask them to sign in again before it expires.',
    }
  }
  const tool = conditionPayload(condition, TOOL_FAILING_PREFIX)
  if (tool !== null) {
    return {
      subject: 'a tool the Operator uses is failing',
      happened:
        'A tool the Operator uses has failed on every recent call, so work that needs it may have stopped.',
      todo: AGENT,
    }
  }
  const label = conditionLabel(condition as FleetCondition)
  return { subject: label, happened: `${label}.`, todo: AGENT }
}

const SOURCE_WORDS: Record<string, AlertWords> = {
  healthchecks: {
    subject: 'a monitor stopped reporting',
    happened: 'A monitor that watches this seat has gone quiet, so other alerts may be missing.',
    todo: 'Give this email to an agent to restore it first.',
  },
  audit_integrity: {
    subject: 'the audit record may be incomplete',
    happened:
      'A check found that the record of what the Operator did may be incomplete or changed.',
    todo: 'Give this email to an agent today. What the client is told is your decision.',
  },
}

export function sourceWords(source: string): AlertWords {
  return (
    SOURCE_WORDS[source] ?? {
      subject: `${source} alert`,
      happened: `A ${source} alert was raised on this seat.`,
      todo: AGENT,
    }
  )
}

/** The email: what happened and what to do, then the machine's detail below a fold. */
export function alertHtml(happened: string, todo: string, details: string[]): string {
  return (
    `<p><strong>WHAT HAPPENED:</strong> ${escapeHtml(happened)}</p>` +
    `<p><strong>WHAT YOU NEED TO DO:</strong> ${escapeHtml(todo)}</p>` +
    `<hr><p><strong>DETAILS, for the agent who picks this up</strong><br>` +
    `${details.map(escapeHtml).join('<br>')}</p>`
  )
}

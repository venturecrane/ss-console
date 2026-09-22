import { readdirSync, readFileSync, existsSync } from 'node:fs'
import { parse } from 'yaml'
import { describe, expect, it } from 'vitest'

/**
 * ADR 0089: a staff member's "send it as me" request must reach the send-as
 * tool, whichever skill a seat routes inbound mail to.
 *
 * WHY THIS EXISTS. The first live test on smd-staging (2026-09-22) had
 * everything wired except the words: the tool was registered with `from`, the
 * broker and approval path were proven, and the model even quoted the seat's
 * `external_send_as_staff: confirm` back. It still called `create_draft`,
 * pasted the draft into an ordinary reply, and told the staff member to reply
 * "approved", because the routed skill said draft-only and never named the
 * send-as call. A capability the routed skill does not mention is a capability
 * the seat does not have.
 *
 * The skill set is DERIVED from the authored configs, not listed by hand: every
 * skill a `message.received` trigger routes to, on any mail source. It is also
 * pinned exactly, so a new mail-routed skill cannot join silently, and an empty
 * parse cannot pass.
 */

const CUSTOMERS = 'operator/customers'
const read = (p: string) => readFileSync(p, 'utf8')
const flat = (s: string) => s.replace(/\s+/g, ' ')

interface Trigger {
  source?: string
  event_type?: string
  skill?: string
}

function mailRoutedSkills(): Set<string> {
  const skills = new Set<string>()
  for (const slug of readdirSync(CUSTOMERS)) {
    const file = `${CUSTOMERS}/${slug}/customer.yaml`
    if (!existsSync(file)) continue
    const doc = parse(read(file)) as { webhook_triggers?: Trigger[] } | null
    for (const t of doc?.webhook_triggers ?? []) {
      if (t.event_type === 'message.received' && t.skill) skills.add(t.skill)
    }
  }
  return skills
}

const skillBody = (skill: string) => flat(read(`operator/skills/${skill}/SKILL.md`))

/**
 * Mail-routed skills that deliberately carry no send-as rule, each with its
 * reason. Send As exists only on Microsoft 365 seats (ADR 0089), so a skill that
 * only ever runs on an AgentMail seat has no tool path to name.
 */
const SEND_AS_EXEMPT: Record<string, string> = {
  'open-house-visitor-capture':
    "the scott seat's AgentMail POC; AgentMail has no Send As and that seat authors no staff_send_as",
}

describe('send-as reaches the tool from every mail-routed skill (ADR 0089)', () => {
  it('derives exactly the two mail-routed skills, plus reviewed exemptions', () => {
    // If this fails because a seat routes mail to a new skill, give that skill
    // the "Send as a staff member" rule and add it here, or exempt it with a
    // reason a reviewer can check.
    const derived = mailRoutedSkills()
    const covered = [...derived].filter((s) => !(s in SEND_AS_EXEMPT)).sort()
    expect(covered).toEqual(['inbox-triage', 'matter-inbox-router'])
    for (const [skill, reason] of Object.entries(SEND_AS_EXEMPT)) {
      expect(derived.has(skill), `${skill} is exempt but no seat routes mail to it`).toBe(true)
      expect(reason.length).toBeGreaterThan(20)
    }
  })

  for (const skill of ['inbox-triage', 'matter-inbox-router']) {
    describe(skill, () => {
      it('names the send-as call', () => {
        const body = skillBody(skill)
        expect(body).toContain('**Send as a staff member.**')
        expect(body).toContain('call `smd_send_message` with `from` set to the sender')
      })

      it('reads success and refusal by their first words, and never invents a reply word', () => {
        // The success result is delivered as a blocked tool call. Without these
        // two strings a draft-primed model reads "Held for approval" as a refusal
        // and falls back to the very draft this rule replaces.
        const body = skillBody(skill)
        expect(body).toContain('"Held for approval, not sent." means it worked')
        expect(body).toContain('create nothing else, no draft and no pasted copy')
        expect(body).toContain('"Refused:" means it did not: do exactly what the refusal says')
        expect(body).toContain('never in a draft addressed to the outside party')
        expect(body).toContain('never tell anyone to reply with a word such as "approved"')
      })
    })
  }

  it('leaves no absolute no-send line without the exception', () => {
    // The words that caused the 09-22 failure. Each must now carry the carve-out
    // in the same sentence group, or the model still has a rule saying "never".
    const triage = skillBody('inbox-triage')
    expect(triage).toContain(
      'there is no send tool in this skill\'s surface. The send-as call in "Send as a staff member" is the one exception'
    )
    expect(triage).toContain(
      "Never sends, never archives, never replies on the user's behalf.** Captain reads, ships, and grades. The one exception"
    )
    const router = skillBody('matter-inbox-router')
    expect(router).toContain(
      'sends nothing itself. The send-as request class is the one exception, and it is a proposal'
    )
    expect(router).toContain(
      'Do not use a direct-send tool (a send-as request is the class above, whose call proposes and does not send)'
    )
  })

  it('gives the router rubric the same class', () => {
    // The rubric is what the router reads on the scheduled-poll channel.
    const rubric = flat(read('operator/skills/matter-inbox-router/references/routing-rubric.md'))
    expect(rubric).toContain('**send-as request**')
    expect(rubric).toContain('"Held for approval, not sent." means it worked')
    expect(rubric).toContain('never tell anyone to reply with a word such as "approved."')
  })
})

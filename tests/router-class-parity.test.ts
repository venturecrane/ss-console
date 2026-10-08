import { readdirSync, existsSync, readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

/**
 * The inbox router is the only door an inbound email has. A class that exists
 * in SKILL.md but not in `references/routing-rubric.md` is a class the router
 * cannot see on the scheduled-poll and Claude channels, where the rubric is the
 * text it reads; the rubric calls itself the source of truth and was source of
 * truth for 9 of 16 routing targets when this file was written (2026-09-11).
 *
 * The chronology class is pinned separately because it is the one routing class
 * that SPENDS the firm's authored page allowance, so two properties of it are
 * load-bearing rather than stylistic: that it is reachable at all, and that it
 * stays admin-reserved.
 *
 * WHY NOTHING HERE USES A LINE NUMBER. The change that introduced this file
 * inserted a table row and a bullet, moving every line after them. Positional
 * scoping would have been wrong on arrival. Everything is located by heading
 * and marker text instead.
 */

const SKILLS_DIR = 'operator/skills'
const ROUTER = `${SKILLS_DIR}/matter-inbox-router/SKILL.md`
const RUBRIC = `${SKILLS_DIR}/matter-inbox-router/references/routing-rubric.md`
const DRAFTING_DISCIPLINE = 'operator/templates/drafting/drafting-discipline.md'
const CHRONOLOGY = 'medical-chronology-maintainer'

/**
 * Slugs allowed to appear as a routing target with no rubric row. Empty on
 * purpose: every exemption is a class the router can reach and the rubric
 * cannot describe. A reason under 20 characters is not a reason.
 */
const ROUTER_RUBRIC_EXEMPT: Record<string, string> = {}

const read = (p: string) => readFileSync(p, 'utf8')

/** Collapse wrapping so a match cannot fail on where a line happened to break. */
const flat = (s: string) => s.replace(/\s+/g, ' ')

/** The class table: the markdown table under the routing-decision heading. */
function classTable(body: string): string {
  const lines = body.split('\n')
  const start = lines.findIndex((l) => l.startsWith('| Inbound class'))
  if (start < 0) throw new Error('class table not found under "## The routing decision"')
  const end = lines.findIndex((l, i) => i > start && !l.startsWith('|'))
  return lines.slice(start, end).join('\n')
}

/** The per-class handling bullets under step 4 of Phase 2. */
function classBullets(body: string): string {
  const lines = body.split('\n')
  const start = lines.findIndex((l) => l.trimStart().startsWith('- **'))
  if (start < 0) throw new Error('per-class bullets not found')
  const end = lines.findIndex((l, i) => i > start && /^\d+\. /.test(l))
  return lines.slice(start, end < 0 ? undefined : end).join('\n')
}

/** One class's bullet, from its bold marker to the next bullet. */
function bulletFor(body: string, marker: string): string {
  const bullets = classBullets(body)
  const at = bullets.indexOf(marker)
  if (at < 0) throw new Error(`no bullet marked ${marker}`)
  const next = bullets.indexOf('\n   - **', at + 1)
  return bullets.slice(at, next < 0 ? undefined : next)
}

const skillSlugs = () =>
  new Set(readdirSync(SKILLS_DIR).filter((d) => existsSync(`${SKILLS_DIR}/${d}/SKILL.md`)))

/** Every skill slug named as a routing target in the two routing regions. */
function routingTargets(body: string): Set<string> {
  const slugs = skillSlugs()
  const region = `${classTable(body)}\n${classBullets(body)}`
  const found = new Set<string>()
  for (const [, tok] of region.matchAll(/`([a-z][a-z0-9-]+)`/g)) {
    if (slugs.has(tok) && tok !== 'matter-inbox-router') found.add(tok)
  }
  return found
}

/**
 * The demand class (2026-10-06). The first real demand request a firm sent
 * was drafted INLINE by the router: line "Attorney drafting request ...
 * EXECUTE in this turn" told it to read the drafting skill and carry it out,
 * so it read a fraction of a 215-document file and drafted from that. A demand
 * is now a queued job. These pin the route an eval of "Pat: draft the demand
 * on Dana Example" must take: the demand class, a submit, no inline draft.
 */
describe('matter-inbox-router: the demand class', () => {
  const DEMAND = 'demand-letter-drafter'
  const DEMAND_SKILL = `${SKILLS_DIR}/${DEMAND}/SKILL.md`
  const bullet = () => flat(bulletFor(read(ROUTER), '**Demand request**'))

  it('routes an attorney\'s "draft the demand" to the job, not the drafting lane', () => {
    // The eval: an attorney's own "draft the demand" is THIS class's example,
    // and the attorney drafting class neither names it nor the demand skill.
    expect(bullet()).toContain('"Pat: draft the demand on Dana Example"')
    expect(bullet()).toContain('This class takes EVERY demand ask whoever sends it')
    const drafting = flat(bulletFor(read(ROUTER), '**Attorney drafting request**'))
    expect(drafting).not.toContain(DEMAND)
    expect(drafting).not.toContain('"draft the demand,"')
    expect(drafting).toContain('a demand is NEVER this class')
  })

  it('DELIVER binds by job id, sends nothing else on a refusal, and names where files went', () => {
    // 2026-10-06 practice job: DELIVER bound by the email's id (refused, the
    // email had its acknowledgment), then emailed the responsible attorney and
    // said the files were "in the matter's demand folder" when they were filed
    // to the library matter. Each pin below is one of those three failures.
    const skill = flat(read(DEMAND_SKILL))
    const deliver = skill.slice(skill.indexOf('## DELIVER mode'), skill.indexOf('## Boundaries'))
    expect(deliver).toContain('`reply_bind` and ONLY `job_id`')
    expect(deliver).toContain('Never pass `internet_message_id` or `graph_message_id` in this mode')
    expect(deliver).toContain('**If the bind is refused, send NOTHING to anyone.**')
    expect(deliver).toContain('Not the responsible attorney')
    expect(deliver).toContain('`smd_send_message`')
    expect(deliver).toContain('`file_to_matter_id` against `matter_id`')
    expect(deliver).toContain('NOT the client matter')
    expect(deliver).not.toContain('the documents are filed in matter <number>')
    // A failed job is ours: the client hears nothing, SMD is alerted, and no reply ever
    // asks the firm to narrow its request or names a limit, cost or job id.
    expect(deliver).toContain('Send the client NOTHING')
    expect(deliver).toContain("raises SMD's shortfall alert")
    expect(deliver).toContain('never ask the firm to narrow, split or change its request')
    expect(deliver).toContain('a token, a limit, a cap, a cost, a dollar figure or a job id')
    expect(deliver).not.toContain('**held** or **failed**')
    // A delivered package names the next step its gap audit opens, so the firm learns
    // it from the Operator itself; nothing is ordered until she answers the order line.
    expect(deliver).toContain("Reply 'order the missing records'")
    expect(deliver).toContain('nothing is ordered until she answers the order line')
  })

  it('submits and never drafts in the turn', () => {
    expect(bullet()).toContain(`/app/skills/${DEMAND}/SKILL.md`)
    expect(bullet()).toContain('REQUEST mode')
    expect(bullet()).toContain('demand_job_submit')
    expect(bullet()).toContain('NEVER draft, outline, summarize or value anything in this turn')
    const skill = flat(read(DEMAND_SKILL))
    expect(skill).toContain('**This skill never drafts in the turn.**')
    expect(skill).toContain('`demand_job_submit`')
    expect(skill).toContain('`reply_bind`')
    // The acknowledgment states only what is true, and never a time.
    expect(skill).toContain("I'll reply in this thread when they're filed.")
    expect(skill).toContain('No timing of any kind')
  })

  it("stays admin-reserved, and the requester is never the model's to say", () => {
    expect(bullet()).toContain('INITIATION AUTHORITY')
    expect(bullet()).toContain('Admin-classed')
    expect(bullet()).toContain("you never pass who asked or the request's words")
  })

  it('defines a demand by substance, and outranks send-as and drafting', () => {
    // The review's phrasings: none says "demand", every one is a demand. Each
    // must be named in BOTH texts as this class, and the send-as bullet must
    // hand a carrier letter back here.
    const evals = [
      'send State Farm a letter asking for the limits',
      'write the adjuster that we will settle for the policy by Friday',
      'put a 30-day offer to Geico in writing',
      'send it as me: a letter to the adjuster demanding the policy limits within 30 days',
    ]
    const b = bullet()
    const rubric = flat(read(RUBRIC))
    for (const phrase of evals) {
      expect(b, phrase).toContain(phrase)
      expect(rubric, phrase).toContain(phrase)
    }
    expect(b).toContain('**A demand is defined by its substance, never by the word.**')
    // Accepting a settlement is the attorney's decision, never a demand and never sent.
    expect(b).toContain('**Accepting a settlement is NOT a demand, and is not this class.**')
    expect(b).toContain('"tell the carrier we accept the limits"')
    expect(b).toContain("accepting a settlement is the responsible attorney's call")
    expect(rubric).toContain('**Accepting a settlement is NOT a demand, and is not this class.**')
    expect(b).not.toContain('tell the carrier we accept the limits if paid this month')
    expect(b).toContain('OUTRANKS the send-as and attorney drafting classes')
    const sendAs = flat(bulletFor(read(ROUTER), '**Send-as request**'))
    expect(sendAs).toContain('NEVER this class, whatever words carry it')
    expect(flat(classTable(read(ROUTER)))).toContain('substance, not the word')
  })

  it('is in the class table and the rubric', () => {
    expect(flat(classTable(read(ROUTER)))).toContain('Demand request')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('**demand request**')
    expect(rubric).toContain(`/app/skills/${DEMAND}/SKILL.md`)
    expect(rubric).toContain('A demand is NEVER this class')
  })
})

/**
 * The queued drafting class (2026-10-07). The firm asked the Operator to draft
 * its litigation documents in its attorney's house style. Like the demand,
 * each is a queued job, never drafted in a turn. On a seat without the lane the
 * same ask falls to the in-turn attorney drafting class, so the fallback is
 * pinned too: dropping it would leave such a seat refusing an attorney.
 */
describe('matter-inbox-router: the document drafting class', () => {
  const SLUG = 'document-drafter'
  const SKILL = `${SKILLS_DIR}/${SLUG}/SKILL.md`
  const bullet = () => flat(bulletFor(read(ROUTER), '**Document drafting request**'))

  it('is reachable on the email channel, queued and never drafted', () => {
    expect(bullet()).toContain(`/app/skills/${SLUG}/SKILL.md`)
    expect(bullet()).toContain('`drafting_job_submit`')
    expect(bullet()).toContain('NEVER draft, outline, summarize or value anything in this turn')
    expect(flat(classTable(read(ROUTER)))).toContain('Document drafting request')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('**document drafting request**')
    expect(rubric).toContain(`/app/skills/${SLUG}/SKILL.md`)
  })

  it('stays admin-reserved, takes no requester from the model, and is never a demand', () => {
    expect(bullet()).toContain('INITIATION AUTHORITY')
    expect(bullet()).toContain('Admin-classed')
    expect(bullet()).toContain("you never pass who asked or the request's words")
    expect(bullet()).toContain('A demand is NEVER this class')
    expect(bullet()).not.toContain('demand_job_submit')
  })

  it('takes a drafted memo DOCUMENT, never a note in the record', () => {
    // A "memo" in Smokeball is also a note entry. If the class took every
    // "memo" ask, "put a memo on the matter" would queue a paid drafting job.
    for (const text of [bullet(), flat(read(RUBRIC))]) {
      expect(text).toContain('**A memo here is a drafted memo DOCUMENT**')
      expect(text).toContain('is NOT this class and is never a paid job')
      expect(text).toContain('"add a note to file"')
      expect(text).toContain('"put a memo on the matter in Smokeball"')
    }
    expect(flat(classTable(read(ROUTER)))).toContain(
      'a drafted memo document (never a note in the record)'
    )
    expect(flat(read(SKILL))).toContain('never a paid job')
  })

  it('is named as outranking in-turn drafting in the attorney and send-as bullets', () => {
    expect(flat(bulletFor(read(ROUTER), '**Attorney drafting request**'))).toContain(
      'outranks this one per rubric rule 11'
    )
    expect(flat(bulletFor(read(ROUTER), '**Send-as request**'))).toContain(
      "wherever the seat's `document-drafter` loads, per rubric rule 11"
    )
  })

  it('falls to attorney drafting where the seat does not carry it', () => {
    expect(bullet()).toContain(
      'handle the ask as the attorney drafting request class below instead'
    )
    expect(flat(read(RUBRIC))).toContain(
      'the ask is the **attorney drafting request** class instead'
    )
  })

  it('names all five classes the broker accepts, and its DELIVER mode keeps failures from the client', () => {
    const skill = flat(read(SKILL))
    for (const klass of [
      'mediation_brief',
      'discovery_set',
      'discovery_response',
      'memo',
      'depo_outline',
    ]) {
      expect(skill, klass).toContain(`\`${klass}\``)
    }
    expect(skill).toContain('**This skill never drafts in the turn.**')
    expect(skill).toContain("Run the document-drafter skill's DELIVER mode for drafting job <id>.")
    expect(skill).toContain('Send the client NOTHING')
    expect(skill).toContain('`reply_bind` and ONLY `job_id`')
    expect(skill).toContain('**If the bind is refused, send NOTHING to anyone.**')
    expect(skill).toContain('`{{ATTORNEY}}`')
    expect(skill).toContain('`{{NOT IN RECORD}}`')
    expect(skill).toContain('`{{CLIENT}}`')
    expect(skill).toContain('caption')
    expect(skill).toContain('never ask the firm to narrow, split or change its request')
    // The runner records `<code>: <sentence>`; the firm hears the sentence only.
    expect(skill).toContain('Relay only the sentence AFTER the first `: `')
    expect(skill).toContain('never the code')
  })
})

/**
 * The queued litigation status class (2026-10-07). The firm asked for one
 * list of every case in litigation, each date citing its document. Like the
 * drafting lane it is a queued job, never assembled in a turn, and its email
 * carries counts only. It must never swallow one client's own status request.
 */
describe('matter-inbox-router: the litigation status class', () => {
  const SLUG = 'litigation-status'
  const SKILL = `${SKILLS_DIR}/${SLUG}/SKILL.md`
  const bullet = () => flat(bulletFor(read(ROUTER), '**Litigation status request**'))

  it('is reachable on the email channel, queued and never read in the turn', () => {
    expect(bullet()).toContain(`/app/skills/${SLUG}/SKILL.md`)
    expect(bullet()).toContain('`litigation_job_submit`')
    expect(bullet()).toContain("NEVER read, list, summarize or date any matter's court papers")
    expect(flat(classTable(read(ROUTER)))).toContain('Litigation status request')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('**litigation status request**')
    expect(rubric).toContain(`/app/skills/${SLUG}/SKILL.md`)
  })

  it('stays admin-reserved and takes no requester from the model', () => {
    expect(bullet()).toContain('INITIATION AUTHORITY')
    expect(bullet()).toContain('Admin-classed')
    expect(bullet()).toContain("you never pass who asked or the request's words")
  })

  it("never takes one matter's status, by precedence rule 12", () => {
    expect(bullet()).toContain("**This is the firm-wide list, never one matter's status.**")
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain(
      '12. **Litigation status versus status request, general and drafting.**'
    )
    expect(rubric).toContain('One client asking where their own matter stands stays status request')
  })

  it('carries the MAY-list entry and its DELIVER mode keeps failures from the client', () => {
    expect(flat(read(ROUTER))).toContain(
      "queue a **Named Administrator's litigation status request**"
    )
    const skill = flat(read(SKILL))
    expect(skill).toContain('**This skill never reads a matter in the turn.**')
    expect(skill).toContain(
      "Run the litigation-status skill's DELIVER mode for litigation job <id>."
    )
    expect(skill).toContain('Send the client NOTHING')
    expect(skill).toContain('Call `reply_bind` with ONLY `job_id`')
  })
})

describe('matter-inbox-router: the chronology class', () => {
  it('is reachable on the email channel', () => {
    const bullet = flat(bulletFor(read(ROUTER), '**Chronology package request**'))
    // A RUNTIME path. A repo path (operator/skills/...) does not exist on the
    // seat, so the read would fail on the one channel this class serves.
    expect(bullet).toContain(`/app/skills/${CHRONOLOGY}/SKILL.md`)
    expect(bullet).toContain('medchron_allowance')
    expect(flat(classTable(read(ROUTER)))).toContain('Chronology package request')
  })

  it('stays admin-reserved', () => {
    // The allowance is a commercial term. If a later edit keeps the class and
    // drops the reservation, the firm's page allowance is open to every
    // rostered sender and nothing else in the repo would notice.
    const bullet = flat(bulletFor(read(ROUTER), '**Chronology package request**'))
    expect(bullet).toContain('INITIATION AUTHORITY')
    expect(bullet).toContain('Admin-classed')
    expect(bullet).toContain('requested_by')
  })

  it('is not in the drafting lane', () => {
    // The skill is content_ceiling: surface_only, tagged NeverDraft, and its own
    // selector test pins that it DECLINES a drafting ask. Two plausible future
    // edits break that: "completing" the drafting lane map with the fifth PI
    // skill, or folding the chronology ask into the drafting bullet because
    // both execute in-turn.
    expect(read(DRAFTING_DISCIPLINE)).not.toContain(CHRONOLOGY)
    expect(bulletFor(read(ROUTER), '**Attorney drafting request**')).not.toContain(CHRONOLOGY)
  })
})

describe('matter-inbox-router: SKILL.md and the rubric agree', () => {
  it('gives every routing target a rubric row', () => {
    const rubric = read(RUBRIC)
    const missing = [...routingTargets(read(ROUTER))]
      .filter((slug) => !rubric.includes(slug) && !(slug in ROUTER_RUBRIC_EXEMPT))
      .sort()
    expect(missing, `routing targets with no rubric row: ${missing.join(', ')}`).toEqual([])
  })

  it('requires a real reason for any exemption', () => {
    for (const [slug, reason] of Object.entries(ROUTER_RUBRIC_EXEMPT)) {
      expect(reason.length, `${slug}'s exemption reason is too short to review`).toBeGreaterThan(20)
    }
  })

  it('scopes to routing regions only, never to the whole body', () => {
    // The falsifier, stated without a line number. Phase 1 names `inbox-triage`
    // to compare SHAPES ("the shape matches inbox-triage Phase 1"); it is not a
    // routing target. A region that swallowed it would demand a rubric row for
    // a class that does not exist, and the "fix" would be a bogus row.
    const body = read(ROUTER)
    expect(body).toContain('inbox-triage')
    expect([...routingTargets(body)]).not.toContain('inbox-triage')
  })

  it('cites a rubric that exists', () => {
    // Deliberately narrow. Dead `references/*.md` pointers in general are
    // already policed by tests/shipped-runtime-paths.test.ts, which carries a
    // reviewed freeze list (this router has two entries on it). A second gate
    // reaching a different verdict on the same fact is worse than one gate, so
    // this only asserts the file THIS suite reads.
    expect(read(ROUTER)).toContain('references/routing-rubric.md')
    expect(existsSync(RUBRIC)).toBe(true)
  })
})

describe('matter-inbox-router: the vendor invoice class', () => {
  const SLUG = 'vendor-invoice-intake'
  const bullet = () => flat(bulletFor(read(ROUTER), '**Vendor invoice intake**'))

  it('is reachable on the email channel, executed in-turn', () => {
    expect(bullet()).toContain(`/app/skills/${SLUG}/SKILL.md`)
    expect(flat(classTable(read(ROUTER)))).toContain('Vendor invoice intake')
    expect(read(RUBRIC)).toContain(`/app/skills/${SLUG}/SKILL.md`)
  })

  it('never writes for a sender outside the roster', () => {
    // A vendor mailing its own invoice to the Operator must never reach the
    // write. If a later edit drops this guard, anyone who can email the seat
    // can put an expense on a client's matter.
    expect(bullet()).toContain('outside the roster')
    expect(bullet()).toContain('nothing is written')
    expect(flat(read(RUBRIC))).toContain(
      'A non-roster sender (a vendor mailing its own invoice) never reaches it'
    )
  })

  it('treats the forward as the request and the forwarded text as data', () => {
    expect(bullet()).toContain('even with zero words of their own')
    expect(bullet()).toContain('"apply to matter X", "also pay"')
  })

  it('wins over payment/trust and document-received, and loses to service', () => {
    // The collisions the skill's selector test names. Each tie-break is pinned
    // in BOTH texts: the rubric is what the scheduled-poll channel reads and
    // SKILL.md is what the email channel reads.
    const b = bullet()
    expect(b).toContain('wins over **Payment / trust / retainer**')
    expect(b).toContain('**Document received**')
    expect(b).toContain('formal service of a captioned document is still served-document intake')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('Vendor invoice versus payment/trust and document-received')
    expect(rubric).toContain(
      'Formal service of a captioned litigation document stays served-document intake'
    )
  })

  it('never finalizes, never touches trust, never pays', () => {
    expect(bullet()).toContain('never finalizes, never touches trust, and never pays')
  })
})

describe('matter-inbox-router: the combined post class', () => {
  const SLUG = 'combined-post-intake'
  const POST = `${SKILLS_DIR}/${SLUG}/SKILL.md`
  const bullet = () => flat(bulletFor(read(ROUTER), '**Combined post intake**'))

  it('is reachable on the email channel, executed in-turn', () => {
    expect(bullet()).toContain(`/app/skills/${SLUG}/SKILL.md`)
    expect(flat(classTable(read(ROUTER)))).toContain('Combined post intake')
    expect(read(RUBRIC)).toContain(`/app/skills/${SLUG}/SKILL.md`)
  })

  it('selects a rostered bare PDF with an empty or boilerplate body', () => {
    // A front desk scans the post and sends it with no words. If the router
    // counts letters before routing, a one-letter scan falls to "document
    // received", which files nothing. Pinned in BOTH texts: the rubric is what
    // the scheduled-poll channel reads, SKILL.md is what the email channel reads.
    expect(bullet()).toContain('empty or boilerplate body')
    expect(bullet()).toContain('a scan holding one letter is a bundle of one')
    expect(flat(classTable(read(ROUTER)))).toContain('a PDF with an empty or boilerplate body')
    const rubric = flat(read(RUBRIC))
    expect(rubric).toContain('**A bare PDF is combined post.**')
    expect(rubric).toContain('bare scanned PDF is combined post intake, not this')
  })

  it('files a court paper in the bundle with a flag and never a deadline; keys the bills, never finalized', () => {
    // 2026-10-01: the firm asked that a medical bill in the day's post reach
    // the Medicals tab and a vendor's bill the expenses. Both are keyed by the
    // skill's own procedure, from the page, never finalized; a court paper is
    // still filed and flagged, and a deadline is still a person's act.
    const b = bullet()
    expect(b).toContain('filed on its resolved matter by the same rules as any letter')
    expect(b).toContain('it never sets a deadline')
    expect(b).toContain('staged as an UNFINALIZED expense from its own pages')
    expect(b).toContain(
      "a medical bill's figures go on the matter's Medicals tab after it is filed"
    )
    expect(b).toContain(
      'never finalized, never a figure the page does not print, never a row changed'
    )
    const post = flat(read(POST))
    expect(post).toContain('filed; court paper, needs calendaring')
    expect(post).toContain('staged as an expense of')
    expect(post).toContain('Medicals tab row added')
    expect(post).toContain('1 court paper filed and needs calendaring')
    expect(post).not.toContain('never filed here')
    expect(post).not.toContain('not entered as an expense')
    // And the skill body carries the two keying steps with the connector's
    // exact signatures, so prose and tool cannot drift apart silently.
    const skill = flat(read(`${SKILLS_DIR}/combined-post-intake/SKILL.md`))
    expect(skill).toContain(
      '`add_medicals_row(matter_id, source_file_id, provider_name, charge, service_start, service_end, account_number, claimant_index, patient_name)`'
    )
    expect(skill).toContain(
      '`stage_vendor_invoice(matter_id, matter_resolution, download_url, file_name, sha256, vendor, invoice_number, invoice_date, amount, first_page, last_page)`'
    )
    expect(skill).toContain('**Never writes a figure the page does not print.**')
    expect(skill).toContain('**Never finalizes, never pays, never changes a row.**')
  })
})

describe('matter-inbox-router: the file work class', () => {
  const SLUG = 'file-work-requests'
  const SKILL = `${SKILLS_DIR}/${SLUG}/SKILL.md`
  const bullet = () => flat(bulletFor(read(ROUTER), '**File work request**'))

  it('is reachable on the email channel, executed in-turn', () => {
    expect(bullet()).toContain(`/app/skills/${SLUG}/SKILL.md`)
    expect(flat(classTable(read(ROUTER)))).toContain('File work request')
    expect(read(RUBRIC)).toContain(`/app/skills/${SLUG}/SKILL.md`)
  })

  it('never writes for a sender outside the roster', () => {
    expect(bullet()).toContain(
      'a sender outside the roster never reaches this skill and nothing is written'
    )
    expect(flat(read(RUBRIC))).toContain('A non-roster sender never reaches it')
  })

  it('makes letters only from the firm forms, and never fills a gap', () => {
    // 2026-10-05: the firm asked for its rep letters "in that exact formatting".
    // If a later edit lets the model compose a letter or fill a missing fact,
    // a letter the firm did not make is filed over its name.
    expect(bullet()).toContain('never compose a letter')
    expect(bullet()).toContain('a refusal is reported, never replaced')
    const skill = flat(read(SKILL))
    expect(skill).toContain('`render_firm_form_letter(matter_id, form, date)`')
    expect(skill).toContain('`add_medicals_provider(matter_id, provider_name, address, note)`')
    expect(skill).toContain('Never pass a value from the email into a letter')
  })

  it("asks about a facility that could be two, and closes a task only on the sender's word", () => {
    // 2026-10-06: filing a document is not doing its task ("Mail DMV SR1 form"
    // is done when the SR1 is mailed), so a task closes only when the sender
    // says the item went out, and the SR1 is never signed for the client.
    expect(bullet()).toContain('is asked about and not written')
    expect(bullet()).toContain(
      "a Smokeball task is completed only when the sender's own email says that item went out"
    )
    expect(bullet()).toContain('the SR1 is never signed for the client')
    // 2026-10-06: the next three on a new file route here by their usual names.
    for (const name of [
      'declarations page',
      'med pay',
      'SR-19',
      'SR 19C',
      'med pay ledger',
      'wage loss',
      'police report',
      // 2026-10-06: a funder's intake form, by the names firms call it.
      'case evaluation form',
      'funding application',
      'cash advance application',
    ]) {
      expect(bullet()).toContain(name)
    }
    const skill = flat(read(SKILL))
    expect(skill).toContain('Never complete a Smokeball task except as step 5 says')
    expect(skill).toContain('Never complete it in the turn that files the document.')
    // 2026-10-06 rehearsal 7: the reply called the funding form's blanks "markers"; it prints none.
    expect(skill).toContain('This form prints NO marker')
    expect(skill).toContain('Quote every list exactly as returned')
  })
})

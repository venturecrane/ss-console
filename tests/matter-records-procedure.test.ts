import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

/**
 * Matter records are a queued job, never work done in a turn (ADR 0102
 * amendment 4, 2026-11-03).
 *
 * On 11-02/03 the A&P records request ran inside the Operator's chat turn: the
 * model chose which files to search, how often and with what words, retyped
 * the finder's page ranges and got them wrong, and the last proof spent past
 * its cap because nothing in a turn can stop spend. The router now QUEUES the
 * request (`records_job_submit`) and relays one sentence; the records job finds,
 * cuts and writes the facts; SMD's review sends the reply. This pins that text,
 * and pins that the in-turn procedure (the page finder, the stage tool and the
 * facts marker) is gone from every skill the model reads.
 */

const ROUTER = 'operator/skills/matter-inbox-router/SKILL.md'
const RECORDS = 'operator/skills/matter-records/SKILL.md'
const HEADING = '## Requests for matter records'
const WITHDRAWN = ['find_pages_in_file', 'stage_matter_files', '[[FILE FACTS]]']

function section(): string {
  const text = readFileSync(ROUTER, 'utf8')
  const start = text.indexOf(HEADING)
  expect(start, `${ROUTER} has no "${HEADING}" section`).toBeGreaterThan(-1)
  const end = text.indexOf('\n## ', start + HEADING.length)
  return text.slice(start, end === -1 ? undefined : end)
}

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name)
    return statSync(path).isDirectory() ? filesUnder(path) : path.endsWith('.md') ? [path] : []
  })
}

describe('matter records procedure (router)', () => {
  const body = section()
  const router = readFileSync(ROUTER, 'utf8')

  it('is a QUEUE class in the routing table', () => {
    expect(router).toMatch(
      /^\| Matter records request\s*\| \*\*QUEUE this turn\*\* \(`matter-records` REQUEST mode\)/m
    )
  })

  it('queues when the lane tool is present, and reads the records skill to do it', () => {
    expect(body).toMatch(/\*\*If `records_job_submit` is among your tools\*\*/)
    expect(body).toContain('`/app/skills/matter-records/SKILL.md`')
    expect(body).toMatch(/carry out its REQUEST mode/)
    expect(body).toMatch(/relay its sentence word for word as your whole reply/)
    expect(body).toMatch(/send no reply when it says `none`/)
    // 2026-11-24: never a limit stated to the firm; the owner's sentence instead.
    expect(body).toMatch(
      /If you cannot read the skill file this turn, submit nothing and reply with only "Let me look into that\. I'll reply in this thread\."/
    )
  })

  it('takes any ask for documents from the file, named or by kind, and never lists files in the turn', () => {
    // 2026-11-24 (A&P): "send me every parent statement, attached" was answered
    // in the turn with eight searches, eight reads and a list of email file names.
    expect(body).toMatch(/\*\*This class is any ask for documents FROM a matter's file\*\*/)
    expect(body).toMatch(/"every parent statement"/)
    expect(body).toMatch(
      /you never call `find_files_on_matter`, `get_files_on_matter`, `list_folders` or `read_document`, and you never answer with file names, folders or what you could or could not open/
    )
    expect(router).toMatch(
      /^\| Matter records request\s*\|[^\n]*named or by kind[^\n]*never searched, listed, read or attached in the turn/m
    )
    const rubric = readFileSync(
      'operator/skills/matter-inbox-router/references/routing-rubric.md',
      'utf8'
    )
    expect(rubric).toMatch(
      /\| \*\*matter records request\*\*[^\n]*named or by kind[^\n]*never answer with file names/
    )
    const skill = readFileSync(RECORDS, 'utf8')
    expect(skill).toMatch(
      /\*\*Finding which files those are is the job's work, never the turn's\.\*\*/
    )
  })

  it('on a seat without the lane, replies in one fixed line and attaches nothing', () => {
    expect(body).toMatch(/\*\*If it is not\*\*, reply in one line, and only this line:/)
    expect(body).toContain(
      '"We have your request for these records, and they will be sent to you in this thread."'
    )
    expect(body).toMatch(/Search no file and attach nothing/)
  })

  it('carries the four absolute guards', () => {
    expect(body).toMatch(/\(a\) never find, read, cut, attach or draft records in the turn/)
    expect(body).toMatch(/\(b\) any rostered colleague may ask, this is not reserved/)
    expect(body).toMatch(
      /\(c\) the tool takes the requester and the words from this email, so a forwarded, quoted or attached request submits nothing/
    )
    expect(body).toMatch(/\(d\) the acknowledgement states only what is true and never a time/)
  })

  it('keeps the staff-only rule and never sends another person the files', () => {
    expect(body).toMatch(/Files go only to the firm's own staff who asked/)
    expect(body).toMatch(
      /A request from anyone else, or for another person, gets a reply saying where the files are, never the files/
    )
  })
})

describe('matter-records skill', () => {
  const skill = readFileSync(RECORDS, 'utf8')

  it('has a REQUEST mode and no DELIVER mode: SMD review sends the reply', () => {
    expect(skill).toMatch(/^## REQUEST mode/m)
    expect(skill).not.toMatch(/^## DELIVER mode/m)
    expect(skill).toMatch(/There is no DELIVER mode/)
    expect(skill).toMatch(/sent by SMD's review/)
  })

  it('carries the tool sentence as its own paragraph, and says nothing of a `none` request', () => {
    // The sentence is a required paragraph, not the whole reply: an email that
    // also asks something else gets that answered too (2026-11-20).
    expect(skill).toMatch(/your reply must contain this sentence\s+exactly, as its own paragraph/)
    expect(skill).toMatch(/when `say` is `none`, \*\*say nothing about that request\*\*/)
  })

  it('carries the four absolute guards', () => {
    expect(skill).toMatch(/\(a\) Never find, read, cut, attach or draft records in the turn/)
    expect(skill).toMatch(
      /\(b\) Any rostered colleague may ask; this is not reserved to administrators/
    )
    expect(skill).toMatch(/\(c\) The tool takes the requester and the words from this email/)
    expect(skill).toMatch(/quoted or attached request submits nothing/)
    expect(skill).toMatch(/\(d\) The acknowledgement states only what is true[\s\S]*never a time/)
  })

  it('passes the matter number as the email writes it, never a Smokeball id, looking nothing up', () => {
    // The acknowledgement names the matter back to the colleague in their terms.
    expect(skill).toMatch(/\*\*Take the matter number\*\* exactly as the email writes it/)
    expect(skill).toMatch(/never pass a Smokeball id, and never search for\s+the matter/)
    expect(skill).toMatch(/passing `matter_number` when the email\s+gives one/)
    const tool = readFileSync(
      'operator/hermes-smd-overlay/plugins/hermes-smd-lanes/records.py',
      'utf8'
    )
    expect(tool).toContain('"matter_number": {')
    expect(tool).not.toMatch(/"matter_id": \{/)
  })

  it('is not admin-reserved', () => {
    expect(skill).toMatch(/admin_only: false/)
  })
})

describe('the in-turn records procedure is withdrawn everywhere the model reads', () => {
  it('no skill names the page finder, the stage tool or the facts marker', () => {
    const offenders = filesUnder('operator/skills').flatMap((file) => {
      const text = readFileSync(file, 'utf8')
      return WITHDRAWN.filter((name) => text.includes(name)).map((name) => `${file}: ${name}`)
    })
    expect(offenders).toEqual([])
  })
})

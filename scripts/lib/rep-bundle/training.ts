/**
 * The representative training and pitch practice, as plain text, for the rep
 * training bundle (scripts/build-rep-bundle.ts). Reads the seat's own training
 * data (operator/skills/rep-training/references and
 * operator/skills/pitch-practice/references) and writes it out in reading
 * order: curriculum, quiz, buyer types, scenarios, drills, rubric, tone rules,
 * discovery. The tone rules' match patterns are left out: they are code, and a
 * representative reads each rule's fix.
 */
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { parse } from 'yaml'

interface Cite {
  document: string
  section?: string
}
interface Lesson {
  id: string
  title: string
  read: Cite[]
  guide?: string
  exercise: string
}
interface Question {
  ask: string
  choices: Record<string, string>
  answer: string
  source: Cite
}
interface Curriculum {
  lessons: Lesson[]
  quiz: { pass_correct: number; questions: Question[] }
}
interface Scenario {
  role: string
  situation: string
  objections: { says: string; answer: Cite[] }[]
}
interface DrillTheme {
  title: string
  drills: { says: string; covers: string[]; answer: Cite[] }[]
}
interface ToneRule {
  id: string
  fix: string
}

const TRAINING_DIR = 'operator/skills/rep-training/references'
const PRACTICE_DIR = 'operator/skills/pitch-practice/references'

function cite(c: Cite): string {
  return c.section ? `${c.document}, section "${c.section}"` : c.document
}

/** Drop a markdown file's own top heading and demote its sections one level. */
function demote(md: string): string {
  return md
    .replace(/^# .*\n+/, '')
    .replace(/^(#+) /gm, '#$1 ')
    .trim()
}

function lessons(c: Curriculum): string[] {
  const out = ['## The lesson curriculum', '']
  c.lessons.forEach((l, i) => {
    const read = l.read.map(cite)
    if (l.guide) read.push(`the guide's own page ${l.guide}`)
    out.push(`### Lesson ${i + 1}: ${l.title}`, '', `Read: ${read.join('; ')}`, '')
    out.push(`Exercise: ${l.exercise.trim()}`, '')
  })
  return out
}

function quiz(c: Curriculum): string[] {
  const out = [
    '## The open-book quiz',
    '',
    `A representative passes with ${c.quiz.pass_correct} or more correct.`,
    '',
  ]
  c.quiz.questions.forEach((q, i) => {
    out.push(`### Question ${i + 1}`, '', q.ask.trim(), '')
    for (const [letter, text] of Object.entries(q.choices)) out.push(`- ${letter}: ${text}`)
    out.push(
      '',
      `Correct answer: ${q.answer}: ${q.choices[q.answer]}. Where the library answers it: ${cite(q.source)}.`,
      ''
    )
  })
  return out
}

function scenarios(list: Scenario[]): string[] {
  const out = [
    '## Practice scenarios: each buyer and the objections they raise',
    '',
    'In a mock pitch the guide plays one buyer and raises each objection in turn. Next to each objection are the library sections that hold the answer.',
    '',
  ]
  for (const s of list) {
    out.push(`### Buyer: ${s.role}`, '', s.situation.trim(), '', 'Objections:', '')
    for (const o of s.objections) {
      out.push(`- "${o.says}" Answered in: ${o.answer.map(cite).join('; ')}`)
    }
    out.push('')
  }
  return out
}

function drills(themes: DrillTheme[]): string[] {
  const out = [
    '## Objection drills',
    '',
    'A drill is one objection at a time. A good answer covers the points listed under it.',
    '',
  ]
  for (const t of themes) {
    out.push(`### Theme: ${t.title}`, '')
    for (const d of t.drills) {
      out.push(`Objection: "${d.says}"`, '', 'A good answer covers:', '')
      for (const c of d.covers) out.push(`- ${c}`)
      out.push('', `Answered in: ${d.answer.map(cite).join('; ')}`, '')
    }
  }
  return out
}

function toneRules(rules: ToneRule[]): string[] {
  const out = [
    "## Tone rules for a representative's own writing",
    '',
    'When a representative drafts something, it is checked against these rules. Each rule and the fix shown when it is broken:',
    '',
  ]
  for (const r of rules) out.push(`- ${r.id}: ${r.fix.trim()}`)
  out.push('')
  return out
}

function readYaml<T>(root: string, path: string): T {
  return parse(readFileSync(join(root, path), 'utf8')) as T
}

function readMd(root: string, path: string): string {
  return readFileSync(join(root, path), 'utf8')
}

/** The repo paths the training file is built from, in the order it reads them. */
export const TRAINING_SOURCES = [
  `${TRAINING_DIR}/curriculum.yaml`,
  `${PRACTICE_DIR}/buyer-types.md`,
  `${PRACTICE_DIR}/scenarios.yaml`,
  `${PRACTICE_DIR}/drills.yaml`,
  `${PRACTICE_DIR}/rubric.md`,
  `${TRAINING_DIR}/tone-rules.yaml`,
  `${TRAINING_DIR}/discovery.md`,
]

export function renderTraining(root: string): string {
  const curriculum = readYaml<Curriculum>(root, `${TRAINING_DIR}/curriculum.yaml`)
  const scen = readYaml<{ scenarios: Scenario[] }>(root, `${PRACTICE_DIR}/scenarios.yaml`)
  const dr = readYaml<{ themes: DrillTheme[] }>(root, `${PRACTICE_DIR}/drills.yaml`)
  const tone = readYaml<{ rules: ToneRule[] }>(root, `${TRAINING_DIR}/tone-rules.yaml`)
  const parts = [
    '# Representative training: lessons, quiz, buyer types, practice and rubric',
    '',
    "This file holds the training a new representative goes through with the guide: a lesson curriculum that reads the product library, an open-book quiz, the buyer types a representative will meet, practice scenarios and objection drills, the rubric answers are judged against, the tone rules for a representative's own drafts, and the discovery approach. Document names refer to the product library (file 05).",
    '',
    ...lessons(curriculum),
    ...quiz(curriculum),
    '## Buyer types',
    '',
    demote(readMd(root, `${PRACTICE_DIR}/buyer-types.md`)),
    '',
    ...scenarios(scen.scenarios),
    ...drills(dr.themes),
    '## The feedback rubric',
    '',
    demote(readMd(root, `${PRACTICE_DIR}/rubric.md`)),
    '',
    ...toneRules(tone.rules),
    '## Discovery practice',
    '',
    demote(readMd(root, `${TRAINING_DIR}/discovery.md`)),
  ]
  return parts.join('\n')
}

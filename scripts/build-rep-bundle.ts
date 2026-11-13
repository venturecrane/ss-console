/**
 * Builds the NotebookLM training bundle a new sales representative learns
 * from, entirely from this repository, so it tells the same capability story
 * as the site and the product guide and cannot drift from either.
 *
 *   npx tsx scripts/build-rep-bundle.ts [outDir]     (default ./dist/rep-bundle)
 *
 * Output: ten numbered .md files. Each begins with one line naming the repo
 * paths it was built from and the commit it was built at.
 *
 *   00 Read me first            docs/enablement/bundle/00-read-me-first.md
 *   01 Overview                 docs/enablement/bundle/01-overview.md
 *   02 Website main pages       the built site (see below)
 *   03 Website industry pages   the built site, every /packs/ page
 *   04 Website security, trust and the self-serve agent, with prices removed
 *   05 Product library          operator/skills/product-guide/references (INDEX first)
 *   06 Sales enablement kit     docs/enablement/figures-src/gen3.body.html, then docs/enablement/01..04
 *   07 Training and practice    operator/skills/{rep-training,pitch-practice}/references
 *   08 Glossary                 docs/enablement/bundle/12-glossary.md
 *   09 Common questions         docs/enablement/bundle/13-common-questions.md
 *
 * Website text is read from the site itself: `astro build` into a temporary
 * directory under dist/, `astro preview` to serve it in workerd, and each public page
 * fetched and reduced to its visible text (scripts/lib/rep-bundle/site.ts). The
 * page list comes from the built sitemap, and a public page with no place in
 * the bundle fails the build. On the self-serve agent page the pricing section
 * is skipped by id, any line still carrying a dollar amount is dropped, and the
 * build fails if one remains in 02 to 04.
 *
 * tests/rep-bundle.test.ts builds the bundle and checks it.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { htmlToText } from './lib/rep-bundle/html-text'
import { readSite, type SitePage } from './lib/rep-bundle/site'
import { renderTraining, TRAINING_SOURCES } from './lib/rep-bundle/training'

const ROOT = resolve(import.meta.dirname, '..')
const BUNDLE_DOCS = 'docs/enablement/bundle'
const LIBRARY = 'operator/skills/product-guide/references'
const EXPLAINER = 'docs/enablement/figures-src/gen3.body.html'
const KIT_PARTS = [
  'docs/enablement/01-what-it-is.md',
  'docs/enablement/02-how-it-works.md',
  'docs/enablement/03-how-an-engagement-goes.md',
  'docs/enablement/04-how-to-pitch-it.md',
]

/** A dollar amount: a dollar sign followed by a digit. */
const DOLLAR = /\$\s?\d/

interface BundleFile {
  name: string
  sources: string[]
  body: string
}

function commit(): string {
  const env: NodeJS.ProcessEnv = {}
  for (const [k, v] of Object.entries(process.env)) if (!k.startsWith('GIT_')) env[k] = v
  return execFileSync('git', ['rev-parse', '--short=12', 'HEAD'], { cwd: ROOT, env })
    .toString()
    .trim()
}

function read(path: string): string {
  return readFileSync(join(ROOT, path), 'utf8').trim()
}

function sitePages(heading: string, intro: string, pages: SitePage[]): string {
  const out = [`# ${heading}`, '', intro, '']
  for (const p of pages) {
    out.push(`# Page: ${p.title}`, '', `Address: https://smd.services${p.path}`, '', p.text, '')
  }
  return out.join('\n')
}

function withoutDollars(text: string): string {
  return text
    .split('\n')
    .filter((line) => !DOLLAR.test(line))
    .join('\n')
}

/** INDEX.md first, then the documents in the order INDEX lists them, then any it does not. */
function libraryOrder(): string[] {
  const all = readdirSync(join(ROOT, LIBRARY))
    .filter((f) => f.endsWith('.md'))
    .sort()
  const index = read(`${LIBRARY}/INDEX.md`)
  const listed = [...index.matchAll(/\]\(([a-z0-9-]+\.md)\)/g)].map((m) => m[1])
  const order = ['INDEX.md', ...new Set(listed.filter((f) => all.includes(f) && f !== 'INDEX.md'))]
  return [...order, ...all.filter((f) => !order.includes(f))]
}

function library(): BundleFile {
  const docs = libraryOrder()
  const parts = [
    '# The Operator product library',
    '',
    "This is the product library a sales representative's guide answers from: what the Operator is, how it works, and what SMD configures it to do. Each document below begins with its own top heading.",
    '',
  ]
  for (const d of docs) parts.push(read(`${LIBRARY}/${d}`), '')
  return {
    name: '05 Product library (what the guide answers from).md',
    sources: [`${LIBRARY}/ (${docs.join(', ')})`],
    body: parts.join('\n'),
  }
}

function enablementKit(): BundleFile {
  const explainer = htmlToText(read(EXPLAINER).replace(/\{\{FIG\d+\}\}/g, ''))
  const parts = [
    '# The sales enablement kit',
    '',
    'Written for a sales representative getting to know SMD Services and the Operator. First the product explainer, How the Operator Works (seven views, for any business). Then four parts written for one industry, law firms, as the example of how the general model is pitched in one vertical.',
    '',
    '# Part 0: How the Operator Works (the product explainer)',
    '',
    explainer,
    '',
  ]
  for (const p of KIT_PARTS) parts.push(read(p), '')
  return {
    name: '06 Sales enablement kit.md',
    sources: [EXPLAINER, ...KIT_PARTS],
    body: parts.join('\n'),
  }
}

function websiteFiles(site: Awaited<ReturnType<typeof readSite>>): BundleFile[] {
  const pages = (ps: SitePage[]) => ps.map((p) => p.path).join(', ')
  const files: BundleFile[] = [
    {
      name: '02 Website - main pages.md',
      sources: [`the built site, pages ${pages(site.main)}`],
      body: sitePages(
        'The SMD Services website: the main pages',
        'This file holds the words a visitor reads on the public website smd.services, page by page, read from the built site. Navigation, buttons and legal pages are left out. Each page starts with a line naming its address.',
        site.main
      ),
    },
    {
      name: '03 Website - industry pages.md',
      sources: [`the built site, pages ${pages(site.industry)}`],
      body: sitePages(
        'The SMD Services website: the industry pages',
        'Each industry page shows the seat the Operator fills in that kind of business: the work it takes on and the line it never crosses. Read from the built site.',
        site.industry
      ),
    },
    {
      name: '04 Website - security, trust and the self-serve agent.md',
      sources: [`the built site, pages ${pages(site.trust)} (prices removed)`],
      body: withoutDollars(
        sitePages(
          'The SMD Services website: security, trust, AI disclosure and the self-serve agent',
          'The security, trust and AI disclosure pages, and the self-serve agent page with its pricing section removed. Read from the built site.',
          site.trust
        )
      ),
    },
  ]
  for (const f of files) {
    const hit = f.body.split('\n').find((l) => DOLLAR.test(l))
    if (hit) throw new Error(`${f.name} carries a dollar amount: ${hit}`)
  }
  return files
}

function bundleDoc(name: string, file: string): BundleFile {
  const path = `${BUNDLE_DOCS}/${file}`
  return { name, sources: [path], body: read(path) }
}

/** Build the bundle into `outDir`, replacing an earlier bundle there. Returns the file names written. */
export async function buildRepBundle(outDir: string): Promise<string[]> {
  const sha = commit()
  const site = await readSite(ROOT)
  const files: BundleFile[] = [
    bundleDoc('00 README - read me first.md', '00-read-me-first.md'),
    bundleDoc('01 What the Operator is - a plain overview for new reps.md', '01-overview.md'),
    ...websiteFiles(site),
    library(),
    enablementKit(),
    {
      name: '07 Representative training and pitch practice.md',
      sources: TRAINING_SOURCES,
      body: renderTraining(ROOT),
    },
    bundleDoc('08 Glossary - terms a rep will hear.md', '12-glossary.md'),
    bundleDoc(
      '09 Common questions from business owners, with answers.md',
      '13-common-questions.md'
    ),
  ]
  // Replace only an earlier bundle's numbered files; nothing else in outDir is touched.
  mkdirSync(outDir, { recursive: true })
  for (const old of readdirSync(outDir)) {
    if (/^\d\d .*\.md$/.test(old)) rmSync(join(outDir, old))
  }
  for (const f of files) {
    const note = `Source: built from ${f.sources.join('; ')} at commit ${sha} of the SMD Services repository.`
    writeFileSync(join(outDir, f.name), `${note}\n\n${f.body.trim()}\n`)
  }
  return files.map((f) => f.name)
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(import.meta.filename)) {
  const outDir = resolve(process.argv[2] ?? join(ROOT, 'dist', 'rep-bundle'))
  const names = await buildRepBundle(outDir)
  process.stdout.write(`wrote ${names.length} files to ${outDir}\n`)
  for (const n of names) process.stdout.write(`  ${n}\n`)
}

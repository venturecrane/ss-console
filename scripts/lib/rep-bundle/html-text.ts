/**
 * Visible text from HTML, as plain markdown-ish lines, for the rep training
 * bundle (scripts/build-rep-bundle.ts).
 *
 * A small tag walker, not a full parser: the input is SMD's own rendered pages
 * and its own explainer body, so the HTML is well formed. It keeps what a
 * visitor reads and drops what they do not: scripts, styles, SVG drawings,
 * form controls, buttons, anything marked hidden or aria-hidden, and any
 * element whose id the caller names (the self-serve page's pricing section).
 * Headings become markdown headings one level below the page title, list items
 * become "- " lines, table cells are joined with " | ".
 */

const VOID = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'source',
  'track',
  'wbr',
])

const SKIP_TAGS = new Set([
  'script',
  'style',
  'svg',
  'noscript',
  'template',
  'button',
  'select',
  'textarea',
  'iframe',
  'head',
])

const BLOCK = new Set([
  'p',
  'div',
  'section',
  'article',
  'aside',
  'main',
  'header',
  'footer',
  'ul',
  'ol',
  'li',
  'table',
  'thead',
  'tbody',
  'tr',
  'figure',
  'figcaption',
  'blockquote',
  'dl',
  'dt',
  'dd',
  'form',
  'fieldset',
  'legend',
  'label',
  'details',
  'summary',
  'br',
  'hr',
  'nav',
])

const HEADING_PREFIX: Record<string, string> = {
  h1: '## ',
  h2: '### ',
  h3: '#### ',
  h4: '##### ',
  h5: '##### ',
  h6: '##### ',
}

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  rsquo: '\u2019',
  lsquo: '\u2018',
  rdquo: '\u201d',
  ldquo: '\u201c',
  hellip: '\u2026',
  middot: '\u00b7',
  rarr: '\u2192',
  larr: '\u2190',
  copy: '\u00a9',
  ndash: '\u2013',
  times: '\u00d7',
}

function decodeEntities(text: string): string {
  return text.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, body: string) => {
    if (body[0] === '#') {
      const code =
        body[1] === 'x' || body[1] === 'X'
          ? parseInt(body.slice(2), 16)
          : parseInt(body.slice(1), 10)
      return Number.isFinite(code) ? String.fromCodePoint(code) : whole
    }
    return NAMED_ENTITIES[body.toLowerCase()] ?? whole
  })
}

/** Every attribute of a tag, by lower-cased name; a bare attribute maps to ''. */
function parseAttrs(attrs: string): Map<string, string> {
  const out = new Map<string, string>()
  const re = /([^\s"'=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g
  for (let m = re.exec(attrs); m; m = re.exec(attrs)) {
    out.set(m[1].toLowerCase(), m[2] ?? m[3] ?? m[4] ?? '')
  }
  return out
}

/**
 * An inline element styled to sit on its own line (a heading split across
 * `<span class="block">` lines): its words must not run into the next ones.
 */
function isLaidOutAsBlock(attrs: string): boolean {
  const cls = parseAttrs(attrs).get('class') ?? ''
  return cls.split(/\s+/).some((c) => /^(?:[a-z]+:)?(?:block|flex|grid|inline-block)$/.test(c))
}

function isSkipped(tag: string, attrs: string, skipIds: ReadonlySet<string>): boolean {
  if (SKIP_TAGS.has(tag)) return true
  const a = parseAttrs(attrs)
  if (a.has('hidden') || a.get('aria-hidden') === 'true') return true
  const id = a.get('id')
  if (id !== undefined && skipIds.has(id)) return true
  // A link to a skipped section goes with it ("See Pricing" when pricing is skipped).
  const href = a.get('href')
  return tag === 'a' && href !== undefined && href.startsWith('#') && skipIds.has(href.slice(1))
}

/** Tidy the raw stream: one space between words, no blank-line runs, no empty list items. */
function tidy(raw: string): string {
  const lines = raw
    .split('\n')
    .map((l) =>
      l
        .replace(/[ \t\u00a0]+/g, ' ')
        .replace(/ ([.,;:!?)\u2019])/g, '$1')
        .replace(/([(\u2018\u201c]) /g, '$1')
        .trim()
    )
    .map((l) => l.replace(/^(\| )+/, '').replace(/( \|)+$/, ''))
    .filter((l) => l !== '-' && l !== '|' && !/^#+$/.test(l))
  const out: string[] = []
  for (const line of lines) {
    if (line === '' && (out.length === 0 || out[out.length - 1] === '')) continue
    out.push(line)
  }
  while (out.length && out[out.length - 1] === '') out.pop()
  return out.join('\n')
}

/** The slice of `html` inside the first `<scope>` element. */
function scoped(html: string, scope: string): string {
  const lower = html.toLowerCase()
  const open = `<${scope.toLowerCase()}`
  let start = lower.indexOf(open)
  while (start !== -1 && !/[\s>]/.test(lower.charAt(start + open.length))) {
    start = lower.indexOf(open, start + 1)
  }
  const end = lower.lastIndexOf(`</${scope.toLowerCase()}>`)
  if (start === -1 || end === -1) throw new Error(`htmlToText: no <${scope}> element`)
  return html.slice(start, end)
}

interface Open {
  tag: string
  skip: boolean
  spaced: boolean
}

/** Walks tags in order, writing the visible text of what is not skipped. */
class TextWalker {
  private readonly stack: Open[] = []
  private skipDepth = 0
  private pre = 0
  out = ''

  constructor(private readonly skipIds: ReadonlySet<string>) {}

  text(raw: string): void {
    if (this.skipDepth === 0) this.out = appendText(this.out, raw, this.pre > 0)
  }

  close(tag: string): void {
    const i = this.stack.map((s) => s.tag).lastIndexOf(tag)
    if (i === -1) return
    if (tag === 'pre') this.pre = Math.max(0, this.pre - 1)
    const closed = this.stack.splice(i)
    for (const s of closed) if (s.skip) this.skipDepth--
    if (this.skipDepth === 0) this.out += closed[0].spaced ? ' ' : closeText(tag)
  }

  open(tag: string, attrs: string): void {
    const selfClosing = VOID.has(tag) || attrs.trimEnd().endsWith('/')
    const skip = isSkipped(tag, attrs, this.skipIds)
    const spaced =
      tag === 'a' || (!BLOCK.has(tag) && !HEADING_PREFIX[tag] && isLaidOutAsBlock(attrs))
    if (tag === 'pre') this.pre++
    if (!selfClosing) {
      this.stack.push({ tag, skip, spaced })
      if (skip) this.skipDepth++
    }
    if (this.skipDepth === 0 && !skip) this.out += spaced ? ' ' : openText(tag)
  }
}

/**
 * The visible text of `html`. When `scope` names a tag (for a page, `main`),
 * only the first such element is read.
 */
export function htmlToText(
  html: string,
  opts: { scope?: string; skipIds?: readonly string[] } = {}
): string {
  const source = opts.scope ? scoped(html, opts.scope) : html
  const walker = new TextWalker(new Set(opts.skipIds ?? []))
  const tagRe = /<!--[\s\S]*?-->|<(\/?)([a-zA-Z][a-zA-Z0-9-]*)([^>]*)>/g
  let last = 0
  for (let m = tagRe.exec(source); m; m = tagRe.exec(source)) {
    walker.text(source.slice(last, m.index))
    last = tagRe.lastIndex
    if (!m[2]) continue
    if (m[1] === '/') walker.close(m[2].toLowerCase())
    else walker.open(m[2].toLowerCase(), m[3] ?? '')
  }
  walker.text(source.slice(last))
  return tidy(walker.out)
}

/**
 * Append a text node. Two words that meet across a tag boundary with nothing
 * between them (`<b>Skills</b><span>The kinds</span>`) get a space, because on
 * the page they sit in separate boxes. Inside <pre>, line breaks are kept.
 */
function appendText(out: string, raw: string, inPre: boolean): string {
  const decoded = decodeEntities(raw)
  const text = inPre ? decoded.replace(/[ \t]+/g, ' ') : decoded.replace(/\s+/g, ' ')
  if (text === '') return out
  return /[A-Za-z0-9.,;:]$/.test(out) && /^[A-Za-z0-9]/.test(text) ? `${out} ${text}` : out + text
}

function openText(tag: string): string {
  if (HEADING_PREFIX[tag]) return `\n\n${HEADING_PREFIX[tag]}`
  if (tag === 'li') return '\n- '
  if (tag === 'p') return '\n\n'
  if (tag === 'td' || tag === 'th') return ' | '
  return BLOCK.has(tag) ? '\n' : ''
}

function closeText(tag: string): string {
  if (HEADING_PREFIX[tag] || tag === 'p') return '\n\n'
  if (tag === 'td' || tag === 'th') return ''
  return BLOCK.has(tag) ? '\n' : ''
}

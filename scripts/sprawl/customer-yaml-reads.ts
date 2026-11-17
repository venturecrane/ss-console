/**
 * Does a Python file parse customer.yaml itself? The detector behind the
 * `customer-yaml-hand-parsers` concept in scripts/sprawl/concepts.ts.
 *
 * The first rule ("calls a yaml load AND names customer.yaml anywhere") counted
 * 13 files that load another YAML file (seats.yaml, role.yaml, a profile
 * config.yaml, a firm config, a SKILL.md front matter) and only mention
 * customer.yaml in a docstring. This one counts a file only when a yaml load is
 * APPLIED to customer.yaml, followed as far as a reading of the text can follow
 * it honestly:
 *
 *   1. An expression says customer.yaml when it holds an identifier such as
 *      `customer_yaml_path` or `CUSTOMER_YAML`, a string that is a path ending
 *      in customer.yaml ("customer.yaml", "/var/lib/smd-config/customer.yaml",
 *      f"{root}/customer.yaml"), or the variable name SMD_CUSTOMER_YAML_PATH.
 *      A string that only mentions it ("customer.yaml unreadable", the flag
 *      "--customer-yaml") says nothing.
 *   2. A name is tainted when it is bound (`x = ...`, `with ... as x`,
 *      `for x in ...`) from an expression that says customer.yaml or holds a
 *      tainted name. Names bound at module level are tainted for the whole
 *      file; names bound in a function only there.
 *   3. A function whose returned value is tainted taints its own name, so
 *      `target = path or live_path()` is tainted when live_path returns one.
 *   4. A function whose docstring's FIRST line names customer.yaml says what its
 *      parameters are, so they are tainted inside it (`def read(path):
 *      """The parsed customer.yaml..."""`). A mention further down a
 *      docstring, or in a module docstring, taints nothing.
 *   5. The file counts when a yaml load (`yaml.safe_load(`, `yaml.load(`,
 *      `safe_load(`) has a tainted argument, or when it calls one of its own
 *      functions with a tainted value in the position of a parameter that
 *      function loads, itself or by passing it on to another such function
 *      (`run(yaml_path)` -> `staff_addresses(yaml_text)` -> the load).
 *
 * Triple-quoted strings are blanked first, so docstring prose is never read as
 * code. What it cannot follow: a path that reaches the load only through
 * another module, or through a parameter of a function whose docstring does not
 * say what it reads. Known answers in both directions are in
 * tests/sprawl-concepts.test.ts.
 */

/** An identifier that names customer.yaml, or its env variable. */
const NAME_SAYS = /customer_ya?ml|SMD_CUSTOMER_YAML/i

/** A string literal's content that is a path to customer.yaml, or the env variable's name. */
const STRING_SAYS = /(^|[/}])customer\.ya?ml$|^SMD_CUSTOMER_YAML/

/** A docstring first line that names customer.yaml. */
const DOC_SAYS = /customer[._]ya?ml/i

const LOAD_CALL = /\b(?:yaml\.(?:safe_)?load|(?<![\w.])safe_load)\(/g

/** A bare call `name(` (not a method `x.name(`). */
const CALL = /(?<![\w.])([A-Za-z_]\w*)\(/g

const DEF = /^(\s*)(?:async\s+)?def\s+(\w+)\s*\(/

const STRING = /[rRbBfFuU]{0,2}(['"])((?:\\.|(?!\1).)*)\1/g

type Fn = {
  name: string
  indent: number
  params: string[]
  doc: string
  lines: string[]
}

/** Blank every triple-quoted string, keeping its length and newlines. */
function blankTripleStrings(text: string): string {
  return text.replace(/("""|''')[\s\S]*?\1/g, (m) => m.replace(/[^\n]/g, ' '))
}

/** The first line of the docstring that opens at `raw` index `at`, or ''. */
function docAfter(raw: string, at: number): string {
  const m = /^\s*[rRbBuU]?("""|''')\s*([^\n]*)/.exec(raw.slice(at))
  if (!m) return ''
  return m[2].split(m[1])[0]
}

/** Join physical lines into logical ones while brackets are open; `end` is the offset after the last. */
function logicalLines(text: string): { line: string; end: number }[] {
  const out: { line: string; end: number }[] = []
  let buf = ''
  let depth = 0
  let offset = 0
  for (const line of text.split('\n')) {
    buf = buf ? `${buf} ${line.trim()}` : line
    offset += line.length + 1
    for (const ch of line.replace(STRING, '""')) {
      if ('([{'.includes(ch)) depth += 1
      else if (')]}'.includes(ch)) depth = Math.max(0, depth - 1)
    }
    if (depth === 0) {
      out.push({ line: buf, end: offset })
      buf = ''
    }
  }
  if (buf) out.push({ line: buf, end: offset })
  return out
}

/** The text inside the bracket that opens at `open` (an index of '('). */
function argsAt(line: string, open: number): string {
  let depth = 0
  for (let i = open; i < line.length; i++) {
    if (line[i] === '(') depth += 1
    else if (line[i] === ')') {
      depth -= 1
      if (depth === 0) return line.slice(open + 1, i)
    }
  }
  return line.slice(open + 1)
}

/** Split an argument list on its top-level commas. */
function topLevel(args: string): string[] {
  const out: string[] = []
  let depth = 0
  let cur = ''
  for (const ch of args.replace(STRING, (m) => m.replace(/,/g, ' '))) {
    if ('([{'.includes(ch)) depth += 1
    else if (')]}'.includes(ch)) depth -= 1
    if (ch === ',' && depth === 0) {
      out.push(cur.trim())
      cur = ''
    } else cur += ch
  }
  if (cur.trim()) out.push(cur.trim())
  return out
}

function paramsOf(line: string): string[] {
  return topLevel(argsAt(line, line.indexOf('(')))
    .map((p) => p.replace(/^\*+/, '').split(/[:=]/)[0].trim())
    .filter((p) => /^\w+$/.test(p) && p !== 'self' && p !== 'cls')
}

/** Identifiers outside string literals. */
function wordsIn(expr: string): string[] {
  return expr.replace(STRING, '""').match(/[A-Za-z_]\w*/g) ?? []
}

function says(expr: string): boolean {
  if (wordsIn(expr).some((w) => NAME_SAYS.test(w))) return true
  return [...expr.matchAll(STRING)].some((m) => STRING_SAYS.test(m[2]))
}

function taintedExpr(expr: string, tainted: Set<string>): boolean {
  return says(expr) || wordsIn(expr).some((w) => tainted.has(w))
}

/** Names a logical line binds, and the expression it binds them from. */
function bindings(line: string): { names: string[]; from: string } | null {
  const withAs = /^\s*(?:async\s+)?with\s+(.+?)\s+as\s+(\w+)\s*:/.exec(line)
  if (withAs) return { names: [withAs[2]], from: withAs[1] }
  const forIn = /^\s*for\s+([\w\s,()]+?)\s+in\s+(.+):\s*$/.exec(line)
  if (forIn) return { names: wordsIn(forIn[1]), from: forIn[2] }
  const assign = /^\s*([A-Za-z_][\w\s,]*?)\s*(?::\s*[^=]+?)?\s*=(?!=)\s*(.+)$/.exec(line)
  if (assign && !/^\s*(if|elif|while|return|assert|def|class|lambda)\b/.test(line)) {
    return { names: wordsIn(assign[1]), from: assign[2] }
  }
  return null
}

function loadArgs(line: string): string[] {
  return [...line.matchAll(LOAD_CALL)].map((m) => argsAt(line, (m.index ?? 0) + m[0].length - 1))
}

/** The arguments of each bare call to `name` on the line, split. */
function callsTo(line: string, name: string): string[][] {
  return [...line.matchAll(CALL)]
    .filter((m) => m[1] === name)
    .map((m) => topLevel(argsAt(line, (m.index ?? 0) + m[0].length - 1)))
}

/** The arguments of a call that land on the parameters in `loaded`. */
function argsOnto(args: string[], fn: Fn, loaded: Set<string>): string[] {
  return args.filter((arg, i) => {
    const kw = /^(\w+)\s*=(?!=)\s*([\s\S]*)$/.exec(arg)
    if (kw) return loaded.has(kw[1])
    return !arg.startsWith('*') && loaded.has(fn.params[i])
  })
}

/** Split a module into its module-level lines and its functions (nested ones separately). */
function split(raw: string): { module: string[]; fns: Fn[] } {
  const module: string[] = []
  const fns: Fn[] = []
  const stack: Fn[] = []
  for (const { line, end } of logicalLines(blankTripleStrings(raw))) {
    if (!line.trim()) continue
    const indent = line.length - line.trimStart().length
    while (stack.length && indent <= stack[stack.length - 1].indent) stack.pop()
    const def = DEF.exec(line)
    if (def) {
      const fn: Fn = {
        name: def[2],
        indent,
        params: paramsOf(line),
        doc: docAfter(raw, end),
        lines: [],
      }
      fns.push(fn)
      stack.push(fn)
      continue
    }
    if (stack.length) stack[stack.length - 1].lines.push(line)
    else module.push(line)
  }
  return { module, fns }
}

/** For one parameter, the names in its function that hold it or a value made from it. */
function derivedFrom(fn: Fn, param: string): Set<string> {
  const out = new Set([param])
  for (let changed = true; changed;) {
    changed = false
    for (const line of fn.lines) {
      const b = bindings(line)
      if (!b || !wordsIn(b.from).some((w) => out.has(w))) continue
      for (const n of b.names) {
        if (!out.has(n)) {
          out.add(n)
          changed = true
        }
      }
    }
  }
  return out
}

/** Per function, the parameters it loads as YAML, itself or through another function of the file. */
function loadedParams(fns: Fn[]): Map<Fn, Set<string>> {
  const loaded = new Map<Fn, Set<string>>(fns.map((f) => [f, new Set<string>()]))
  const derived = new Map(fns.map((f) => [f, f.params.map((p) => [p, derivedFrom(f, p)] as const)]))
  for (let changed = true; changed;) {
    changed = false
    for (const fn of fns) {
      const mine = loaded.get(fn) as Set<string>
      for (const [param, names] of derived.get(fn) ?? []) {
        if (mine.has(param)) continue
        const holds = (arg: string) => wordsIn(arg).some((w) => names.has(w))
        const hit = fn.lines.some(
          (line) =>
            loadArgs(line).some(holds) ||
            fns.some((g) =>
              callsTo(line, g.name).some((args) =>
                argsOnto(args, g, loaded.get(g) as Set<string>).some(holds)
              )
            )
        )
        if (hit) {
          mine.add(param)
          changed = true
        }
      }
    }
  }
  return loaded
}

export function parsesCustomerYaml(code: string[]): boolean {
  const text = code.join('\n')
  // No load anywhere, nothing to follow (and most files stop here).
  if (!/\b(?:yaml\.(?:safe_)?load|safe_load)\(/.test(text)) return false
  const { module, fns } = split(text)
  const global = new Set<string>()
  const local = new Map<Fn, Set<string>>(
    fns.map((f) => [f, new Set(DOC_SAYS.test(f.doc) ? f.params : [])])
  )
  const scopeOf = (fn: Fn | null) => (fn ? new Set([...global, ...(local.get(fn) ?? [])]) : global)

  // Fixpoint over bindings and tainted returns; each pass only adds names.
  for (let changed = true; changed;) {
    changed = false
    const visit = (lines: string[], fn: Fn | null) => {
      const into = fn ? (local.get(fn) as Set<string>) : global
      for (const line of lines) {
        const scope = scopeOf(fn)
        const b = bindings(line)
        if (b && taintedExpr(b.from, scope)) {
          for (const n of b.names) {
            if (!scope.has(n)) {
              into.add(n)
              changed = true
            }
          }
        }
        const ret = /^\s*return\s+(.+)$/.exec(line)
        if (fn && ret && taintedExpr(ret[1], scope) && !global.has(fn.name)) {
          global.add(fn.name)
          changed = true
        }
      }
    }
    visit(module, null)
    for (const fn of fns) visit(fn.lines, fn)
  }

  const loaded = loadedParams(fns)
  const scopes: [string[], Fn | null][] = [
    [module, null],
    ...fns.map((f) => [f.lines, f] as [string[], Fn]),
  ]
  for (const [lines, fn] of scopes) {
    const scope = scopeOf(fn)
    const tainted = (arg: string) => taintedExpr(arg, scope)
    for (const line of lines) {
      if (loadArgs(line).some(tainted)) return true
      for (const g of fns) {
        const onto = loaded.get(g) as Set<string>
        if (
          onto.size &&
          callsTo(line, g.name).some((args) => argsOnto(args, g, onto).some(tainted))
        )
          return true
      }
    }
  }
  return false
}

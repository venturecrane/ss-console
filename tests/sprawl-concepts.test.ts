/**
 * Known answers for every detector in scripts/sprawl/concepts.ts, in both
 * directions: a file that is an instance and a near miss that is not. The
 * fixtures are inline strings, never repo files, so a pass here says the rule
 * does what its `rule` text claims, independent of the tree.
 */
import { describe, expect, it } from 'vitest'
import { CONCEPTS, conceptCensus, conceptRatchet } from '../scripts/sprawl/concepts'

type File = { path: string; text: string }

function found(id: string, files: File[]): string[] {
  const concept = CONCEPTS.find((c) => c.id === id)
  if (!concept) throw new Error(`no concept ${id}`)
  return concept.instances(files)
}

describe('job-lane-lists', () => {
  it('counts a quoted list, a dict, a shell case and a bare one-line list', () => {
    const files = [
      { path: 'a/quoted.py', text: 'LANES = ("medchron", "demand", "drafting", "records")' },
      {
        path: 'a/keys.ts',
        text: 'const T = {\n  medchron: 1,\n  demand: 2,\n  note: 3,\n  casefile: 4,\n}',
      },
      {
        path: 'a/case.sh',
        text: 'case "$1" in\n  medchron) x ;;\n  demand) x ;;\n  drafting) x ;;\n  litigation) x ;;\nesac',
      },
      { path: 'a/bare.sh', text: '_ALL_LANES="medchron demand drafting litigation"' },
    ]
    expect(found('job-lane-lists', files)).toEqual([
      'a/bare.sh',
      'a/case.sh',
      'a/keys.ts',
      'a/quoted.py',
    ])
  })

  it('ignores three lanes, comments, skill names that contain a lane word, tests and docs', () => {
    const files = [
      { path: 'b/three.py', text: 'X = ("medchron", "demand", "drafting")' },
      { path: 'b/comment.py', text: '# medchron demand drafting litigation records' },
      {
        path: 'b/skills.py',
        text: 'S = "medical-records-chaser records-order-chase demand-letter-drafter note-taker"',
      },
      { path: 'b/tests/test_x.py', text: 'L = ["medchron", "demand", "drafting", "records"]' },
      { path: 'b/doc.md', text: 'medchron demand drafting litigation' },
    ]
    expect(found('job-lane-lists', files)).toEqual([])
  })
})

describe('lane-skill-tables', () => {
  it('counts four lane-to-skill pairs, one per line or all on one line', () => {
    const files = [
      {
        path: 'c/table.py',
        text: [
          '"medchron": "medical-chronology-maintainer",',
          '"demand": "demand-letter-drafter",',
          '"drafting": "document-drafter",',
          '"records": "matter-records",',
        ].join('\n'),
      },
      {
        path: 'c/oneline.sh',
        text: 'case "$1" in medchron) s=medical-chronology-maintainer ;; demand) s=demand-letter-drafter ;; drafting) s=document-drafter ;; casefile) s=case-file-manager ;; esac',
      },
    ]
    expect(found('lane-skill-tables', files)).toEqual(['c/oneline.sh', 'c/table.py'])
  })

  it('ignores three pairs, and lanes and skills on different lines', () => {
    const files = [
      {
        path: 'd/three.py',
        text: '"medchron": "medical-chronology-maintainer",\n"demand": "demand-letter-drafter",\n"drafting": "document-drafter",',
      },
      {
        path: 'd/apart.py',
        text: 'L = ["medchron", "demand", "drafting", "records"]\nS = ["medical-chronology-maintainer", "demand-letter-drafter", "document-drafter", "matter-records"]',
      },
    ]
    expect(found('lane-skill-tables', files)).toEqual([])
  })
})

describe('customer-yaml-hand-parsers', () => {
  it('counts a Python file that loads yaml and names customer.yaml', () => {
    const files = [
      {
        path: 'e/a.py',
        text: 'import yaml\ncfg = yaml.safe_load(open("/opt/data/customer.yaml"))',
      },
      {
        path: 'e/b.py',
        text: 'from yaml import safe_load\nd = safe_load(CUSTOMER_YAML.read_text())',
      },
    ]
    expect(found('customer-yaml-hand-parsers', files)).toEqual(['e/a.py', 'e/b.py'])
  })

  it('ignores a yaml load of another file, a mention without a load, and TypeScript', () => {
    const files = [
      { path: 'f/other.py', text: 'yaml.safe_load(open("seat-layout.yaml"))' },
      { path: 'f/mention.py', text: 'PATH = "customer.yaml"' },
      { path: 'f/x.ts', text: 'yaml.load(read("customer.yaml"))' },
    ]
    expect(found('customer-yaml-hand-parsers', files)).toEqual([])
  })
})

describe('broker-socket-clients', () => {
  it('counts a Python file that opens a unix socket and connects', () => {
    const files = [
      {
        path: 'g/client.py',
        text: 's = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)\ns.connect(SOCK)',
      },
    ]
    expect(found('broker-socket-clients', files)).toEqual(['g/client.py'])
  })

  it('ignores a server that binds, and a TCP client', () => {
    const files = [
      { path: 'h/server.py', text: 's = socket.socket(socket.AF_UNIX)\ns.bind(SOCK)\ns.listen()' },
      { path: 'h/tcp.py', text: 's = socket.socket(socket.AF_INET)\ns.connect((host, 443))' },
    ]
    expect(found('broker-socket-clients', files)).toEqual([])
  })
})

describe('money-formatters', () => {
  it('counts function and arrow formatters under src/ and workers/', () => {
    const files = [
      {
        path: 'src/a.ts',
        text: 'function formatCurrency(cents: number): string {\n  return ""\n}',
      },
      { path: 'src/b.astro', text: 'const fmtPrice = (n: number) => `$${n}`' },
      { path: 'workers/c.ts', text: 'export function formatDollars(amount) {}' },
    ]
    expect(found('money-formatters', files)).toEqual(['src/a.ts', 'src/b.astro', 'workers/c.ts'])
  })

  it('ignores a formatted value, a date formatter, a prop type, and files outside src/workers', () => {
    const files = [
      { path: 'src/d.ts', text: 'const formattedAmount = `$${x.toFixed(2)}`' },
      { path: 'src/e.ts', text: 'function formatDate(d: Date) {}' },
      { path: 'src/f.astro', text: 'interface P { formatCents: (cents: number) => string }' },
      { path: 'scripts/g.ts', text: 'function formatMoney(n) {}' },
    ]
    expect(found('money-formatters', files)).toEqual([])
  })
})

describe('resend-senders', () => {
  it('counts a program that posts to Resend itself', () => {
    const files = [
      { path: 'i/a.ts', text: "await fetch('https://api.resend.com/emails', { method: 'POST' })" },
      { path: 'operator/bin/notify', text: 'curl -s https://api.resend.com/emails -d @body.json' },
    ]
    expect(found('resend-senders', files)).toEqual(['i/a.ts', 'operator/bin/notify'])
  })

  it('ignores a comment, a doc and a test that name the endpoint', () => {
    const files = [
      { path: 'j/a.ts', text: '// sends through https://api.resend.com/emails via sendEmail()' },
      { path: 'j/README.md', text: 'POST https://api.resend.com/emails' },
      { path: 'j/a.test.ts', text: "fetch('https://api.resend.com/emails')" },
    ]
    expect(found('resend-senders', files)).toEqual([])
  })
})

describe('inline-rolling-issue-workflows', () => {
  const call = (n: number) =>
    Array.from({ length: n }, () => '  gh issue list --label x').join('\n')

  it('counts a workflow with four inline issue calls, gh or github-script', () => {
    const files = [
      { path: '.github/workflows/a.yml', text: call(4) },
      {
        path: '.github/workflows/b.yaml',
        text: 'github.rest.issues.create()\nissues.update()\nissues.createComment()\nissues.listForRepo()',
      },
    ]
    expect(found('inline-rolling-issue-workflows', files)).toEqual([
      '.github/workflows/a.yml',
      '.github/workflows/b.yaml',
    ])
  })

  it('ignores three calls and a script outside the workflows directory', () => {
    const files = [
      { path: '.github/workflows/c.yml', text: call(3) },
      { path: 'scripts/rolling-issue.sh', text: call(9) },
    ]
    expect(found('inline-rolling-issue-workflows', files)).toEqual([])
  })
})

describe('hand-kept-python-copies', () => {
  it('counts every byte-identical same-named copy past the first', () => {
    const files = [
      { path: 'operator/skills/a/skill_helpers.py', text: 'X = 1\n' },
      { path: 'operator/skills/b/skill_helpers.py', text: 'X = 1\n' },
      { path: 'operator/templates/skill_helpers.py', text: 'X = 1\n' },
    ]
    expect(found('hand-kept-python-copies', files)).toEqual([
      'operator/skills/b/skill_helpers.py',
      'operator/templates/skill_helpers.py',
    ])
  })

  it('ignores a drifted copy, a same-text file under another name, tests and files outside operator/', () => {
    const files = [
      { path: 'operator/skills/a/routing.py', text: 'Y = 1\n' },
      { path: 'operator/skills/b/routing.py', text: 'Y = 2\n' },
      { path: 'operator/skills/c/other.py', text: 'Y = 1\n' },
      { path: 'operator/skills/a/tests/test_r.py', text: 'Z = 1\n' },
      { path: 'operator/skills/b/tests/test_r.py', text: 'Z = 1\n' },
      { path: 'scripts/x/routing.py', text: 'Y = 1\n' },
    ]
    expect(found('hand-kept-python-copies', files)).toEqual([])
  })
})

describe('writing-rules-bodies', () => {
  const H = '## Delivery channels + refusal fallback (law seat rule)'

  it('counts distinct bodies, one representative file each', () => {
    const files = [
      { path: 's/a/SKILL.md', text: `# A\n${H}\nRule one.\n## Next\nA only` },
      { path: 's/b/SKILL.md', text: `# B\n${H}\nRule one.\n\n## Other\nB only` },
      { path: 's/c/SKILL.md', text: `# C\n${H}\nRule one, reworded.` },
    ]
    expect(found('writing-rules-bodies', files)).toEqual(['s/a/SKILL.md', 's/c/SKILL.md'])
  })

  it('ignores files without the section and non-SKILL.md files that carry it', () => {
    const files = [
      { path: 's/d/SKILL.md', text: '# D\n## Procedure\nx' },
      { path: 's/e/README.md', text: `${H}\nRule one.` },
    ]
    expect(found('writing-rules-bodies', files)).toEqual([])
  })
})

const R = 'operator/runners/jobs/jobs/'
const B = 'operator/workspace_broker/'

describe('lane-host-loops', () => {
  it('counts a runner class that defines run_job or tick', () => {
    const files = [
      {
        path: `${R}a_lane.py`,
        text: 'class A(Daemon):\n    def run_job(self, job_id):\n        pass',
      },
      {
        path: `${R}b_lane.py`,
        text: 'class B(Daemon):\n    def tick(self) -> None:\n        pass',
      },
    ]
    expect(found('lane-host-loops', files)).toEqual([`${R}a_lane.py`, `${R}b_lane.py`])
  })

  it('ignores a module-level run_job, another method, a test and a file outside the runner', () => {
    const files = [
      { path: `${R}c.py`, text: 'def run_job(lane, job_id):\n    pass' },
      { path: `${R}d.py`, text: 'class D:\n    def run(self):\n        pass' },
      {
        path: 'operator/runners/jobs/tests/test_e.py',
        text: 'class E:\n    def tick(self):\n        pass',
      },
      { path: `${B}f.py`, text: 'class F:\n    def tick(self):\n        pass' },
    ]
    expect(found('lane-host-loops', files)).toEqual([])
  })
})

describe('verdict-readers', () => {
  it('counts read_verdict, read_verdicts, finished_verdict and a read( beside verdict.json', () => {
    const files = [
      { path: `${R}a.py`, text: 'def read_verdict(job_dir, out):\n    pass' },
      { path: `${R}b.py`, text: 'def read_verdicts(d):\n    pass' },
      { path: `${R}c.py`, text: 'def finished_verdict(v):\n    pass' },
      { path: `${R}d.py`, text: 'NAME = "verdict.json"\ndef read(job_dir, out):\n    pass' },
    ]
    expect(found('verdict-readers', files)).toEqual([
      `${R}a.py`,
      `${R}b.py`,
      `${R}c.py`,
      `${R}d.py`,
    ])
  })

  it('ignores a read( with no verdict file, a writer, and a reader outside the runner', () => {
    const files = [
      { path: `${R}e.py`, text: 'def read(path):\n    pass' },
      { path: `${R}f.py`, text: 'NAME = "verdict.json"\ndef write(job_dir, payload):\n    pass' },
      { path: `${B}g.py`, text: 'def read_verdict(x):\n    pass' },
    ]
    expect(found('verdict-readers', files)).toEqual([])
  })
})

describe('lane-ledger-classes', () => {
  it('counts a broker class recording a job transition', () => {
    const files = [
      {
        path: `${B}x_ledger.py`,
        text: 'class X:\n    def record(self, job_id: str, state: str, fields):\n        pass',
      },
    ]
    expect(found('lane-ledger-classes', files)).toEqual([`${B}x_ledger.py`])
  })

  it('ignores another record signature, a runner file and a broker test', () => {
    const files = [
      { path: `${B}y.py`, text: 'class Y:\n    def record(self, row):\n        pass' },
      {
        path: `${R}z.py`,
        text: 'class Z:\n    def record(self, job_id, state, fields):\n        pass',
      },
      {
        path: `${B}tests/test_w.py`,
        text: 'class W:\n    def record(self, job_id):\n        pass',
      },
    ]
    expect(found('lane-ledger-classes', files)).toEqual([])
  })
})

describe('firm-settings-loaders', () => {
  it('counts a runner module with its own ConfigError and a load(', () => {
    const files = [
      {
        path: `${R}demand/firm.py`,
        text: 'class DemandConfigError(ValueError):\n    pass\n\ndef load(inputs_dir):\n    pass',
      },
    ]
    expect(found('firm-settings-loaders', files)).toEqual([`${R}demand/firm.py`])
  })

  it('ignores a ConfigError without a load(, a load( without one, and an import of either', () => {
    const files = [
      { path: `${R}a.py`, text: 'class AConfigError(ValueError):\n    pass' },
      { path: `${R}b.py`, text: 'def load(path):\n    pass' },
      { path: `${R}c.py`, text: 'from .firm import DemandConfigError, load\nx = load(None)' },
    ]
    expect(found('firm-settings-loaders', files)).toEqual([])
  })
})

describe('paid-session-setups', () => {
  it('counts a runner file building both the doorway and the limits', () => {
    const files = [
      {
        path: `${R}x/run.py`,
        text: 'self.doorway = Doorway(client, ledger)\nself.limits = Limits(cap_usd=1.0)',
      },
    ]
    expect(found('paid-session-setups', files)).toEqual([`${R}x/run.py`])
  })

  it('ignores one without the other, and a test', () => {
    const files = [
      { path: `${R}a.py`, text: 'd = Doorway(c, l)' },
      { path: `${R}b.py`, text: 'l = Limits(cap_usd=1.0)' },
      { path: 'operator/runners/jobs/tests/test_c.py', text: 'Doorway(c, l)\nLimits(cap_usd=1)' },
    ]
    expect(found('paid-session-setups', files)).toEqual([])
  })
})

describe('model-id-literals', () => {
  it('counts a quoted model id in operator Python', () => {
    const files = [
      { path: 'operator/a.py', text: 'MODEL = "claude-opus-5"' },
      { path: 'operator/b.py', text: "m = 'claude-haiku-4-5-20251001'" },
    ]
    expect(found('model-id-literals', files)).toEqual(['operator/a.py', 'operator/b.py'])
  })

  it('ignores a comment, an unquoted mention, a test and a file outside operator/', () => {
    const files = [
      { path: 'operator/c.py', text: '# uses claude-opus-5 by default' },
      { path: 'operator/d.py', text: 'doc = f"model {name}"  # claude-sonnet family' },
      { path: 'operator/tests/test_e.py', text: 'M = "claude-opus-5"' },
      { path: 'src/f.py', text: 'M = "claude-opus-5"' },
    ]
    expect(found('model-id-literals', files)).toEqual([])
  })
})

describe('word-to-text', () => {
  it('counts a module that defines its own docx-to-text function', () => {
    const files = [
      { path: 'operator/a.py', text: 'def docx_text(path):\n    pass' },
      { path: 'operator/b.py', text: 'class R:\n    def _docx_text(self, raw):\n        pass' },
    ]
    expect(found('word-to-text', files)).toEqual(['operator/a.py', 'operator/b.py'])
  })

  it('ignores a caller, a docx writer, and a test', () => {
    const files = [
      { path: 'operator/c.py', text: 'from .extract import docx_text\nt = docx_text(p)' },
      {
        path: 'operator/d.py',
        text: 'def write_docx(text):\n    doc = Document()\n    doc.paragraphs',
      },
      { path: 'operator/tests/test_e.py', text: 'def docx_text(p):\n    pass' },
    ]
    expect(found('word-to-text', files)).toEqual([])
  })
})

describe('conceptRatchet', () => {
  const rows = conceptCensus(
    [{ path: 'a/x.py', text: 'L = ("medchron", "demand", "drafting", "records")' }],
    CONCEPTS.filter((c) => c.id === 'job-lane-lists')
  )

  it('fails a rise, a fall, and a concept missing from the baseline', () => {
    expect(conceptRatchet(rows, { 'job-lane-lists': 0 }).rose).toHaveLength(1)
    expect(conceptRatchet(rows, { 'job-lane-lists': 2 }).fell).toHaveLength(1)
    expect(conceptRatchet(rows, {}).rose).toHaveLength(1)
  })

  it('passes an unchanged count', () => {
    expect(conceptRatchet(rows, { 'job-lane-lists': 1 })).toEqual({ rose: [], fell: [] })
  })
})

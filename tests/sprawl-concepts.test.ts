/**
 * Known answers for every detector in scripts/sprawl/concepts.ts, in both
 * directions: a file that is an instance and a near miss that is not. The
 * fixtures are inline strings, never repo files, so a pass here says the rule
 * does what its `rule` text claims, independent of the tree.
 */
import { describe, expect, it } from 'vitest'
import { CONCEPTS, conceptCensus, conceptRatchet } from '../scripts/sprawl/concepts'
import { LANE_SKILL } from '../src/lib/operator/job-lanes.generated'

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

  it('takes its lanes from the declaration: every declared lane counts, ids that are not lanes do not', () => {
    // A list of the declared lanes counts whatever the declaration holds, so a lane added to
    // operator/contracts/job-lanes.yaml is counted with no edit to the census.
    const declared = Object.keys(LANE_SKILL)
    const files = [
      { path: 'e/declared.py', text: `L = (${declared.map((l) => `"${l}"`).join(', ')})` },
      // library_setup is a drafting job kind and marketing a firm-input-only skill, not lanes.
      { path: 'e/kinds.py', text: 'K = ("medchron", "demand", "library_setup", "marketing")' },
    ]
    expect(found('job-lane-lists', files)).toEqual(['e/declared.py'])
  })

  it('ignores lanes a Python docstring names in prose, and still counts a triple-quoted string in code', () => {
    const files = [
      {
        path: 'f/module_doc.py',
        text: '"""A wake names its job: medchron, demand,\ndrafting or litigation."""\n\nX = 1\n',
      },
      {
        path: 'f/def_doc.py',
        text: 'def f(\n    a: int,\n) -> str:\n    """Runs for "medchron", "demand", "drafting" and "records"."""\n    return ""\n',
      },
      {
        path: 'f/bound.py',
        text: 'X = 1\nHELP = """\nmedchron demand drafting litigation\n"""\n',
      },
      {
        path: 'f/argument.py',
        text: 'X = 1\nprint(\n    """lanes: "medchron", "demand", "drafting", "records" """\n)\n',
      },
    ]
    expect(found('job-lane-lists', files)).toEqual(['f/argument.py', 'f/bound.py'])
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

  it('ignores pairs a module docstring names in prose, and counts the same pairs in code', () => {
    const prose =
      'medchron (medical-chronology-maintainer), demand (demand-letter-drafter), drafting (document-drafter), records (matter-records)'
    const files = [
      { path: 'g/doc.py', text: `"""Wakes: ${prose}."""\n\nX = 1\n` },
      { path: 'g/code.py', text: `X = 1\nT = "${prose}"\n` },
    ]
    expect(found('lane-skill-tables', files)).toEqual(['g/code.py'])
  })
})

describe('customer-yaml-hand-parsers', () => {
  const py = (...lines: string[]) => lines.join('\n')

  /** One fixture per idiom a counted file in the tree uses, each named for where it is used. */
  const MUST_COUNT: File[] = [
    {
      // the first rule's own fixture: a literal path inside the load
      path: 'e/inline-literal.py',
      text: 'import yaml\ncfg = yaml.safe_load(open("/opt/data/customer.yaml"))',
    },
    {
      // `from yaml import safe_load` and an upper-case constant
      path: 'e/bare-safe-load.py',
      text: 'from yaml import safe_load\nd = safe_load(CUSTOMER_YAML.read_text())',
    },
    {
      // a path built from the literal, then read (the laptop tools before this change)
      path: 'e/path-variable.py',
      text: py(
        'def load(slug):',
        '    path = CUSTOMERS / slug / "customer.yaml"',
        '    return yaml.safe_load(path.read_text()) or {}'
      ),
    },
    {
      // the load's argument spread over lines
      path: 'e/multi-line.py',
      text: py(
        'def load(slug):',
        '    return yaml.safe_load(',
        '        (CUSTOMERS / slug / "customer.yaml").read_text(encoding="utf-8")',
        '    )'
      ),
    },
    {
      // with open(<dir> / "customer.yaml") as fh (templates/lane-memory-caps-probe.py)
      path: 'e/with-open.py',
      text: py(
        'def caps(cfg):',
        '    with open(cfg / "customer.yaml", encoding="utf-8") as fh:',
        '        doc = yaml.safe_load(fh) or {}',
        '    return doc'
      ),
    },
    {
      // the text read first, then parsed (bootstrap/validate.py)
      path: 'e/text-variable.py',
      text: py(
        'def validate_customer_yaml(customer_yaml):',
        '    raw_text = customer_yaml.read_text()',
        '    cfg = yaml.safe_load(raw_text)',
        '    return cfg'
      ),
    },
    {
      // only the env variable names it (each skill pre_run.py, pre_run_gate, skill_helpers)
      path: 'e/env-variable.py',
      text: py(
        'def load_chase_config(path=None):',
        '    path = path or os.environ.get("SMD_CUSTOMER_YAML_PATH")',
        '    with open(path, encoding="utf-8") as handle:',
        '        data = yaml.safe_load(handle) or {}',
        '    return data'
      ),
    },
    {
      // the function's docstring says what its parameter is (shared/capabilities.py read)
      path: 'e/docstring-param.py',
      text: py(
        'def read(path):',
        '    """The parsed customer.yaml, or None when it cannot be read as a mapping."""',
        '    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))'
      ),
    },
    {
      // the path comes back from a function (shared/logic_in_code.py live_path)
      path: 'e/returned-path.py',
      text: py(
        'PATH_ENVS = ("SMD_CUSTOMER_YAML_PATH", "SMD_CUSTOMER_YAML")',
        'def live_path():',
        '    for name in PATH_ENVS:',
        '        value = os.environ.get(name)',
        '        if value:',
        '            return value',
        '    return "/etc/default"',
        'def load_switch(key, path=None):',
        '    target = path or live_path()',
        '    with open(target, encoding="utf-8") as fh:',
        '        raw = fh.read()',
        '    return yaml.safe_load(raw)'
      ),
    },
    {
      // a helper that loads its parameter, given the customer.yaml path (ensure-disabled-skills.py)
      path: 'e/helper-param.py',
      text: py(
        'def _load_yaml(path: Path) -> dict[str, Any]:',
        '    with path.open(encoding="utf-8") as f:',
        '        data = yaml.safe_load(f) or {}',
        '    return data',
        'def main(args):',
        '    return _load_yaml(Path(args.customer_yaml))'
      ),
    },
    {
      // two hops from a module constant (msgraph-read-app-cannot-send-probe.py)
      path: 'e/two-hops.py',
      text: py(
        '_DEFAULT_YAML = "/var/lib/smd-config/customer.yaml"',
        'def staff(yaml_text):',
        '    data = yaml.safe_load(yaml_text) or {}',
        '    return data',
        'def run(yaml_path, user):',
        '    yaml_text = open(yaml_path, encoding="utf-8").read()',
        '    return staff(yaml_text)',
        'def main(argv):',
        '    yaml_path = argv[1] if len(argv) > 1 else _DEFAULT_YAML',
        '    return run(yaml_path, argv[2])'
      ),
    },
    {
      // the one reader itself (shared/seat_config.py): load reads, parse parses
      path: 'e/the-reader.py',
      text: py(
        'def parse(source):',
        '    """A YAML document parsed safely."""',
        '    return yaml.safe_load(source)',
        'def load(path, *, encoding, on_missing):',
        '    """The YAML document at ``path`` (customer.yaml, or a file beside it), parsed.',
        '    """',
        '    text = Path(path).read_text(encoding=encoding)',
        '    return parse(text)'
      ),
    },
  ]

  /** Near misses, each from a file the first rule counted though it parses no customer.yaml. */
  const MUST_NOT_COUNT: File[] = [
    {
      // fleet/seats.yaml, customer.yaml named only in the module docstring (bin/lib/seat_purpose.py)
      path: 'f/module-docstring.py',
      text: py(
        '"""Seat purposes. A twin\'s customer.yaml equals its client\'s."""',
        'SEATS_FILE = ROOT / "fleet" / "seats.yaml"',
        'def load(path=SEATS_FILE):',
        '    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}'
      ),
    },
    {
      // role.yaml, customer.yaml named lower in the function docstring (bootstrap/role_persona.py)
      path: 'f/function-docstring.py',
      text: py(
        'def load_role_persona(role, roles_dir=None):',
        '    """The role persona.',
        '',
        '    A seat selects a role with seat.role in its customer.yaml.',
        '    """',
        '    manifest_path = roles_dir / role / "role.yaml"',
        '    return yaml.safe_load(manifest_path.read_text()) or {}'
      ),
    },
    {
      // an error message and a flag name, passed to a function that loads ANOTHER parameter
      // (shared/policy_profile.py: current(cfg) loads role.yaml from roles_dir_override)
      path: 'f/message-and-flag.py',
      text: py(
        'def current(cfg=None, *, roles_dir_override=None):',
        '    path = roles_dir(roles_dir_override) / "role.yaml"',
        '    return yaml.safe_load(path.read_text())',
        'def main(args):',
        '    index = args.index("--customer-yaml")',
        '    cfg = CustomerConfig.from_volume(args[index + 1])',
        '    note = f"customer.yaml unreadable ({index})"',
        '    return current(cfg), note'
      ),
    },
    {
      // profile config.yaml files, customer.yaml named in a docstring (ensure-curator-disabled.py)
      path: 'f/profile-config.py',
      text: py(
        '"""The customer.yaml to config translation lives in the overlay."""',
        'def _load(path: Path) -> dict:',
        '    with path.open() as f:',
        '        cfg = yaml.safe_load(f) or {}',
        '    return cfg',
        'for p in sorted(home.glob("profiles/*/config.yaml")):',
        '    _load(p)'
      ),
    },
    {
      // a SKILL.md front matter, beside a message naming customer.yaml (initiation/seat_facts.py)
      path: 'f/front-matter.py',
      text: py(
        'UNREADABLE = "customer.yaml unreadable"',
        'def front(path):',
        '    text = path.read_text(encoding="utf-8")',
        '    end = text.find("---", 3)',
        '    return yaml.safe_load(text[3:end])'
      ),
    },
    {
      // a load inside a program string run elsewhere (boot_harness/collect.py)
      path: 'f/program-string.py',
      text: py(
        'HARNESS_FIXTURE = "/harness/customer.yaml"',
        'PROGRAM = """import yaml',
        'doc = yaml.safe_load(open(sys.argv[1]))',
        '"""',
        'def on_harness():',
        '    with open(HARNESS_FIXTURE) as fh:',
        '        return any(line.startswith("customer_id:") for line in fh)'
      ),
    },
    {
      // a name tainted in one function is not the same-named local of another
      path: 'f/other-scope.py',
      text: py(
        'def exists(slug):',
        '    path = CUSTOMERS / slug / "customer.yaml"',
        '    return path.is_file()',
        'def card(slug):',
        '    path = CUSTOMERS / slug / "initiation-card.yaml"',
        '    return yaml.safe_load(path.read_text())'
      ),
    },
    {
      // an end-to-end test harness parsing a template customer.yaml (tests_e2e/_seat.py)
      path: 'f/tests_e2e/_seat.py',
      text: 'TEMPLATE = ROOT / "customer.yaml"\ndoc = yaml.safe_load(TEMPLATE.read_text())',
    },
    { path: 'f/other.py', text: 'yaml.safe_load(open("seat-layout.yaml"))' },
    { path: 'f/mention.py', text: 'PATH = "customer.yaml"' },
    { path: 'f/x.ts', text: 'yaml.load(read("customer.yaml"))' },
  ]

  it('counts every idiom the tree uses to load customer.yaml', () => {
    expect(found('customer-yaml-hand-parsers', MUST_COUNT)).toEqual(
      MUST_COUNT.map((f) => f.path).sort()
    )
  })

  it('ignores loads of other files, however customer.yaml is mentioned, and tests', () => {
    expect(found('customer-yaml-hand-parsers', MUST_NOT_COUNT)).toEqual([])
  })

  it('each near miss that names customer.yaml beside a load is one the first rule counted', () => {
    // The first rule, as it stood: a yaml load anywhere AND customer.yaml named anywhere. A near
    // miss it would not have counted proves nothing about the fix.
    const firstRule = (text: string) => {
      const code = text
        .split('\n')
        .filter((l) => !/^\s*(#|\/\/|\/\*|\*)/.test(l))
        .join('\n')
      return /\b(yaml\.(safe_)?load|safe_load)\(/.test(code) && /customer[._]ya?ml/i.test(code)
    }
    const nearMisses = MUST_NOT_COUNT.filter(
      (f) => f.path.endsWith('.py') && /customer[._]ya?ml/i.test(f.text) && /load\(/.test(f.text)
    )
    expect(nearMisses.length).toBeGreaterThanOrEqual(7)
    expect(nearMisses.filter((f) => !firstRule(f.text)).map((f) => f.path)).toEqual([])
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

describe('filename-rule-copies', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts a runner module that joins name and extension unless the name already ends with it', () => {
    const files = [
      {
        path: `${R}a.py`,
        text: 'def full_name(f):\n    return name if not ext or name.lower().endswith(ext.lower()) else name + ext',
      },
      { path: `${R}b.py`, text: 'if not ext or name.lower().endswith(ext):\n    return name' },
    ]
    expect(found('filename-rule-copies', files)).toEqual([`${R}a.py`, `${R}b.py`])
  })

  it('ignores a caller, an unconditional join, a comment, a test and a file outside the runner', () => {
    const files = [
      { path: `${R}c.py`, text: 'from ..filing import full_name\nn = full_name(row)' },
      { path: `${R}d.py`, text: 'return str(r.get("name") or "") + str(r.get("ext") or "")' },
      { path: `${R}e.py`, text: '# name.lower().endswith(ext) is the rule' },
      { path: `${R}../tests/test_f.py`, text: 'x = name.lower().endswith(ext)' },
      { path: 'operator/connectors/g.py', text: 'x = name.lower().endswith(ext)' },
    ]
    expect(found('filename-rule-copies', files)).toEqual([])
  })
})

describe('send-record-copies', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts a runner module that writes a file and flags its own send record', () => {
    const files = [
      { path: `${R}a.py`, text: 'rec["pending"] = True\nseat.add_file(m, f, n, b)' },
      {
        path: `${R}b.py`,
        text: 'rec.update(sent=True)\nseat.add_file_version(m, f, b, expected_version=v)',
      },
    ]
    expect(found('send-record-copies', files)).toEqual([`${R}a.py`, `${R}b.py`])
  })

  it('ignores the seat backend, a write with no flags, flags with no write, and a test', () => {
    const files = [
      {
        path: `${R}seat.py`,
        text: 'r = self.client.add_file(matter_id, name, data, folder_id=folder_id)',
      },
      { path: `${R}c.py`, text: 'out = client.add_file(matter_id, name, data, folder_id=folder)' },
      { path: `${R}d.py`, text: 'if row.get("sent"):\n    pass' },
      { path: `${R}../tests/test_e.py`, text: 'rec["pending"] = True\nseat.add_file(m, f, n, b)' },
    ]
    expect(found('send-record-copies', files)).toEqual([])
  })
})

describe('pdf-text-readers', () => {
  const R = 'operator/runners/jobs/jobs/'
  const C = 'operator/connectors/smokeball/'
  it('counts a module that imports a PDF library and reads a text layer, at the top or in a function', () => {
    const files = [
      {
        path: `${R}a.py`,
        text: 'import pymupdf\n\ndef t(p):\n    return pymupdf.open(p)[0].get_text()',
      },
      {
        path: `${C}b.py`,
        text: 'def t(b):\n    from pypdf import PdfReader\n    return PdfReader(b).pages[0].extract_text()',
      },
    ]
    expect(found('pdf-text-readers', files)).toEqual([`${C}b.py`, `${R}a.py`])
  })

  it('ignores HTML text, the connector entry point, a PDF writer, a test and the overlay', () => {
    const files = [
      { path: `${R}c.py`, text: 'from bs4 import BeautifulSoup\nsoup.get_text("\\n")' },
      { path: `${R}d.py`, text: 'import pypdf\nr = extract_text_ex(blob)' },
      { path: `${R}e.py`, text: 'import pymupdf\ndoc = pymupdf.open()\ndoc.new_page()' },
      { path: `${R}../tests/test_f.py`, text: 'import pymupdf\np.get_text()' },
      { path: 'operator/hermes-smd-overlay/g.py', text: 'import pypdf\np.extract_text()' },
    ]
    expect(found('pdf-text-readers', files)).toEqual([])
  })
})

describe('readable-tests', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts a module that defines a named page-readability predicate', () => {
    const files = [
      { path: `${R}a.py`, text: 'def ocr_pages(texts):\n    return []' },
      { path: `${R}b.py`, text: 'def thin(text):\n    return len(text) < 200' },
      {
        path: 'operator/connectors/c.py',
        text: 'def _looks_scanned(text, pages):\n    return False',
      },
    ]
    expect(found('readable-tests', files)).toEqual([
      'operator/connectors/c.py',
      `${R}a.py`,
      `${R}b.py`,
    ])
  })

  it('ignores a caller, a near name, a comment and a test', () => {
    const files = [
      { path: `${R}d.py`, text: 'picks = ocr_pages(texts)' },
      { path: `${R}e.py`, text: 'def thinner(text):\n    pass' },
      { path: `${R}f.py`, text: '# def garbled(t): moved' },
      { path: `${R}../tests/test_g.py`, text: 'def garbled(t):\n    pass' },
    ]
    expect(found('readable-tests', files)).toEqual([])
  })
})

describe('paid-page-store-writers', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts a store method or function that writes paid text', () => {
    const files = [
      {
        path: `${R}a.py`,
        text: 'class TextCache:\n    def put_text(self, sha, text):\n        pass',
      },
      { path: 'operator/connectors/b.py', text: 'def cache_put(blob, text):\n    pass' },
    ]
    expect(found('paid-page-store-writers', files)).toEqual([
      'operator/connectors/b.py',
      `${R}a.py`,
    ])
  })

  it('ignores a caller, a reader and a test', () => {
    const files = [
      { path: `${R}c.py`, text: 'cache.put_text(sha, text, scanned_pages=1, matter_id=m)' },
      { path: `${R}d.py`, text: 'def get_text(self, sha):\n    pass' },
      { path: `${R}../tests/test_e.py`, text: 'def put_text(self, s, t):\n    pass' },
    ]
    expect(found('paid-page-store-writers', files)).toEqual([])
  })
})

describe('downloaders', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts a runner module that downloads through the seat', () => {
    const files = [
      { path: `${R}a.py`, text: 'got = sr.seat.fetch(url, dest, size)' },
      { path: `${R}b.py`, text: 'seat.fetch(url, dest, None)' },
    ]
    expect(found('downloaders', files)).toEqual([`${R}a.py`, `${R}b.py`])
  })

  it('ignores a mint, another fetch, a comment, a test and the connector', () => {
    const files = [
      { path: `${R}c.py`, text: 'minted = seat.mint(m, ids)' },
      { path: `${R}d.py`, text: 'rows = fetch_mod.fetch_matter(seat, m, files, ids, d, log)' },
      { path: `${R}e.py`, text: '# seat.fetch(url, dest, None)' },
      { path: `${R}../tests/test_f.py`, text: 'seat.fetch(u, d, None)' },
      { path: 'operator/connectors/g.py', text: 'seat.fetch(u, d, None)' },
    ]
    expect(found('downloaders', files)).toEqual([])
  })
})

describe('file-name-display-joins', () => {
  const R = 'operator/runners/jobs/jobs/'
  it('counts every line that joins a name and an extension, one instance per line', () => {
    const files = [
      {
        path: `${R}a.py`,
        text: [
          "full = f\"{r.get('name') or ''}{r.get('ext') or ''}\"",
          'key = r["name"] + (r.get("ext") or "")',
        ].join('\n'),
      },
      { path: `${R}b.py`, text: 'key = name + str(r.get("ext") or "")' },
      { path: `${R}c.py`, text: "same = f\"{m.get('name') or ''}{m.get('fileExtension') or ''}\"" },
    ]
    expect(found('file-name-display-joins', files)).toEqual([
      `${R}a.py#1`,
      `${R}a.py#2`,
      `${R}b.py#1`,
      `${R}c.py#1`,
    ])
  })

  it('ignores a path built from an id, a name alone, two lines, a comment and a test', () => {
    const files = [
      { path: `${R}d.py`, text: 'dest = raw / (b["id"] + (b.get("ext") or ""))' },
      { path: `${R}e.py`, text: 'shown = r["name"]' },
      { path: `${R}f.py`, text: 'a = f"name: {doc[\'name\']}"\nb = f"type: {doc.get(\'ext\')}"' },
      { path: `${R}g.py`, text: '# r["name"] + (r.get("ext") or "")' },
      { path: `${R}../tests/test_h.py`, text: 'k = r["name"] + (r.get("ext") or "")' },
    ]
    expect(found('file-name-display-joins', files)).toEqual([])
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

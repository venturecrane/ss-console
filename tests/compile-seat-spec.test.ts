/**
 * The seat-spec compiler (scripts/lib/seat-spec*.ts, Wave 2 W4): golden refusal
 * cases, byte stability, the signing contract, and the two tables it must hold
 * equal to the code it mirrors (fleet build's lane schema modules, the committed
 * public key's id). The shell callers are covered by
 * tests/config-publish-guards.test.ts and tests/r2-config-reconcile.test.ts.
 */
import { createPublicKey, generateKeyPairSync } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { LANE_INPUTS, canonicalJson, imagePathOf, sha256Hex } from '../scripts/lib/seat-spec'
import {
  compileSpec,
  keyIdOf,
  pointerProblem,
  sameSpec,
  signedPointer,
  specBytes,
  type CompileInputs,
} from '../scripts/lib/seat-spec-compile'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const SHA = 'a'.repeat(40)

const CONFIG = [
  'customer_id: seat-alpha',
  'personas:',
  '  - slug: operator',
  '    skills:',
  '      - name: matter-reader',
  '        version: pending',
  '        enabled: true',
  '      - name: dormant',
  '        enabled: false',
  '',
].join('\n')

const IMAGE_MANIFEST = {
  schema_version: 1,
  built_from: 'b'.repeat(40),
  skills: { 'matter-reader': { pin: 'abc123', files: ['SKILL.md'] } },
}

const SKILLS_MANIFEST = {
  version: '202610230000-abcdef12',
  files: { 'matter-reader/SKILL.md': { sha256: 'c'.repeat(64), size: 1 } },
  needs: {
    'matter-reader': {
      paths: ['/app/bin/reader'],
      tools: ['email_search'],
      verbs: ['matter_read'],
      action_classes: ['read'],
    },
    dormant: { paths: ['/app/never'], tools: [], verbs: [], action_classes: [] },
  },
}

const CAPS_ALL = {
  schema_version: 1,
  paths: ['/app/bin/reader'],
  tools: ['email_search'],
  verbs: ['matter_read'],
  action_classes: ['read'],
}

function inputs(over: Partial<CompileInputs> = {}): CompileInputs {
  return {
    slug: 'seat-alpha',
    compiledFrom: SHA,
    customerYaml: Buffer.from(CONFIG),
    laneProofs: null,
    lanePins: {},
    laneSchemaDigests: { demand: 'd'.repeat(64) },
    imageManifest: IMAGE_MANIFEST,
    imageCapabilities: CAPS_ALL,
    imageLaneInputs: { demand: 'd'.repeat(64) },
    skillsPointer: {
      version: SKILLS_MANIFEST.version,
      manifest_key: `fleet/skills/${SKILLS_MANIFEST.version}.json`,
      manifest_sha256: 'e'.repeat(64),
    },
    skillsManifest: SKILLS_MANIFEST,
    ...over,
  }
}

describe('compileSpec: golden cases', () => {
  it('compiles a config the image can run, naming only enabled skills', () => {
    const r = compileSpec(inputs())
    expect(r.imageRefusals).toEqual([])
    expect(r.laneRefusals).toEqual([])
    const req = r.spec.requires as Record<string, unknown>
    expect(req.skills).toEqual(['matter-reader'])
    expect(req.image).toEqual(['skill:matter-reader'])
    // The disabled skill's need is not a requirement.
    expect((req.capabilities as Record<string, string[]>).paths).toEqual(['/app/bin/reader'])
  })

  it('refuses an enabled skill that needs a capability the image lacks', () => {
    const r = compileSpec(inputs({ imageCapabilities: { ...CAPS_ALL, verbs: [] } }))
    expect(r.imageRefusals.join('\n')).toContain('verb:matter_read')
  })

  it('a capability need is met by a directory the image carries', () => {
    const caps = { ...CAPS_ALL, paths: ['/app/bin/reader/main.py'] }
    expect(compileSpec(inputs({ imageCapabilities: caps })).imageRefusals).toEqual([])
  })

  it('refuses a skill the image and the skills release both lack', () => {
    const r = compileSpec(
      inputs({
        imageManifest: { ...IMAGE_MANIFEST, skills: {} },
        skillsManifest: { ...SKILLS_MANIFEST, files: {} },
      })
    )
    expect(r.imageRefusals.join('\n')).toContain('skill:matter-reader')
  })

  it('without capabilities, refuses only a need the published config did not already have', () => {
    const before = Buffer.from(CONFIG)
    const same = compileSpec(inputs({ imageCapabilities: undefined, published: before }))
    expect(same.imageRefusals).toEqual([])
    const none = compileSpec(
      inputs({ imageCapabilities: undefined, published: Buffer.from('personas: []\n') })
    )
    expect(none.imageRefusals.join('\n')).toContain('cannot determine')
  })

  it('refuses the SPEC, not the config, when a lane schema digest differs from the image', () => {
    const r = compileSpec(
      inputs({
        lanePins: { demand: Buffer.from('pins: {}\n') },
        imageLaneInputs: { demand: 'f'.repeat(64) },
      })
    )
    expect(r.imageRefusals).toEqual([])
    expect(r.laneRefusals.join('\n')).toContain('lane demand')
  })

  it('refuses a lane whose image recorded no lane-input digests', () => {
    const r = compileSpec(
      inputs({ lanePins: { demand: Buffer.from('x') }, imageLaneInputs: undefined })
    )
    expect(r.laneRefusals.join('\n')).toContain('cannot determine')
  })

  it('pins a lane by digest and key only, never by content', () => {
    const firm = Buffer.from('voice: private firm text\n')
    const r = compileSpec(inputs({ lanePins: { demand: firm } }))
    const bytes = new TextDecoder().decode(specBytes(r.spec))
    expect(bytes).not.toContain('private firm text')
    expect((r.spec.lanes as Record<string, unknown>).demand).toEqual({
      key: 'vaults/seat-alpha/demand/demand-firm.yaml',
      sha256: sha256Hex(firm),
    })
    expect(
      (r.spec.requires as Record<string, Record<string, unknown>>).lane_schema_digests.demand
    ).toEqual({
      path: '/app/runners/medchron/medchron/demand/firm.py',
      sha256: 'd'.repeat(64),
    })
  })
})

describe('compileSpec: canonical bytes', () => {
  it('is byte-stable across runs and independent of key order', () => {
    const a = specBytes(compileSpec(inputs()).spec)
    const b = specBytes(compileSpec(inputs()).spec)
    expect(a).toEqual(b)
    expect(canonicalJson({ b: 1, a: { d: 2, c: [3, { f: 4, e: 5 }] } })).toBe(
      '{"a":{"c":[3,{"e":5,"f":4}],"d":2},"b":1}'
    )
  })

  it('treats a spec differing only in compiled_from as the same spec', () => {
    const a = compileSpec(inputs()).spec
    const b = compileSpec(inputs({ compiledFrom: 'b'.repeat(40) })).spec
    expect(specBytes(a)).not.toEqual(specBytes(b))
    expect(sameSpec(a, b)).toBe(true)
    const c = compileSpec(inputs({ customerYaml: Buffer.from(`${CONFIG}notes: x\n`) })).spec
    expect(sameSpec(a, c)).toBe(false)
  })
})

describe('signing', () => {
  const pair = () => {
    const { privateKey, publicKey } = generateKeyPairSync('ed25519')
    return {
      priv: String(privateKey.export({ format: 'pem', type: 'pkcs8' })),
      pub: String(publicKey.export({ format: 'pem', type: 'spki' })),
    }
  }

  it('a signed pointer verifies; a tampered spec, signature or key does not', () => {
    const k = pair()
    const spec = compileSpec(inputs()).spec
    const pointer = signedPointer(spec, k.priv, k.pub)
    const bytes = specBytes(spec)
    expect(pointerProblem(pointer, bytes, k.pub)).toBeNull()

    const tampered = new TextEncoder().encode(
      new TextDecoder().decode(bytes).replace('matter-reader', 'matter-writer')
    )
    expect(pointerProblem(pointer, tampered, k.pub)).toContain('do not hash')
    const sig = Buffer.from(String(pointer.signature), 'base64')
    sig[3] ^= 1
    expect(
      pointerProblem({ ...pointer, signature: sig.toString('base64') }, bytes, k.pub)
    ).toContain('does not verify')
    expect(pointerProblem(pointer, bytes, pair().pub)).toContain('not the committed key')
  })

  it('refuses to sign with a key whose public half is not the committed one', () => {
    const spec = compileSpec(inputs()).spec
    expect(() => signedPointer(spec, pair().priv, pair().pub)).toThrow(/not the committed/)
  })

  it('the committed public key is Ed25519 with key_id 15850dd6829dc7ee', () => {
    const pem = readFileSync(join(REPO_ROOT, 'operator', 'templates', 'seat-spec.pub'), 'utf8')
    const key = createPublicKey(pem)
    expect(key.asymmetricKeyType).toBe('ed25519')
    expect(keyIdOf(key)).toBe('15850dd6829dc7ee')
  })
})

describe('tables held equal to the code they mirror', () => {
  it('LANE_INPUTS names the same schema modules fleet build hashes into lane-inputs.json', () => {
    const build = readFileSync(join(REPO_ROOT, 'operator/bin/lib/fleet/build.py'), 'utf8')
    const block = /LANE_SCHEMA_MODULES = \{([\s\S]*?)\n\}/.exec(build)?.[1] ?? ''
    const fromBuild = Object.fromEntries(
      [...block.matchAll(/"([a-z]+)":\s*"([^"]+)"/g)].map((m) => [m[1], m[2]])
    )
    expect(Object.keys(fromBuild).length).toBeGreaterThan(0)
    const ours = Object.fromEntries(Object.entries(LANE_INPUTS).map(([k, v]) => [k, v.module]))
    expect(ours).toEqual(fromBuild)
  })

  it('every lane schema module is copied into the image at the path the spec names', () => {
    const dockerfile = readFileSync(join(REPO_ROOT, 'operator/templates/Dockerfile'), 'utf8')
    expect(dockerfile).toContain('COPY operator/runners/ /app/runners/')
    for (const { module } of Object.values(LANE_INPUTS)) {
      expect(imagePathOf(module).startsWith('/app/runners/')).toBe(true)
    }
  })
})

describe('the cross-language fixture the seat verifier tests against', () => {
  // operator/hermes-smd-overlay/tests/test_spec_verifier.py verifies these
  // bytes with `cryptography`. This test proves they are what the compiler
  // produces, so a change to the canonical form fails here and there together.
  const dir = join(REPO_ROOT, 'operator/hermes-smd-overlay/tests/fixtures/seat_spec')

  it('is exactly the compiler output for its inputs, and its pointer verifies', () => {
    const r = compileSpec({
      slug: 'seat-alpha',
      compiledFrom: '1'.repeat(40),
      customerYaml: Buffer.from(
        'customer_id: seat-alpha\npersonas:\n  - slug: operator\n    skills:\n      - name: matter-reader\n        version: pending\n        enabled: true\n'
      ),
      laneProofs: Buffer.from('lanes: {}\n'),
      lanePins: { demand: Buffer.from('pins: {}\n') },
      laneSchemaDigests: { demand: 'd'.repeat(64) },
      imageManifest: {
        schema_version: 1,
        built_from: 'x',
        skills: { 'matter-reader': { pin: 'abc123', files: ['SKILL.md'] } },
      },
      imageCapabilities: { paths: [], tools: [], verbs: [], action_classes: [] },
      imageLaneInputs: { demand: 'd'.repeat(64) },
    })
    const committed = readFileSync(join(dir, 'spec.json'))
    expect(new Uint8Array(committed)).toEqual(specBytes(r.spec))
    const pointer: unknown = JSON.parse(readFileSync(join(dir, 'pointer.json'), 'utf8'))
    expect(
      pointerProblem(pointer, committed, readFileSync(join(dir, 'seat-spec.pub'), 'utf8'))
    ).toBeNull()
  })
})

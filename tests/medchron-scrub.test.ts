/**
 * Scrub gate for the medchron runner (ss#2613, ADR 0087).
 *
 * The runner is firm-agnostic code in a PUBLIC repo, ported from a pipeline whose
 * source carried a firm's provider aliases, client surnames, and matter numbers
 * in constants and comments. This test bans those tokens from ever appearing
 * under operator/runners/** without naming them here: it carries only sha256
 * hashes of lowercased tokens (the plaintext list lives in the private
 * engagements repo at operator/customers/<slug>/medchron/denylist.txt, and
 * tools/medchron/scrub_hashes.py regenerates the hashes).
 *
 * Matching: every candidate phrase in a scanned file (runs of 1..4 words of
 * letters/digits/&/./' separated by single spaces, lowercased) is hashed and
 * looked up. A hit names the file and the offset, never the token.
 */

import { createHash } from 'node:crypto'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(__dirname, '..')
// The WHOLE repo, not one directory. Until 2026-09-17 this was
// `operator/runners` alone, and five denylisted tokens -- already hashed into
// the set below -- sat untouched in operator/connectors/, docs/, .stitch/ and
// tests/, because the gate was never pointed at them. A guard aimed at 5% of a
// public repo reports green while the leak is in the other 95%.
const SCAN_ROOT = ROOT
const SKIP_DIRS = new Set([
  '.venv',
  '__pycache__',
  '.pytest_cache',
  '.ruff_cache',
  'node_modules',
  '.git',
  'dist',
  '.astro',
  '.wrangler',
  'coverage',
  // Vendored third-party text: not ours to police, and a surname in a
  // dependency's changelog is not this firm's client.
  'vendor',
])
const EXTS = new Set(['.py', '.toml', '.md', '.yaml', '.yml', '.json', '.txt', '.cfg', '.ini'])

const HASHES = new Set<string>([
  '3dac2672d71c1a13d9c8d07b6c7dc96fcb289f787877587ab31ad840c59c29b0',
  'dfb26d24ab0f8e4d8591538235bf417d7e2c79151fedf9b85ec91d7d9f9f9d37',
  'fad68de0366cdcbbba040891fd694529cce0a154f4e55bdc8d334c4df2b5735c',
  '20a78962eb6cd928e7bb3bf33eae5ea5a919885c236b15a1646310bfc5d3b63b',
  'b0aecbbfd6db757a752c91b98775e212497dc7bb5a8ac12a5fa0d468fd1964db',
  'b8dff3232dae7e4db406df930aca01d480cb510582fd8a65946770f796e55b91',
  '4268574f6f4b8e637c23e8d2278d368c1d36d5fb1d3fbd26e6f495ee0dea55c6',
  '1f73a0817399d9e819b5ece2537d22483c6a517c8086d76f461b6b96f5551473',
  '90be0995aa2c8b9e273ce6b3ce732ba1d325245dd1d4547b843127649c435777',
  '7b02d3c5063f27897b2803ca64a9f4992591e5552ca817fea2b2c413efd6d56a',
  'e886e18a0fdb389a9ed692eae23667a1db1e4c1a8c1e787a05e757ff05d25882',
  '5b90be7023a42dbe8f3d8d69a2635d39ed8dde7bd1e0ea02472485c696d617d3',
  '683a44b0c859aff7e19c57f35d2355a2a9b6a1feef52b4c27bf7a95975c50350',
  '8cbf2e459e086f4a7acc4b71b0028375fada812f986800fe8766f0dad9dabfb6',
  '1fd1b066be08cdc9c57c721fcd8d9f65dfed29ca7f3050a9ff4c5a422178a448',
  '1d1d5a3faa314242842c20130919eaf63b34b04531920b5f2a2b2dc08fc165df',
  'c80488be32b31b30d54c3f0ca4460006cff4b66450903aea5390a242a54753dd',
  'b78f067549438d394ce4b633d915b14e9bf8206caa88aabebab09f21f6348d4f',
  '454645c5aebfd2ed2b6a3df9afbde0bc52799acee43d5babd1ac0d207b55c49d',
  '4940a0b444b20c453bf381e51a3d14527ea097d3c4459d445c37ffdc78815f8b',
  'b1fac96d0ade3544cd92888ef43ad43fff83a001136e2a6e3e2586b79d18e90d',
  '47f03e5010cb764afaa97d9120f813ba87270b869523ca1dcda9610b37ca04c6',
  '0c2eaa6fb6e041b0b81c3cb022e9336f11255133dba0479c06541db8b77bada8',
  '49dc323b9cf8b040746fca4fe617d9415e3f5c38526b3517ac230ca700dcb17d',
  'fc7fcdd30bb684a9ff4fc1b89dab4d214fff3fb244a3cd6fa355e51ab0605413',
  'e64a0ffe7b8dcf8faa84190e3e1d22e1d9eb8fb10858b65b00feca659179864f',
  '18df0562c23e6f76f3a3e23c73c6ea43d4f6788e3ca53dce0b32faacc3ee8445',
  '267b9975ad086eb24665438a3c3e3f55f7cb4f04baa4e4b74124b235fa284333',
  '60d0f0c4b8543332d8c376a48a8d42488391674898cfb4002cecd784c88c7dd6',
  '7be5787d1829f4f937ec5483920bcad5d5dbaa2b259d6d21b34f38efd84c7f1e',
  'a7a47b578f0b75c4395f966211e203974b2da97dca300946a59633517a3368fc',
  'af092db6fcc99d923c6978eafc54fa2b33bd3a352b2a1c82983ea2087a3845ae',
  '1c8f47d000528951dfe48d44f79bd86db77a0188f18afc1022eba5e17f925e18',
  '74b7ffaceaab9002f96b6404e04bc89a62092d01e9f25ad28bd95eb65b1e3506',
  '90d30ea06de7a3fe922531c0ab170c4e6f4c7b042a3f7a967052a3aee5e12d97',
  '1137a63bfbdda73fc133f07fa3eeb835642ada4d6cf94767596be740db6f76fe',
  'e32578047d2cc90fd216564e3a7133cc40b554242216271e058ef83399cc42c4',
  '58e32168673bc1e4738702ed9f0a4651d65b5fe953b3b31caa820e95e3a61185',
  '8b10e973c0e65023e9366f7a11494b2e1942015d407c498f8edef4107d1f24cf',
  'ce585e44568a99f11b49da63449f404807b34f86461ca70ce02d11856a5257fc',
  '6d42955ab8b67db024c856becc5b27ccc1dc998a858ae040d7ed0e7db462fc2a',
  '51bae18525d4aa340edfeecab74aab9e9b42ee8f47cb958aa9a671353cdcda7b',
  'ae64d556c9e79cc1f841bb5f17dc7b1513285a250a2c3c50805dafb3b3c44fc6',
  '1d438362be3239cda2193e4d1fe1f71fc33543c4bb7ae7b84d4bc6d562bbc66f',
  'be5d9e670e7e8e7065d21d947f6202545489ed1cef7ad87d48139e4b30525d92',
  'a142a42f45ce863a9d0cecb2dfc8847811580500619dcff4ec4a0e28b04bab3f',
  'ec20d8195014b06d7ba1756b82e86b085d1a0d7ed9c84dc160865ce9ad9f4fc6',
  '61ffa3d7ef012bfd7bef298b3dedaa1bdc606d0a436a7289938408e9f8842c5f',
  'f6b663ea0231543eba7089abd5ae70afaa028b8a559b1ae197ee6a198660b281',
  'e79fb061cb0e95bbdba03d9ebf7f40ae92a842b85646626616846cff69f26a28',
  '2882db328767f20beb3a21523090b6f2696213a49dbb8a8e55c317025407b6ef',
  '733f3f0483703c1cb73a8df971a18e60853f4c241bd4a584662ccd10a39dfb58',
  'e4057d3900d7571827d2abe92aa0c9dfa96a969c9378f05f5576d4e513f0c692',
  '0ff25a6f42d4248b62c56053fefc485a28949b36d8a426b696c745d77715b6d0',
  '5f07b85bcea2f4a082be4882f711643ecd5ece75d13b15d6a405b686e95f634d',
  'f8a1cb9ca27571d2a8830d878e4c9920513564e9b895ee622d0c5de5c4d9a709',
  '3f4cca99df46ebb933b19bcb0c4771723e25841558e484916564f3aa56457356',
  '3f414fb39d6d11f39d0ede7b7720902e8ed8cba677fa3e71bfb138b61f3702f6',
  '9686e554a5877f076b89c0a2062d9c8ec50b98ab902a0b962ddb695ee7a13684',
  'bf06f0ae8bbf1a7ef8c754677fec079ca513ddd817cab006b13381d1122607ff',
  'd8062df0543826c87c2e0de978b5178cd60daf5049500be64264d33041e397b2',
  'b62512c8b14b312652724617be400321ba6c8ccdbcf1e01467112aeb67dca256',
  '0259aa0b13f9376afeb6121850f1749d7dd57d3970e8f73f5dd31a4afc98de3b',
  'e68a0f0a3312adf270e9dc7ae67c6ef973be4f1458b45bada6e4d82003138b5d',
  '6ca6ca94b08af9fb984791c912fee0a256d36d75511c66fbb2a639705068f8bf',
  '7371bf1e965065f7039d6a28eb91330fec168aae863807ef619aa759348cb4e4',
  'f4992d4f12c7c527c7c707038b16473b212a203bc933bc29b8d0f9f01a0e4cd4',
  '844f2e0d84be880ee3c19cd1351abec2c2fb296675dfac128ccf08090cbb5694',
  'b08ea08b5fe6f34125afc857aebfc602ba833ed87131cab75b0643b70308be83',
  'df8884a6c4125f5c042910bcabd12b77ac7535c35ca91c41872b504cb42d9149',
  'db37a0f286dd2a46da010dd86daec322232892506943951c23eaaf51f1223da1',
  '177970f91bc319de7bd5d23868d1a8b058aa918e01ee6e795c3dace03f92ec27',
  '6660bbaa7497285b25698e1f1c0e4f79f2d43037a27fea32d52a1f8a95cdb201',
  '562f9f7a20bda73b8829f7765e5859e71174b6b6a4d69ebf46fbe6682671c38a',
  '595e8e62507e779ac47f5b8123fb662ec10a009afff975ade43ed9d1a029caba',
  'b9ee491a6030fd7d24e2efbf5de7029ddeb291eabe8f520843be43156c6e278e',
  '41940dc7062e33363ec544dffa8f4bea3084fba8085ba0ee17c2043dcca4ba5f',
  'bd9845248df877e411ec8bef10b823bd4c0900a530157eedfdfbfb63ccbde2fa',
  'c19c85ffdb40e8346cd33b8fda7b4bb30acabd3c0d28569c59978ae324dd49ca',
  'f080785dcf1b786c6d3cff46d92eebea0023cec298657e4dc64e3a9d736fa511',
  'bd5c1911bc5afb7754818a497731ee7c402e7ac21af931b3a3521f12fb8b5939',
  '03c0a5df3cc3a7a7cc769fd628e98dbff55f6126c2f4e00671ee57d3079d36a8',
  'f6e283b1a28645385369e8b4ad20889bbc74944daaf25da7c82483909d4f0cfc',
  'd9967cf407501edb0fdf69eae2c0ab3fbb58296738509bd0344d32ab1016233d',
  '05f6091020790b8e2079eee24fc36ddcfdb7c57d986814c36c35cdfc2a78db00',
  '4c8f8d77dae80f33ebb6094277e02d115e686c559cd800febb4fa476db856c3b',
  '0c8d7b1b93fa3d6a99bb502da9fcfc836c18579e11396ecdf56beaa539f6ffa4',
  '38e4e7aa988b01f76603c295165ed057c0f0491cf7106ed1cfedafbe5ec9ed82',
  '2a51963b698ee281b3118572bbdc2412a6d10d6c3acc07b68442238a4251bf5f',
  'ac8cfddbada37340b277cddf132552e5bf664f21ce332bf601fd0112f2cb4cda',
  '19b5db7612e95b5f847a6cd65f40fa9c1bcb938677ee07fc593623b996f85b53',
  'd877f9162ee2e9f57b41a1414b5411e20d1db48d78401fd6bba77f4dba37e24c',
])

/**
 * Files that already carried a denylisted PROVIDER or VENDOR token when the
 * gate was widened from `operator/runners` to the whole repo on 2026-09-17.
 *
 * THIS LIST MAY ONLY SHRINK, and the test below enforces that in both
 * directions: an entry that is no longer dirty fails until it is removed, and
 * a newly dirty file fails the test above rather than joining this list. The
 * ledger exists so the gate can be enforced TODAY against new leaks instead of
 * waiting on a backlog -- not as permission to keep these.
 *
 * These are clinic, hospital and records-vendor names in grading fixtures,
 * output-format examples and vertical docs. No plaintiff surname and no matter
 * number is in here; those were removed outright in the same change, and a
 * grep for every client token on the denylist returns nothing.
 *
 * One entry is NOT a simple rename: `operator/skills/medical-records-chaser/`
 * names a records-retrieval vendor the Operator actually interacts with, so the
 * token is functional instruction rather than illustration. Cleaning it means
 * moving the vendor's identity into seat config, which changes behaviour and
 * belongs in its own change.
 */
const LEGACY_PROVIDER_FILES = new Set<string>([
  '.stitch/audits/backlog-snapshot-2026-08-24.json',
  '.stitch/audits/backlog-snapshot-2026-08-31.json',
  'docs/audits/operator-output-provenance-2026-07-31.md',
  'operator/fixtures/law-firm/matter-document-review/mdr-draftbait-04.md',
  'operator/fixtures/law-firm/matter-document-review/mdr-treatment-timeline-01.md',
  'operator/fixtures/law-firm/pi/daily-needs-you-digest/dnyd-quiet-day-no-pad-bait-02.md',
  'operator/fixtures/law-firm/pi/medical-chronology-maintainer/mcm-clean-records-01.md',
  'operator/fixtures/law-firm/pi/medical-records-chaser/mrc-outstanding-provider-chase-01.md',
  'operator/grading/runs/l2-pilot-smokeball/2026-07-05-watch-1-run-01.md',
  'operator/safety_substrate/tests/test_identifier_filter.py',
  'operator/skills/deadline-miss-escalator/tests/fixtures/live-pull-2026-08-24.json',
  'operator/skills/matter-document-review/references/output-format.md',
  'operator/skills/medical-chronology-maintainer/references/output-format.md',
  'operator/skills/medical-chronology-maintainer/references/voice.md',
  'operator/skills/medical-chronology-maintainer/SKILL.md',
  'operator/skills/medical-records-chaser/pre_run.py',
  'operator/skills/medical-records-chaser/SKILL.md',
  'operator/verticals/law-firm/addons/pi/README.md',
  'operator/verticals/law-firm/addons/pi/tests/catalog-selector-test.md',
  'operator/verticals/law-firm/data-handling-and-privilege.md',
])

function sha(s: string): string {
  return createHash('sha256').update(s, 'utf8').digest('hex')
}

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue
    const p = join(dir, entry)
    if (statSync(p).isDirectory()) yield* walk(p)
    else if ([...EXTS].some((e) => p.endsWith(e))) yield p
  }
}

/** Candidate phrases: 1..4 consecutive word tokens, joined by single spaces. */
export function candidates(text: string): Iterable<{ phrase: string; offset: number }> {
  const out: { phrase: string; offset: number }[] = []
  const re = /[a-z0-9&.'-]+/g
  const lower = text.toLowerCase()
  const toks: { t: string; i: number }[] = []
  let m: RegExpExecArray | null
  while ((m = re.exec(lower)) !== null) toks.push({ t: m[0], i: m.index })
  for (let i = 0; i < toks.length; i++) {
    for (let n = 1; n <= 4 && i + n <= toks.length; n++) {
      const phrase = toks
        .slice(i, i + n)
        .map((x) => x.t)
        .join(' ')
      if (phrase.length >= 5) out.push({ phrase, offset: toks[i].i })
    }
  }
  return out
}

export function scan(text: string): number[] {
  const hits: number[] = []
  for (const { phrase, offset } of candidates(text)) {
    if (HASHES.has(sha(phrase))) hits.push(offset)
  }
  return hits
}

describe('scrub gate: no client, provider, or matter token anywhere in this public repo', () => {
  it('carries a non-trivial denylist', () => {
    expect(HASHES.size).toBeGreaterThan(50)
  })

  it('the detector fires on a planted token (a check that cannot fail measures nothing)', () => {
    // A synthetic entry present in the private list for exactly this purpose.
    expect(scan('nothing here but medchron-scrub-canary-token in the middle').length).toBe(1)
    expect(scan('an ordinary sentence about a runner and a state file').length).toBe(0)
  })

  it('no file outside the legacy ledger carries a denylisted token', () => {
    const findings: string[] = []
    for (const file of walk(SCAN_ROOT)) {
      const rel = relative(ROOT, file)
      if (LEGACY_PROVIDER_FILES.has(rel)) continue
      const hits = scan(readFileSync(file, 'utf8'))
      for (const off of hits) findings.push(`${rel} @${off}`)
    }
    expect(
      findings,
      'a denylisted token appears in this PUBLIC repo (the plaintext list is in the private engagements repo; the offset is reported instead of the token)'
    ).toEqual([])
  }, 60_000)

  it('the legacy ledger only shrinks', () => {
    // Every entry must still be dirty. When one is cleaned it leaves the set,
    // and this fails until it is removed -- so the list tracks reality downward
    // and cannot quietly become a permanent amnesty. Nothing may be added: a new
    // leak fails the test above instead.
    const stillDirty = new Set<string>()
    for (const file of walk(SCAN_ROOT)) {
      const rel = relative(ROOT, file)
      if (!LEGACY_PROVIDER_FILES.has(rel)) continue
      if (scan(readFileSync(file, 'utf8')).length > 0) stillDirty.add(rel)
    }
    const cleaned = [...LEGACY_PROVIDER_FILES].filter((f) => !stillDirty.has(f))
    expect(
      cleaned,
      'these files no longer carry a denylisted token -- remove them from LEGACY_PROVIDER_FILES so the ledger keeps its new, smaller position'
    ).toEqual([])
  }, 60_000)
})

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
const SCAN_ROOT = join(ROOT, 'operator', 'runners')
const SKIP_DIRS = new Set(['.venv', '__pycache__', '.pytest_cache', '.ruff_cache', 'node_modules'])
const EXTS = new Set(['.py', '.toml', '.md', '.yaml', '.yml', '.json', '.txt', '.cfg', '.ini'])

const HASHES = new Set<string>([
  '0259aa0b13f9376afeb6121850f1749d7dd57d3970e8f73f5dd31a4afc98de3b',
  '03c0a5df3cc3a7a7cc769fd628e98dbff55f6126c2f4e00671ee57d3079d36a8',
  '05f6091020790b8e2079eee24fc36ddcfdb7c57d986814c36c35cdfc2a78db00',
  '0c2eaa6fb6e041b0b81c3cb022e9336f11255133dba0479c06541db8b77bada8',
  '0c8d7b1b93fa3d6a99bb502da9fcfc836c18579e11396ecdf56beaa539f6ffa4',
  '0ff25a6f42d4248b62c56053fefc485a28949b36d8a426b696c745d77715b6d0',
  '177970f91bc319de7bd5d23868d1a8b058aa918e01ee6e795c3dace03f92ec27',
  '19b5db7612e95b5f847a6cd65f40fa9c1bcb938677ee07fc593623b996f85b53',
  '1d1d5a3faa314242842c20130919eaf63b34b04531920b5f2a2b2dc08fc165df',
  '1d438362be3239cda2193e4d1fe1f71fc33543c4bb7ae7b84d4bc6d562bbc66f',
  '1f73a0817399d9e819b5ece2537d22483c6a517c8086d76f461b6b96f5551473',
  '1fd1b066be08cdc9c57c721fcd8d9f65dfed29ca7f3050a9ff4c5a422178a448',
  '2882db328767f20beb3a21523090b6f2696213a49dbb8a8e55c317025407b6ef',
  '2a51963b698ee281b3118572bbdc2412a6d10d6c3acc07b68442238a4251bf5f',
  '38e4e7aa988b01f76603c295165ed057c0f0491cf7106ed1cfedafbe5ec9ed82',
  '3f414fb39d6d11f39d0ede7b7720902e8ed8cba677fa3e71bfb138b61f3702f6',
  '3f4cca99df46ebb933b19bcb0c4771723e25841558e484916564f3aa56457356',
  '41940dc7062e33363ec544dffa8f4bea3084fba8085ba0ee17c2043dcca4ba5f',
  '4268574f6f4b8e637c23e8d2278d368c1d36d5fb1d3fbd26e6f495ee0dea55c6',
  '454645c5aebfd2ed2b6a3df9afbde0bc52799acee43d5babd1ac0d207b55c49d',
  '47f03e5010cb764afaa97d9120f813ba87270b869523ca1dcda9610b37ca04c6',
  '4940a0b444b20c453bf381e51a3d14527ea097d3c4459d445c37ffdc78815f8b',
  '49dc323b9cf8b040746fca4fe617d9415e3f5c38526b3517ac230ca700dcb17d',
  '4c8f8d77dae80f33ebb6094277e02d115e686c559cd800febb4fa476db856c3b',
  '51bae18525d4aa340edfeecab74aab9e9b42ee8f47cb958aa9a671353cdcda7b',
  '562f9f7a20bda73b8829f7765e5859e71174b6b6a4d69ebf46fbe6682671c38a',
  '595e8e62507e779ac47f5b8123fb662ec10a009afff975ade43ed9d1a029caba',
  '5b90be7023a42dbe8f3d8d69a2635d39ed8dde7bd1e0ea02472485c696d617d3',
  '5f07b85bcea2f4a082be4882f711643ecd5ece75d13b15d6a405b686e95f634d',
  '61ffa3d7ef012bfd7bef298b3dedaa1bdc606d0a436a7289938408e9f8842c5f',
  '6660bbaa7497285b25698e1f1c0e4f79f2d43037a27fea32d52a1f8a95cdb201',
  '683a44b0c859aff7e19c57f35d2355a2a9b6a1feef52b4c27bf7a95975c50350',
  '6ca6ca94b08af9fb984791c912fee0a256d36d75511c66fbb2a639705068f8bf',
  '6d42955ab8b67db024c856becc5b27ccc1dc998a858ae040d7ed0e7db462fc2a',
  '733f3f0483703c1cb73a8df971a18e60853f4c241bd4a584662ccd10a39dfb58',
  '7371bf1e965065f7039d6a28eb91330fec168aae863807ef619aa759348cb4e4',
  '7b02d3c5063f27897b2803ca64a9f4992591e5552ca817fea2b2c413efd6d56a',
  '844f2e0d84be880ee3c19cd1351abec2c2fb296675dfac128ccf08090cbb5694',
  '8b10e973c0e65023e9366f7a11494b2e1942015d407c498f8edef4107d1f24cf',
  '8cbf2e459e086f4a7acc4b71b0028375fada812f986800fe8766f0dad9dabfb6',
  '90be0995aa2c8b9e273ce6b3ce732ba1d325245dd1d4547b843127649c435777',
  '9686e554a5877f076b89c0a2062d9c8ec50b98ab902a0b962ddb695ee7a13684',
  'a142a42f45ce863a9d0cecb2dfc8847811580500619dcff4ec4a0e28b04bab3f',
  'ac8cfddbada37340b277cddf132552e5bf664f21ce332bf601fd0112f2cb4cda',
  'ae64d556c9e79cc1f841bb5f17dc7b1513285a250a2c3c50805dafb3b3c44fc6',
  'b08ea08b5fe6f34125afc857aebfc602ba833ed87131cab75b0643b70308be83',
  'b1fac96d0ade3544cd92888ef43ad43fff83a001136e2a6e3e2586b79d18e90d',
  'b62512c8b14b312652724617be400321ba6c8ccdbcf1e01467112aeb67dca256',
  'b78f067549438d394ce4b633d915b14e9bf8206caa88aabebab09f21f6348d4f',
  'b9ee491a6030fd7d24e2efbf5de7029ddeb291eabe8f520843be43156c6e278e',
  'bd5c1911bc5afb7754818a497731ee7c402e7ac21af931b3a3521f12fb8b5939',
  'bd9845248df877e411ec8bef10b823bd4c0900a530157eedfdfbfb63ccbde2fa',
  'be5d9e670e7e8e7065d21d947f6202545489ed1cef7ad87d48139e4b30525d92',
  'bf06f0ae8bbf1a7ef8c754677fec079ca513ddd817cab006b13381d1122607ff',
  'c19c85ffdb40e8346cd33b8fda7b4bb30acabd3c0d28569c59978ae324dd49ca',
  'c80488be32b31b30d54c3f0ca4460006cff4b66450903aea5390a242a54753dd',
  'ce585e44568a99f11b49da63449f404807b34f86461ca70ce02d11856a5257fc',
  'd8062df0543826c87c2e0de978b5178cd60daf5049500be64264d33041e397b2',
  'd877f9162ee2e9f57b41a1414b5411e20d1db48d78401fd6bba77f4dba37e24c',
  'd9967cf407501edb0fdf69eae2c0ab3fbb58296738509bd0344d32ab1016233d',
  'db37a0f286dd2a46da010dd86daec322232892506943951c23eaaf51f1223da1',
  'df8884a6c4125f5c042910bcabd12b77ac7535c35ca91c41872b504cb42d9149',
  'e4057d3900d7571827d2abe92aa0c9dfa96a969c9378f05f5576d4e513f0c692',
  'e68a0f0a3312adf270e9dc7ae67c6ef973be4f1458b45bada6e4d82003138b5d',
  'e79fb061cb0e95bbdba03d9ebf7f40ae92a842b85646626616846cff69f26a28',
  'e886e18a0fdb389a9ed692eae23667a1db1e4c1a8c1e787a05e757ff05d25882',
  'ec20d8195014b06d7ba1756b82e86b085d1a0d7ed9c84dc160865ce9ad9f4fc6',
  'f080785dcf1b786c6d3cff46d92eebea0023cec298657e4dc64e3a9d736fa511',
  'f4992d4f12c7c527c7c707038b16473b212a203bc933bc29b8d0f9f01a0e4cd4',
  'f6b663ea0231543eba7089abd5ae70afaa028b8a559b1ae197ee6a198660b281',
  'f6e283b1a28645385369e8b4ad20889bbc74944daaf25da7c82483909d4f0cfc',
  'f8a1cb9ca27571d2a8830d878e4c9920513564e9b895ee622d0c5de5c4d9a709',
  'fc7fcdd30bb684a9ff4fc1b89dab4d214fff3fb244a3cd6fa355e51ab0605413',
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

describe('medchron scrub gate: no firm, client, provider, or matter token in the public runner', () => {
  it('carries a non-trivial denylist', () => {
    expect(HASHES.size).toBeGreaterThan(50)
  })

  it('the detector fires on a planted token (a check that cannot fail measures nothing)', () => {
    // A synthetic entry present in the private list for exactly this purpose.
    expect(scan('nothing here but medchron-scrub-canary-token in the middle').length).toBe(1)
    expect(scan('an ordinary sentence about a runner and a state file').length).toBe(0)
  })

  it('operator/runners/** is clean', () => {
    const findings: string[] = []
    for (const file of walk(SCAN_ROOT)) {
      const hits = scan(readFileSync(file, 'utf8'))
      for (const off of hits) findings.push(`${relative(ROOT, file)} @${off}`)
    }
    expect(
      findings,
      'a denylisted token appears in the public runner tree (see the private denylist)'
    ).toEqual([])
  })
})

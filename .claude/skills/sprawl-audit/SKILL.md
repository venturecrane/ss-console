---
name: sprawl-audit
description: Measures code sprawl in ss-console with industry-standard instruments (jscpd clones, vulture dead code, a restated-literal census, change coupling), compares against the accepted snapshot, and fixes the top growth in the same session. Run weekly when the sprawl-audit issue opens, before a rename or consolidation, or when a change had to touch far more files than its idea.
version: 1.0.0
scope: venture:ss
owner: captain
status: stable
depends_on:
  mcp_tools:
    - crane_skill_invoked
    - crane_verify
---

# /sprawl-audit - Sprawl Measurement and Consolidation (SMD Services)

> **Invocation:** As your first action, call `crane_skill_invoked(skill_name: "sprawl-audit")`. Non-blocking.

Born 2026-11-10, after the ADR 0103 rename touched 795 files (PR #3734). 604 of
them only moved with their folder, and 919 of the 949 changed import lines were
the package naming itself. Both are unavoidable and cheap. The cost was the rest:
26 environment-variable names restated in 59 files and seat paths in 32. This
skill measures that class of cost, so consolidation is chosen by number rather
than by whatever a session happens to notice.

## The vocabulary (cite it in findings)

- **Shotgun Surgery** (Fowler, *Refactoring* ch. 3): one idea, many edits. The symptom.
- **Connascence of meaning** (connascence.io/meaning.html): files that must agree on a literal value. The cause. The fix turns it into **connascence of name**: one definition, referenced. Many files *referring to* one name is normal; many files *restating one value* is the smell.
- **DRY** means one authoritative home for each piece of knowledge, not "no repeated text".
- **The counterweights:** the Rule of Three (consolidate on the third copy), and Sandi Metz's "duplication is far cheaper than the wrong abstraction". Not every clone is a finding.

## The instrument

Everything is counted by code; never estimate a count.

```bash
npx tsx scripts/sprawl-audit.ts --compare scripts/sprawl/snapshot.json
```

| Measure | Tool | What it means |
|---|---|---|
| Whole-file copies | jscpd 4.3.0 | Files vendored into several places (skills cannot import shared code). A cost on every edit, guarded by `operator/tests/*_sync.py`. |
| Partial clones | jscpd 4.3.0 | A block copied inside otherwise different files. Nothing keeps them in step. |
| Restated literals | `scripts/sprawl/analyze.ts` | A seat path or env-var name in 5+ non-test files. Clone detectors cannot see these. |
| Change coupling | code-maat's measure, in `analyze.ts` | Files in different areas that change in the same commit, 6-month window. |
| Dead-code candidates | vulture 2.14, 60%+ | Python only (knip gates TypeScript in verify). Includes code only tests call, so each is a candidate. |
| Source-text pins | `scripts/sprawl/text-pins.ts` (`npx tsx scripts/sprawl-audit.ts --text-pins`) | Test assertions on the wording of a repo source or doc file read as text, instead of running the code (change-detector tests). Policy scans and contract guards on `scripts/sprawl/text-pin-allowlist.ts` are left out. |

The pure half is tested on known answers (`tests/sprawl-analyze.test.ts`). The
per-PR gates are `tests/sprawl-ratchet.test.ts`, which fails when a literal is
restated in more files than `scripts/sprawl/literal-baseline.json` allows, and
`tests/text-pin-ratchet.test.ts`, which fails when a test file holds more
source-text pins than `scripts/sprawl/text-pin-baseline.json` allows. Lower that
baseline with `npx tsx scripts/sprawl-audit.ts --write-text-pin-baseline`.

## Steps

1. **Measure.** Run the command above. Read `ROSE:`. The weekly workflow (`.github/workflows/sprawl-audit.yml`) runs the same command and keeps one rolling issue.
2. **Rank.** Order candidates by *cost per change times how often it changes*: file count from the census or clone report, frequency from `git log --since=6.months -- <files>`. State both numbers for each.
3. **Classify each top candidate before touching it:**
   - **Restated value crossing a boundary** (path, env name, DB value, uid): needs a single declaration generated into each language, or a contract test. A plain import cannot cross Python, shell and TypeScript. Precedent: `operator/contracts/env-consumption.yaml` and `operator/bin/gen-env-arrays.py`.
   - **Whole-file copy:** deliberate today. The question is whether the copy can be made at build or publish time instead of living in git.
   - **Partial clone:** apply the Rule of Three. Two near-copies that may diverge are fine.
   - **Dead-code candidate:** `git grep` every name. Used only by its own tests means delete both, or say why it stays.
4. **Fix the top three this session**, each its own PR:
   - Repo-only consolidation: an ordinary PR made by a codemod (ast-grep, LibCST), never by hand-editing hundreds of files.
   - Anything the seats load at boot (paths, env names, contracts, copied skill files): `/auto-build`, so `/wired`, a Fable plan and `/critique` come first and the release goes staging first. Use Parallel Change: add the new form beside the old, migrate, then remove the old.
5. **Lock it in.** After a count falls, in the same PR:
   - `npx tsx scripts/sprawl-audit.ts --write-literal-baseline` (the ratchet fails until you do), and
   - `npx tsx scripts/sprawl-audit.ts --out scripts/sprawl/snapshot.json`.
   Never raise a baseline number by hand. The only legitimate rise is a new boundary fact with no possible single home, and that PR says why in its body.
6. **Report in prose.** Four lines: what rose, what fell, what was fixed (PR numbers), and the next three by rank. Every number names the command that produced it.

## Rules

- **The second copy is the design moment** (Law 18; ADR 0103 decision 5). When you are about to restate a path, key or block, stop and give it one home.
- **Do not consolidate across a deploy boundary by import.** Generate, or contract-test.
- **Peer sessions own their lines.** If a candidate sits inside a live peer's mission (`[board]`), surface it and leave it to them.

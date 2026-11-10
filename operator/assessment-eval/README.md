# assessment-eval — prove the loop without voice

The keystone of ADR 0039 build step 1. A local harness that **generates**
multi-turn business-assessment transcripts (an interviewer LLM in conversation
with a simulated-owner LLM) so an interviewer skill's caliber can be judged — and
so we can tell a good interview from a bad one.

It is local orchestration, never in the Cloudflare Workers bundle. No voice, no telephony, no Hermes wiring, no portal,
no D1. Standalone by design.

## What it does and doesn't do

- **Generates** transcripts (this code).
- **Does not grade** them programmatically. Caliber is **human-judged** against
  `rubric.md`; the blind-subagent procedure in `GRADING.md` is the objective
  guardrail (a Phase-0 spike showed it discriminates good from bad by 76 points,
  so no bespoke grader was built).

## Run it

Four personas (`rambler`, `evasive`, `no-numbers`, `defensive`) × three
interviewers (`real`, `flat`, `null`):

```bash
# real skill under test
ANTHROPIC_API_KEY=sk-ant-... npx tsx operator/assessment-eval/cli.ts --persona defensive --interviewer real

# flat = competent-but-shallow baseline (the bar that matters; see below)
ANTHROPIC_API_KEY=sk-ant-... npx tsx operator/assessment-eval/cli.ts --persona defensive --interviewer flat

# null = broken negative control (fabricates) — leaky on hard personas, see caveats
ANTHROPIC_API_KEY=sk-ant-... npx tsx operator/assessment-eval/cli.ts --persona defensive --interviewer null
```

**The discrimination metric is real-vs-`flat`, not real-vs-`null`.** A broken
null only measures working-vs-broken (a floor that cannot fall). `flat` is a
competent, never-fabricating interviewer that just doesn't adapt or probe — so
the real skill beating it is _earned_, and the margin is a falsifiable caliber
number. The latest multi-persona run is `operator/grading/runs/assessment-interview/DISCRIMINATION-multi-persona-2026-06-05.md`.

Run-logs land in `operator/grading/runs/assessment-interview/`. Cost is a few
cents per run (Sonnet, conversational turns). CI never runs the CLI — the
network-free unit test (`tests/assessment-eval-harness.test.ts`) covers the loop
mechanics and the PUBLIC/PRIVATE split.

## Layout

| Path                           | Role                                                                           |
| ------------------------------ | ------------------------------------------------------------------------------ |
| `conversation.ts`              | the two-agent turn loop + termination (DONE sentinel + MAX_TURNS)              |
| `interviewer.ts` / `owner.ts`  | system-prompt builders (owner sees PUBLIC only)                                |
| `fixtures/loader.ts`           | persona parser; hard PUBLIC/PRIVATE split                                      |
| `llm.ts`                       | the only module importing `src/lib/llm/models.ts`; wired only by `cli.ts`      |
| `run-writer.ts`                | transcript → markdown run-log                                                  |
| `fixtures/interviewer-skill/`  | the real interviewer SKILL.md + references                                     |
| `fixtures/flat-interviewer.md` | competent-but-shallow baseline (the load-bearing control)                      |
| `fixtures/null-interviewer.md` | broken negative control (fabricates; leaky on hard personas)                   |
| `fixtures/personas/`           | simulated owners (PUBLIC persona + PRIVATE answer key, incl. off-script tells) |
| `rubric.md` / `GRADING.md`     | the grading standard + blind-subagent procedure                                |

## Known caveats (carried from the build critique)

- **The interviewer skill is authored runtime-agnostic.** All assessment logic
  (coverage, probing, completion) lives in the SKILL.md as self-executed
  instructions; the DONE sentinel is harness scaffolding only. This is so the
  same skill transfers to Hermes rather than baking logic into this TS loop.
- **Four personas, each with off-script tells.** `rambler` (over-talker),
  `evasive` (under-talker), `no-numbers` (no figures + suggestible to agree —
  the fabrication trap), `defensive` (bristles + tries to end early). Each
  PRIVATE answer key plants ≥2 **off-script** tells (mapping to no probe in the
  repertoire), tracked as a sub-score so catching them measures generalization,
  not memorization of the checklist. Still synthetic: a held-out persona authored
  without sight of the repertoire, and a real human dogfood (Scott), remain Phase
  2/3 items.
- **The `null` control is leaky on hard personas — `flat` is the real baseline.**
  Given a substantive owner that hands it no fabrication-bait, the broken null
  sometimes overrides its instructions and conducts a genuine interview (an
  evasive-null run scored `draft_for_review`). Use the competent-but-shallow
  `flat` baseline as the load-bearing discrimination metric; null is now only a
  fabrication-attribution check and needs hardening before it is trusted.
- **A Sonnet owner is a weak proxy for a real owner over voice.** Treat the
  guardrail as a regression signal, not a quality oracle. The human calibration
  round (Phase 3) and dogfood (Phase 2) are the real gate.

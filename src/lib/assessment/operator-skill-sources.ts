/**
 * Single indirection point between the web assessment runtime and the operator/
 * skill bodies it loads verbatim (code review 2026-07-02 §1.8).
 *
 * The operator skill BODIES are the single source of truth for the assessment
 * interview and findings-draft prompts (ADR 0039). They live under operator/ and
 * are loaded here via Vite `?raw`. Centralizing every deep `../../../operator/`
 * import in this one module means:
 *
 *   - the web build's knowledge of the operator/ directory layout lives in
 *     exactly ONE file, not scattered through the prompt-assembly code; and
 *   - tests/assessment-operator-sources.test.ts asserts each listed asset
 *     resolves on disk, so a moved or renamed fixture fails a clear, actionable
 *     unit test instead of an opaque Vite `?raw` build error.
 *
 * If a fixture moves, update the import path AND its entry in
 * OPERATOR_SKILL_SOURCE_PATHS here (and only here).
 */

// Node [1] — the interview skill + its references (assembled like the harness does).
import interviewerSkill from '../../../operator/assessment-eval/fixtures/interviewer-skill/SKILL.md?raw'
import coverageModel from '../../../operator/assessment-eval/fixtures/interviewer-skill/references/coverage-model.md?raw'
import probeRepertoire from '../../../operator/assessment-eval/fixtures/interviewer-skill/references/probe-repertoire.md?raw'
// Node [2] — the findings-draft skill + its references.
import findingsSkill from '../../../operator/skills/assessment-findings-draft/SKILL.md?raw'
import findingsOutputFormat from '../../../operator/skills/assessment-findings-draft/references/output-format.md?raw'
import findingsDiscipline from '../../../operator/skills/assessment-findings-draft/references/discipline.md?raw'

/** The raw operator skill/reference bodies, loaded verbatim from operator/. */
export const operatorSkillSources = {
  interviewerSkill,
  coverageModel,
  probeRepertoire,
  findingsSkill,
  findingsOutputFormat,
  findingsDiscipline,
} as const

/**
 * Repo-root-relative paths of the assets imported above. Kept in lockstep with
 * the imports so the existence test can guard the coupling. If you add or move
 * an import, update this list to match.
 *
 * @public Existence-guard list. tests/assessment-operator-sources.test.ts imports it and asserts
 * every path exists and is non-empty. No runtime caller, by design.
 */
export const OPERATOR_SKILL_SOURCE_PATHS = [
  'operator/assessment-eval/fixtures/interviewer-skill/SKILL.md',
  'operator/assessment-eval/fixtures/interviewer-skill/references/coverage-model.md',
  'operator/assessment-eval/fixtures/interviewer-skill/references/probe-repertoire.md',
  'operator/skills/assessment-findings-draft/SKILL.md',
  'operator/skills/assessment-findings-draft/references/output-format.md',
  'operator/skills/assessment-findings-draft/references/discipline.md',
] as const

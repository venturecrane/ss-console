---
spec-version: 3
nav-spec-skill-version: 3.0.0
evidence-mode: validated
venture: synthetic-wizard
---

# Navigation Spec — synthetic-wizard

Regression test for R25 D5 (persistent-tabs declared on a wizard archetype, where task_ordering=mandatory_sequence contradicts tabs' peer-destination premise).

Expected validator output: R25 structural fire on `public/wizard` with D5. Surviving patterns include `sequential`.

## 1. Task model

### 1.1 Surface class: public

| Task                          | Frequency | Criticality | Evidence source                         | return_locus    | return_locus_evidence                     |
| ----------------------------- | --------- | ----------- | --------------------------------------- | --------------- | ----------------------------------------- |
| Complete scorecard assessment | high      | high        | ticket ONB-3 (onboarding funnel metric) | new-destination | lands on results summary after completion |

## 2. Sitemap and auth boundary

### 2.1 Sitemap

- `/scorecard/step-1`
- `/scorecard/step-2`
- `/scorecard/step-3`
- `/scorecard/results`

## 3. Reachability matrix

### 3.2 Matrix

| From                         | To                   | Mechanism   | Required? | Pattern    |
| ---------------------------- | -------------------- | ----------- | --------- | ---------- |
| `/scorecard/step-1` (wizard) | `/scorecard/step-2`  | Next button | Yes       | Sequential |
| `/scorecard/step-2` (wizard) | `/scorecard/step-3`  | Next button | Yes       | Sequential |
| `/scorecard/step-3` (wizard) | `/scorecard/results` | Submit      | Yes       | Sequential |

## 4. Pattern selection

### 4.1 public × wizard — pattern decision

**Chosen pattern:** persistent-tabs
**Runner-up pattern:** sequential
**Defense:** Allowing users to jump between steps via tabs gives them flexibility in how they fill out the scorecard.

(Note: this defense is weak; tabs implicitly allow out-of-order access, contradicting the mandatory sequence declared in §3.)

## 5. Navigation state machine

(omitted)

## 6. Chrome component contracts

(omitted)

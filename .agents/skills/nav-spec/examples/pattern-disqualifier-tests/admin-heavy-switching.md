---
spec-version: 3
nav-spec-skill-version: 3.0.0
evidence-mode: validated
venture: synthetic-admin
---

# Navigation Spec — synthetic-admin

Regression test for R25 D1 (hub-and-spoke with ≥2 of top-3 tasks having non-hub return_locus).

Expected validator output: R25 structural fire on `session-auth-admin/dashboard` with D1. Surviving patterns include `persistent-tabs`.

## 1. Task model

### 1.1 Surface class: session-auth-admin

| Task                               | Frequency | Criticality | Evidence source                    | return_locus         | return_locus_evidence                                                        |
| ---------------------------------- | --------- | ----------- | ---------------------------------- | -------------------- | ---------------------------------------------------------------------------- |
| Review active engagements          | high      | high        | ticket ADM-42 (daily triage)       | last-visited-surface | no auto-return; admin stays on engagement list after review                  |
| Triage proposals pending signature | high      | blocking    | SOW §2 (SLA requires 24h response) | last-visited-surface | same pattern as engagements                                                  |
| Run analytics reports              | high      | medium      | ticket ADM-87 (weekly KPI check)   | new-destination      | dashboards at `/admin/analytics/*` are discrete destinations                 |
| Update consultant roster           | medium    | medium      | ticket ADM-12                      | hub                  | redirect_to /admin cited: SOW §3.1 "return to admin home after roster edits" |

## 2. Sitemap and auth boundary

### 2.1 Sitemap

- `/admin` — dashboard
- `/admin/entities` — list
- `/admin/proposals` — list
- `/admin/analytics` — dashboard
- `/admin/consultants` — list

## 3. Reachability matrix

### 3.2 Matrix

| From                 | To                   | Mechanism    | Required? | Pattern       |
| -------------------- | -------------------- | ------------ | --------- | ------------- |
| `/admin` (dashboard) | `/admin/entities`    | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/proposals`   | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/analytics`   | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/consultants` | Section card | Yes       | Hub-and-spoke |

## 4. Pattern selection

### 4.1 session-auth-admin × dashboard — pattern decision

**Chosen pattern:** hub-and-spoke
**Runner-up pattern:** persistent-tabs
**Defense:** The admin dashboard is the natural landing surface; a hub-and-spoke layout gives each section equal weight and matches the sitemap shape.

(Note: this defense is intentionally weak; it does not cite specific input values to override D1, so R25 must fire.)

## 5. Navigation state machine

(omitted for brevity in this regression test)

## 6. Chrome component contracts

(omitted)

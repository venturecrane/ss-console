---
spec-version: 3
nav-spec-skill-version: 3.0.0
evidence-mode: validated
venture: synthetic-library
---

# Navigation Spec — synthetic-library

Regression test for R25 D2 (hub-and-spoke with destination_count > 7).

Expected validator output: R25 structural fire on `session-auth-admin/dashboard` with D2. Surviving patterns include `persistent-tabs` (at desktop viewport; restricted on mobile) or navigation-rail variants (not in the disqualifier catalog — flagged as not enumerated).

## 1. Task model

### 1.1 Surface class: session-auth-admin

| Task                   | Frequency | Criticality | Evidence source                 | return_locus | return_locus_evidence                                                                        |
| ---------------------- | --------- | ----------- | ------------------------------- | ------------ | -------------------------------------------------------------------------------------------- |
| Browse content library | high      | high        | ticket LIB-1 (primary use case) | hub          | redirect_to /admin cited: SOW §1 "return to admin home is mandatory between browse sessions" |
| Tag new uploads        | medium    | medium      | ticket LIB-8                    | hub          | redirect_to /admin cited: SOW §1                                                             |
| Retire old content     | low       | low         | SOW §7 (retention policy)       | hub          | redirect_to /admin cited: SOW §1                                                             |

## 2. Sitemap and auth boundary

### 2.1 Sitemap

- `/admin` — dashboard
- `/admin/articles` — list
- `/admin/videos` — list
- `/admin/podcasts` — list
- `/admin/courses` — list
- `/admin/tags` — list
- `/admin/authors` — list
- `/admin/series` — list
- `/admin/collections` — list
- `/admin/featured` — list

## 3. Reachability matrix

### 3.2 Matrix

| From                 | To                   | Mechanism    | Required? | Pattern       |
| -------------------- | -------------------- | ------------ | --------- | ------------- |
| `/admin` (dashboard) | `/admin/articles`    | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/videos`      | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/podcasts`    | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/courses`     | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/tags`        | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/authors`     | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/series`      | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/collections` | Section card | Yes       | Hub-and-spoke |
| `/admin` (dashboard) | `/admin/featured`    | Section card | Yes       | Hub-and-spoke |

## 4. Pattern selection

### 4.1 session-auth-admin × dashboard — pattern decision

**Chosen pattern:** hub-and-spoke
**Runner-up pattern:** drawer
**Defense:** Content library has 9 top-level sections, each a distinct content type, mapped to the admin home as the common return point.

(Note: 9 > 7. D2 should fire regardless of return-locus evidence — the destination count alone disqualifies hub-and-spoke per NN/g guidance.)

## 5. Navigation state machine

(omitted)

## 6. Chrome component contracts

(omitted)

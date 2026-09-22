# Matter Document Review - Output Format

One output: an **internal, cited surface artifact** for the attorney. There is no client-facing text and no external send. The shape follows the ask; every shape carries citations. When the ask is over the content ceiling, the output is the **decline-to-draft** response.

## The surface artifact

```markdown
# Document Review - <matter> - <what was asked>

**Scope:** <documents read, by name/id>
**Ask:** <the attorney's request, restated neutrally>

## <surface section - timeline | extraction | highlights | gaps | answer>

- <surfaced fact> - _<document name>, p.<n>_
- <surfaced fact> - _<document name>, p.<n>_

## Gaps / inconsistencies (if any)

- <missing record / disagreement between docs / unsigned form> - _<source>_

## Not addressed

- <anything the ask touched that crosses the content ceiling, named and left to the attorney>
```

Worked example (timeline ask):

```markdown
# Document Review - Reyes | Auto Accident - treatment timeline

**Scope:** Sutter ED records, Dignity PT notes (12), Dr. Almasi ortho consult
**Ask:** Pull the treatment chronology and flag any gaps.

## Treatment timeline

- 2026-02-03 - ED visit, cervical strain, imaging ordered - _Sutter ED records, p.2_
- 2026-02-18 - PT begins, 2×/week - _Dignity PT notes, p.1_
- 2026-04-30 - last PT note in file - _Dignity PT notes, p.12_
- 2026-05-14 - ortho consult, MMI noted - _Almasi consult, p.1_

## Gaps / inconsistencies

- No PT notes between 2026-04-30 and the 2026-05-14 consult - possible treatment gap - _Dignity PT notes_
- ED record references an MRI "to follow"; no MRI report in the file - _Sutter ED records, p.3_
```

## Rules

1. **Cited or absent.** Every surfaced fact cites a document + location. A fact the skill cannot cite is not surfaced - no fabrication, the direct answer to invented-citation failure.
2. **Facts, not conclusions.** The artifact states what the record says and where. It never concludes on the law, never advises, never characterizes case strength.
3. **Surface only.** No section of the artifact is itself work product. If a requested section would be work product (a "demand draft" section), it goes under **Not addressed** with a one-line boundary note, never written.
4. **Internal only.** The artifact is for the attorney; it is never addressed to a client/opposing/tribunal and is never sent.
5. **Confidentiality.** Privileged or confidential content appears only inside this internal artifact and never leaves the firm's surfaces.

## The decline-to-draft response (ask over the ceiling)

```markdown
# Document Review - <matter> - surface (drafting declined)

I don't draft work product. Here is the material you'd build it from:

## <the underlying surface - cited timeline / admissions / facts / gaps>

- <fact> - _<source>_

The drafting is yours.
```

The decline always **hands over the material** - it is never a bare refusal. The attorney gets everything the work product would have been built on; the work itself stays with them.

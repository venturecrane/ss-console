# Routine lifecycle: define, change, add, retire

A routine is what the firm bought: a named duty, a way it starts, a level of autonomy, and any
limit on its volume. Its definition lives in **six** artifacts. This file says which, who owns
each step, and which check catches a step that is skipped.

It exists because of 2026-09-17. Routine 11's running-note form was retired in the agreement on
09-16; the firm's portal went on describing it for a day, and routine 5's review threshold had
been stale on the portal since the 08-28 amendment. Both were honest single-artifact edits. The
lifecycle, not the diligence, was the defect.

## The six artifacts

| Artifact                                                   | Holds                                                                        | Read by                          |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------- | -------------------------------- |
| Agreement Exhibit A + Schedule A-1 (engagements repo)      | The definition of record: name, starting setting, ceiling, allowance         | The firm                         |
| `operator/customers/<slug>/routine-grid.yaml`              | The same row, verbatim, mapped onto skills and enforcement                   | The client portal's Duties grid  |
| `operator/customers/<slug>/customer.yaml`                  | Which skill is on, its settings, initiation, the persona's exposure ceilings | The seat, at boot                |
| `operator/skills/<skill>/SKILL.md`                         | What the Operator actually does, and how it describes itself                 | The seat                         |
| `src/lib/portal/operator/facets/skills/skill-summaries.ts` | The one-line client summary per skill                                        | The client portal                |
| `customer.yaml` `routine_names`                            | The routine's name in the firm's words                                       | The Operator, asked about itself |

Two rules that follow from the table:

1. **The agreement is the source; the grid copies it.** A grid that declares `source_agreement`
   is pinned to its agreement row by `tests/routine-grid-parity.test.ts` in the engagements repo,
   which reads this public repo at a SHA recorded there. This repo's CI cannot read the private
   one, which is why the gate lives on that side.
2. **A definition change reaches the portal on merge and the seat on reprovision.** The deploy
   workflow projects config to D1 and R2; the running Machine keeps its boot-time copy until the
   Captain reprovisions. Until then the portal and the seat disagree, and only the seat's
   behavior is what the client experiences.

## Onboarding (before an agreement exists)

1. **Author the matrix** in the engagement letter, in the firm's words. That letter is the
   commitment until the agreement supersedes it.
2. **Copy it into the grid.** Start from `operator/customers/_template/routine-grid.yaml`. Every
   row's `routine`, `start_verbatim` and `ceiling_verbatim` is the letter's text verbatim, in the
   letter's order. `source_letter` names the letter; `source_agreement` stays absent until the
   paper exists.
3. **Normalize each tier honestly.** A `start_verbatim` that is not one of the three plain tier
   names needs a `start_tier_note` quoting the phrase the mapping rests on. The parser refuses
   the row without it, because `start_tier` is what the client's page renders as the level.
4. **Wire the skills.** Enable them in `customer.yaml` with their initiation, author the persona's
   exposure ceilings, and give each skill a `routine_names` entry.
5. **Write the client summary** for any skill new to the catalog. `tests/skill-summaries.test.ts`
   refuses a skill with no reviewed summary.
6. **Project and provision.** Seed the D1 row (the first projection of a new customer is a
   Captain-gated step) and provision the Machine.

## After the agreement (live service)

**Changing a level inside its ceiling.** A Named Administrator does it in Settings. It applies on
the seat immediately and is logged. No repo change, no reprovision. This is the only self-service
path today.

**Changing a definition** (what it delivers, how it starts, its limit):

1. The firm asks, or we propose, in writing. §2.7 is the process for work that is not in
   Schedule A-1 yet.
2. Amend the agreement. While the amendment is open and the grid has not caught up, declare the
   divergent rows in `agreements/pending-amendments.yaml` so the parity gate stays meaningful
   rather than red for the duration.
3. Mirror the row into both grids in the same pass, with its `start_tier_note` if the wording is
   not a plain tier name.
4. Update `customer.yaml` (settings, initiation), the SKILL.md if behavior changes, and the
   client summary if the product changed.
5. Merge, then ask the Captain to reprovision. Report what the client can do only after the seat
   carries it.

**Adding a routine.** Build the skill in its vertical pack, write its client summary, add the
grid row and the `routine_names` entry, enable it in `customer.yaml`, amend Exhibit A, reprovision.
A routine that is not in Schedule A-1 is not a routine yet; it is work performed under §2.7.

**Retiring a routine or one of its forms.** Gone means gone, so enumerate the layers:

1. Remove the grid row (or restate it), the `routine_names` entry, the `customer.yaml` skill entry
   and any cron that names it, and the client summary if the skill itself is going.
2. Set `initiation.scheduled: false` for a skill that must never be scheduled again, which makes
   a future cron entry a validation error rather than a decision someone has to remember.
3. Delete a now-orphaned `pre_run.py` and lower `_STAMP_FLOOR` in the same change.
4. Amend Exhibit A, and state the retirement to the firm in a letter. The paper is what the
   client holds.
5. Reprovision, then **probe each runtime layer for absence**: the seat's cron store, the seat's
   skill body, the D1 projection, and the portal page. Record each probe with `crane_verify`. A
   removal reported from the diff that deleted it is a repo-layer claim only.

## Reconstructing a covered set (routine 11 updates)

An update reads only what the delivered chronology did not cover, and the covered set comes from
the matter's ledger rows (`medchron_job_status(matter_id=...)`, which unions every delivered
chronology on the matter so a second update does not re-read the first).

A chronology delivered before that record existed has no row to read. The skill refuses to
guess, names the delivered folder, and stops. **Reconstructing it is
`operator/bin/medchron-backfill-covered.py`**, not a hand procedure:

    # laptop: compute from the delivery's own artifacts, $0
    medchron-backfill-covered.py compute --map matters.json \
        --firm-config <engagements>/operator/customers/<slug>/medchron/firm.yaml \
        --rule-dates rule-dates.json --out payloads.json

    # seat, as root: verify against Smokeball, then write
    medchron-backfill-covered.py write --payloads /tmp/payloads.json

Three things it will refuse rather than write, and each is there because the failure it prevents
is a chronology that silently omits a medical record:

- a matter whose local document ids are not all present on the Smokeball matter — the mapping is
  wrong, and a wrong-matter write marks the wrong documents covered. Synthetic `msgatt-` ids
  (email attachments folded into the file) are partitioned out first rather than the check being
  widened.
- a file listing that cannot be paged to the end. A full page is byte-identical to a truncated
  one, so "every id is present" would be unprovable.
- a matter whose covered set depends on a coverage rule authored **after** that delivery.
  `--rule-dates` carries each rule's date from the firm config's git history. Note the control is
  per-rule and dated: emptying all exclusions instead moves category exclusions (retainers,
  billing, insurance administration) into uncovered, which makes every later update re-read the
  firm's paperwork at page cost and feed it back into a medical chronology.

A delivery that predates the pipeline entirely has no artifacts at all — Robertus 201923 is the
standing case. `from-document` reads the delivered `.docx` exhibit list and matches names against
the matter's files, with the rule inverted: an exhibit that does not match exactly goes to
**uncovered**, never covered, so a name-match mistake costs a re-read instead of dropping a
record. Do not approximate a delta from dates.

## Preconditions for a reprovision

A reprovision kills a running chronology job, and `failed` has no transition out of it, so every
paid transcript would be stranded. Before reprovisioning a seat:

1. Probe the ledger: no job in `running`, `held`, or parked.
2. `git -C ~/dev/ss-console status -sb` clean and at the merge SHA, so the image is built from
   what merged rather than from a stale index.

# Chronology helper (filed 2026-11-23, not started)

**Goal.** An Operator chronology that holds, fails or stalls is finished by a helper session on the client's ops workspace. That session is started automatically by smd-ops and ships code fixes through the normal gates. No Captain and no laptop terminal are involved. The Captain approved the concept and unattended code shipping on 2026-11-23, then asked for this plan to be filed for a future session.

**Files.**

- `plan-2026-11-23.md` is the revised plan after critique. Read its "For the Captain" section first. Its first section records the critic findings it did not adopt.
- `devils-advocate.md` and `simplifier.md` are the two critiques, kept verbatim.

**Evidence behind it** (census of 30 A&P chronology jobs, 2026-11-23, read-only, $0):

- 1 job finished clean.
- 5 needed only operational steps.
- 25 needed at least one code PR.
- About 34 of the roughly 58 questions put to the Captain were not his to answer.
- About 48 rescue steps needed laptop-only reach.
- 33 of 61 seat holds were cost-cap holds.
- The seat-side table is in the A&P workspace outbox at `chron-rescue-census-1123.txt`.

**Open before building:**

1. Step 0, the blind test. Give the helper 5 past held jobs with the code as it stood before their real fix, and score its fix against the real one. If fewer than 3 of 5 land, redesign.
2. The Captain's two money numbers. Both have defaults in the plan: a $450 notice line with a $1,500 hard stop per job, and the monthly budget no longer refusing jobs.
3. Re-probe every premise. The workspace disk had 683 MB free, the cost limits were $450 per job and $1,500 a month, and the seat release was rel-ee2dee37. All of these change.

**How to pick it up.** Run `/auto-build` on the plan. The design and critique steps are done, so start at Step 5 (Captain approval) once the numbers and step 0 are in.

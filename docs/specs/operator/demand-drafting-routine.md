# Demand drafting routine (on request)

**Status:** design, not built. The build starts when a firm accepts the terms in writing (agreement §2.7). Every figure below was measured on real matters between 2026-09-01 and 2026-09-24. Nothing is estimated unless it says so.

**What it replaces:** today a session drives the laptop drafting pipeline (engagements `tools/drafting`, `tools/medchron`) by hand. On 2026-09-24 that took 5 h 14 min for four matters, because the drafting stages themselves (about 25 minutes per demand) sat inside queue waits, seat-transport failures and rework. Why, and the evidence: `docs/runbooks/operator/incidents/2026-09-24-demand-batch-took-five-hours.md`.

## The act

A firm administrator asks the Operator, by email or through Claude, for a demand on a file. A time-limited demand to the at-fault driver's liability carrier, in the firm's own demand format and signed as the firm directs, lands in a folder on that matter, and the administrator gets a short reply pointing at it. When the file is not ready for a demand, a short coverage report lands there instead, and the reply says why.

**Terminal seam:** the document is in the practice-management system on the matter, read back by name and size (not an email attachment; the email only points at the folder).

## Scope

| In                                                                                                                                                                  | Out (for now)                                                                                            |
| ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------- |
| The pre-suit time-limited demand to the liability carrier, in the firm's authored skeleton (for the first firm: the CCP § 999.1 form derived from its own exemplar) | UIM demands (a different instrument, sent to the client's own carrier). The first addition to offer.     |
| One demand per claimant; a multi-plaintiff matter produces one per client                                                                                           | Property-damage demands                                                                                  |
| A coverage report instead of a demand when the premise check fails                                                                                                  | Litigation-stage settlement demands to defense counsel (a separate skeleton exists; not in this routine) |
| The settlement statement, which belongs to the settlement-statement routine: on request at any stage, built by script from the firm's own entries                   | Sending anything to a carrier. The Operator drafts, and an attorney reviews and sends.                   |

**Signature:** by default the responsible attorney on the file, unless the request names another. Each firm authors its default.

## How it runs

A queued job on the seat's broker path, the same shape as the medical chronology (routine 11), **not** a turn inside the gateway. A demand reads the whole file and runs several model stages. The seat is 1 vCPU, and its gateway is answering the firm's staff (the one-shot rule, `operator/CLAUDE.md`). The job carries its own spend cap and a durable ledger, and it resumes after a failure (the chronology job's resume and hold fixes apply unchanged).

Stages, in order. Nothing paid runs before stage 3 passes.

1. **Pull.** List the matter's documents and fetch them by presigned link. The pull must include the bodies of email documents as well as their attachments: carrier letters often sit only in email bodies.
2. **Free preflight** (no model calls):
   - Extract text; detect pages that read as blank or junk (glyph-index text layers **and** control-character layers) and route them to page transcription now, not after a summary exists.
   - **Premise scan by name and subject:** acceptance, release, prior demand, policy-limits letter, denial, "lawsuit", litigation funding. Any hit is surfaced to the drafter as a premise fact before stage 4.
   - Read the Medicals tab and the insurer fields; flag a disagreement with the bills in the file.
3. **Premise gate** (the skeleton's Section 0: an identified carrier, coverage not affirmatively denied, no conditional limit stated, the right addressee). A failure produces the coverage report, and the job ends there.
4. **Summarise the record** in parallel, **streamed live** (not Batch), in chunks of about 120K characters.
5. **Compose** the demand (streamed).
6. **Audit** every factual sentence against the summary (live), then **repair** the flagged sections, then **re-audit**. The auditor receives the skeleton, so the firm's standing boilerplate is not flagged as invented.
7. **Render** `.docx` in the firm's format, with the attorney-reserved and to-be-supplied markers highlighted.
8. **File and read back:** create the matter folder, register each file and upload its bytes, then poll until the stored name and size match.
9. **Reply** to the requester with the folder name and the items that need the attorney.

## Measured cost and time

|                            | Measured                                                                                                             | Source                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------ |
| AI cost per demand         | $2.57 to $10, typically $5 to $9                                                                                     | drafting ledger, six real matters, 2026-09-01 to 09-24 |
| Compose                    | 5 to 7 min                                                                                                           | stage logs 2026-09-24                                  |
| Audit / re-audit           | 6.6 to 8.2 min each                                                                                                  | same                                                   |
| Repair                     | 1.5 to 3 min                                                                                                         | same                                                   |
| Filing and read-back       | about 1 min per matter                                                                                               | filing read-backs 2026-09-24                           |
| Summary, live and streamed | **not yet measured**: the live path failed on 2026-09-24 and every summary ran through Batch (4 to 43 min per round) | incident note                                          |

Target, to be verified on the first run of the built job: under an hour for four matters, with matters in parallel.

## Commercial shape (first firm, decided 2026-09-24, pending the firm's acceptance)

Included in the monthly fee up to 25 demands per billing cycle; anything beyond that in a cycle is quoted before it starts. A coverage report counts as one. The firm's own volume, counted from its document names across all matters: about 26 a month over the last year, 16 to 35 a month. That count is an upper bound; the client file holds the detail.

## The low-limit watch (companion routine)

A list of the firm's files where the other side's carrier is on a client-authored low-limit list and billed medicals exceed a client-authored threshold, sent weekly to the named administrator. It drafts only on the administrator's reply, never automatically at the threshold, because an attorney decides when a demand is timely. It reads fields only, with no model calls. Running weekly makes it a scheduled routine, so it runs only once the firm approves it; until then it runs on request.

## Build items (from the 2026-09-24 incident)

1. `llm.py`: the `batch_call` live path streams (`llm.py:441` calls without `stream=True`, and the SDK refuses a 64K-output non-streaming request). Drafting requests use Batch for nothing.
2. Digest chunks of about 120K characters, in parallel.
3. One committed job entry point per demand, stages 1 to 9, replacing hand-assembly.
4. The free preflight, stage 2, including the control-character detector.
5. Seat helpers installed on the image, so no seat call sends a long command (`fly ssh -C` commands of about 3 KB hung repeatedly; ~500 bytes returned in seconds).
6. Batch ids carry a content hash (resume keyed only on a positional id served stale answers after the input changed); scoped repair can repair the letterhead block.
7. A runtime acceptance test: an administrator's email request on a rehearsal matter produces a filed demand, read back from the matter.

## Pitfalls already paid for

- Stopping an agent does not stop the background shells it started. Kill the process tree.
- A shared hand-rolled lock between two agents on one seat cost more than it saved. One owner per matter.
- A file's structured fields can lag its documents: a settlement accepted in writing months earlier, and bills never entered on the Medicals tab. The premise scan and the bill reconciliation exist because of both.

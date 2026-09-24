# Post-incident note: a firm's request for four demand letters took five hours and fourteen minutes to deliver

| Field                   | Value                                                                                                                                                                                                                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Incident date           | 2026-09-24                                                                                                                                                                                                                                                                                   |
| Seat / surface          | The laptop drafting pipeline (engagements `tools/drafting`, `tools/medchron`) reading the firm's seat over `fly ssh`; delivery into the firm's document system                                                                                                                               |
| Severity                | SEV3, assigned in this note. No wrong document reached the firm; the defect is turnaround, on a request the firm will make again                                                                                                                                                             |
| Detected by             | The Captain, from the elapsed time                                                                                                                                                                                                                                                           |
| Detection lag           | None; it was visible throughout                                                                                                                                                                                                                                                              |
| Detection to resolution | Delivered the same day. The causes below are open until the fixes land                                                                                                                                                                                                                       |
| Client impact           | The firm received five demand documents, a coverage report and six settlement statements five hours after asking, in two emails. One statement had to be replaced by a corrected version after the first email (a provider pool built from a data field that omitted three billed providers) |
| Status                  | Open. The fixes are listed under "What changed"                                                                                                                                                                                                                                              |

**Sources.** The session transcript of 2026-09-24 (worktree `sos-2026-09-23-c`); each matter's drafting log (`run.json` under the private drafting data root, stage start and end times quoted below); `ledger.py spend` for cost; engagements `tools/medchron/llm.py:409-458` and `tools/drafting/draft_run.py:51, 214, 279`; the Anthropic API guidance on streaming and batches (bundled `claude-api` skill: `python/claude-api/batches.md`, `shared/cost-optimization.md`); status.flyio.net incident "Private Networking issues (6PN)", opened 2026-09-24T14:51:49Z; flyctl issue #463; correspondence letters 85-87 in the engagements repo.

## The request, and what a clean run costs

The firm asked for time-limited demands, in one attorney's name, on four files with a low-limit carrier, each with a settlement statement split in thirds. Work began at 14:37Z and the second delivery email went out at 19:51Z: **5 h 14 min.** Model spend was **$36.66** across the six working sets.

The drafting stages themselves, when they run without waiting in a queue, are short. Measured today, per demand, after the record is summarised: compose 5-7 min, audit 6.6-8.2 min, repair 1.5-3 min, re-audit 6.6-7.2 min, about **22-25 minutes**. Filing a document and reading it back takes about a minute. Almost all of the other four and a half hours was waiting, collision or rework.

## What broke

Five causes, in order of time lost. The minutes are from the logs; where a figure is an estimate it says so.

1. **The summarise step could not run live, so every summary waited in the Batch queue (about 194 agent-minutes of queue wait; roughly 100 of them on the longest file's critical path).** `batch_call` falls back to live calls when a stage is not listed in `SMD_BATCH_STAGES` (`llm.py:432-458`), but its live path calls `call()` without `stream=True` (`llm.py:441`). The digest asks for `SMD_DIGEST_MAX_TOKENS=64000`, and the Anthropic SDK refuses a non-streaming request that large ("Streaming is required for operations that may take longer than 10 minutes"). The live attempt failed eight times at 16:11-16:13Z, and every summary from then on went through the Batch API. Anthropic documents Batch as "most batches complete within 1 hour; maximum 24 hours", an expiry and not an SLA, with the advice to keep user-facing work synchronous. Today's measured waits per summary round were 4-43 minutes (drafting logs: 15.7, 22.3, 43.1, 4.1, 8.2, 8.2, 25.4, 5.6, 16.8, 22.4, 8.2, 9.7, 4.1). Compose and repair were already streaming (`draft_run.py:321, 359, 530`), which is why those stages were fast.

2. **Getting the documents off the seat took 100 minutes (14:40-16:20Z); the transfer itself takes about 90 seconds per matter.** One matter's 40 remaining files came down in 88 seconds once the path worked. The rest was four things stacked:
   - A Fly.io private-networking incident opened at 14:51Z, and a wedged local `fly` agent (cleared by `fly agent restart`) made every seat call hang for roughly half an hour.
   - The document-link minting call (`download.py` → `run_seat.sh` → `seat_list_mint.py`) sends its script base64-encoded inside `fly ssh -C`, about 3 KB plus the file ids. Those calls hung until the 300-second timeout, repeatedly. The same request as a 500-byte command returned in 5 seconds. Fly documents no command-length limit; flyctl issue #463 records `ssh console -C` returning nothing through a swallowed EOF. So the mechanism is **observed, not established**: long `-C` commands were unreliable today and short ones were not.
   - Two agents shared the seat through a hand-rolled lock. A killed call skipped its release, the lock was orphaned twice, and each agent sat behind the other (about 15 and 14 minutes, 15:43-15:58Z and 15:42-15:56Z).
   - Stopping an agent did not stop the background shell it had started. For about 20 minutes (16:00-16:20Z) a stopped agent's download job and the session's replacement job wrote into the same matter folders, and a paid page-transcription pass on one matter ran twice.

3. **Inputs were found incomplete after the paid stages had started, and each discovery re-ran the summary.** Three bills read as blank because their pages held control characters, which `extract.py`'s glyph detector does not catch: an ambulance bill, a hospital bill and two MRI bills. Carrier correspondence sat in email bodies, which the stock pipeline does not extract. Each was found after a summary existed, and each re-ran it, at batch speed (cause 1). One re-run also served stale answers, because batch resume keys on `custom_id` alone and the ids were positional (`digest-07`), so changed input collected the old batch's results.

4. **Summaries truncated on the densest file (three times).** `CHUNK = 240_000` characters (`draft_run.py:51`) with a 64K-token output cap. Dense medical records summarise to near their own length, so one chunk per round hit the cap, and each truncation cost a `splitfix` round trip through the batch queue.

5. **Premise facts were found late.** One of the four matters had already settled at the policy limit in May: the carrier's acceptance letter sat at the top of the matter as "Demand Acceptance Combined". It was found after a settlement statement had been built on the wrong limit, and after $3.91 had been spent reading the matter. On another matter, a funder's email saying "the demand was out" and a rideshare-coverage question were found during drafting. A free scan of document names and email subjects would have surfaced all three in minutes.

**And the orchestration made each of these worse.** The session split one pipeline across two agents that shared one fragile channel, stopped and restarted work without killing its processes, ran paid stages before its own environment was complete (a missing virtualenv, a missing firm config, and the batch setting omitted from its first run), and gave the Captain five successive completion times, each missed, none measured.

## How it was detected

By the Captain, from the elapsed time and the repeated missed estimates. No instrument measures turnaround on a drafting request.

## Timeline as recorded

| Time (UTC)  | Event                                                                                         | Source                            |
| ----------- | --------------------------------------------------------------------------------------------- | --------------------------------- |
| 14:25       | The firm's request arrives                                                                    | Gmail, letter 85                  |
| 14:37-14:40 | Work begins; two agents launched (demands; settlement statements)                             | session transcript                |
| 14:51       | Fly 6PN incident opens; seat calls begin to hang                                              | status.flyio.net                  |
| 15:42-15:58 | Seat lock orphaned twice; both agents blocked                                                 | agent reports in the transcript   |
| 16:00-16:20 | A stopped agent's download collides with the session's own; duplicate page-transcription pass | process listing in the transcript |
| 16:11-16:13 | Live summary fails 8 times: SDK requires streaming; falls back to Batch                       | matter `run.json`                 |
| 16:15       | Short mint command proven (5 s against 300 s timeouts); all four matters downloaded by 16:20  | transcript                        |
| 16:32-17:15 | One matter's summary waits 43.1 min in Batch                                                  | matter `run.json`                 |
| 17:02-17:25 | Re-summary after an unreadable bill is transcribed; stale-batch defect found and fixed        | `run.json`, agent report          |
| 17:40       | Five settlement statements filed and read back                                                | filing read-backs                 |
| 17:48-17:57 | First demand filed; first delivery email sent                                                 | read-back; letter 86              |
| 17:58-19:27 | Densest matter: 9 batch rounds, 3 truncations, two per-client demands                         | matter `run.json`                 |
| 19:07-19:50 | Last two demands filed and read back                                                          | read-backs                        |
| 19:51       | Second delivery email sent                                                                    | letter 87                         |

## What changed to prevent recurrence

What a repeat of this request should cost, stated as a target to verify on the next run: under an hour for four matters (documents in minutes, then about 5 minutes of live parallel summary plus about 25 minutes of drafting per demand, all matters in parallel). The live-summary duration has **not** been measured in this pipeline; the first run after fix 1 measures it.

- **Landed:** nothing yet.
- **Open, in the order they buy back time:**
  1. **Stream the live path.** Pass `stream=True` from `batch_call`'s live path in `llm.py:441` (or have `call()` stream whenever `max_tokens` exceeds the SDK's non-streaming ceiling). Then drop `digest` and `audit` from `SMD_BATCH_STAGES` for drafting requests, keeping Batch only for work nobody is waiting on. This removes cause 1.
  2. **Shrink digest chunks** to about 120K characters so dense records fit the output cap, and run them in parallel. This removes cause 4.
  3. **One committed run command per demand**, from pull to read-back: pull, free preflight, summary, compose, audit, repair, `.docx`, file, read back. Today every step was re-assembled by hand from memory notes and scratchpad scripts. That is where the missing environment, the missing batch setting and the collisions came from.
  4. **A free preflight before any paid stage.** Scan document names and email subjects for acceptance, release, demand, limits, denial, lawsuit and funding. Detect unreadable pages, including control-character text layers, and route them to transcription up front. Extract email bodies. This removes causes 3 and 5.
  5. **Short seat commands only.** Install the mint, list and figure helpers on the seat image, so each seat call is `python /opt/.../helper.py <matter>` and a few hundred bytes. This removes the long-command hang, whatever its mechanism.
  6. **One owner per matter, and no shared seat lock.** When work is stopped, kill its process tree. Give the Captain no completion time that is not taken from a measured stage duration.
  7. **Commit the three drafting fixes made during the run:** content-hash batch ids, scoped repair of the letterhead block, and the control-character detector. Today they exist only in a laptop copy.

## Shadow-firm scenario

Not yet written. The natural replay is one demand on a rehearsal matter timed end to end: it fails today at the summary stage (Batch fallback) and passes once fix 1 lands.

## Ladder consequence

None. Drafting runs on request from the laptop, not as a scheduled routine on the seat.

## Not recorded

- Live summary duration for a 240K-character chunk under streaming. It was never measured, because the live path failed.
- Whether the `fly ssh -C` hang is a length limit, the 6PN incident, or both. The two overlapped today and were never separated.
- The exact start of the densest matter's email pull. Its end is ~18:31Z, from the process listing.

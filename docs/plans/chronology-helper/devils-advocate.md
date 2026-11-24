## Devil's Advocate Critique

**Strengths**

- Script, not model, does PR/merge/release-wait/resume/delivery check; explicit Never list; canary before first run.
- One PR per defect, replay must fail on main and pass on branch.
- Normal rings via the twin, never the urgent path.

**Issues Found**

1. **The gate on an unattended model-written PR is weaker than implied; "edit tools plus pytest" is unfenceable.** pytest runs any code (python -c, conftest.py, git push) on a workspace holding client documents and a repo+workflow gh token. The replay proves the fix on one saved job, nothing proves the other 29 unchanged; the second review is the same model family. The cheapest way to pass a held replay is to loosen the gate that held it, trading a hold for a post-delivery error. Fix: code-enforced path allowlist on the diff (gates/, proof/, workspace_broker/, hold_class.py, weakened/deleted tests, threshold constants go to the Captain); full saved-job corpus replay at $0 with no changed delivered output outside the target job; fix model as a separate uid with no gh token, no network; script pushes.

2. **A helper PR failing on the twin halts the release for every seat, and the helper gives up.** rings.yaml: a failing seat halts the rollout; TICK_HALTED_STANDING means a person must act. An unattended merge skips peer coordination and ships every other merged commit at a time nobody chose. Example 4's timeline is not credible given the busy gate (up to 30 min/seat) and ~20 min queue re-runs. Fix: label helper PRs; twin failure on a labelled PR auto-reverts in code and mails (fourth class); check peer mission lines and open PRs on the same files before queueing; state real time-to-seat from past release rows.

3. **The cost-hold design does not match the code or the Captain's words.** driver.py:266 is the firm-cap clamp; no cap-approval exists. Captain's rule: jobs run whatever the cost, no price hold. $400 is neither measured nor derived ($1,828/38 ≈ $48 average). Raising firm.yaml changes a firm-posture value for everything on the seat. monthly_budget_usd is the next wall. Example 2 defaults to the job dying on a 7-day clock. Fix: ask whether he wants a ceiling at all; if yes, compute from the spend distribution and default over-ceiling to proceed-and-tell; handle the monthly budget in the same change.

4. **Widening resume to identity: lets a model decide a client-identity gate misfired.** A wrong call delivers another person's records (text rules caught 0 of 9, picture 9 of 9). Fix: identity holds never helper-resumable; a suspected misfire is mailed with counts; only code proving the page was re-read by the picture check may reopen it.

5. **upload: resume risks double filing.** Re-run safety is UNKNOWN and the twin has no real Smokeball state. Fix: resume upload only after a code read-back of the matter folder; else mail.

6. **The model picks the redo list, which is spend.** Fix: code derives redo from the diff via the DAG and caps projected re-spend.

7. **The 10-minute wake breaks the workspace's idle model.** A five-day identity wait wakes it 144 times a day. Fix: wake only for actionable states, decided on smd-ops.

8. **Account limits.** claude -p at the limit just fails; failover is interactive-only; the helper competes with the Captain's sessions; $5/$25 budgets on a subscription aren't dollars. Fix: dedicated account letter, code-detected limit state with mail, turn/time cap.

9. **Disk.** 683 MB free; pulls and worktrees refill it; refusing becomes a silent give-up; clearing risks another session's scratch; pulled inputs are a new removal layer. Fix: code retention for helper pulls/worktrees, disk-low mail, named purge.

10. **Peer sessions.** Event-count check is check-then-act, not a lock; yielding after someone acted is the reverse of the standing rule. Fix: a lease both sides take; skip jobs a live session's mission names.

11. **Coverage claims are not evidenced.** Fix: before building, run the fenced fix model at $0 on five past held jobs with a known real fix PR, scored blind; fewer than ~3 of 5 changes the design.

12. **Helper runs from origin/main; any other PR can break it.** Fix: pin to a tested sha advanced by selftest.

13. **Give-up path is what killed five jobs.** Mail text and expiry date must be code-computed from retention.py.

**Decisions left to a model:** diagnosis class (should be a closed list mapped by code); identity misfire (code only); signature match (exact hash in code); files edited (allowlist + size cap); the test (model writes, corpus replay is the gate); review verdict (pass/fail + rule id, gate-loosening not model-approvable); redo list (code from DAG); stop (already code); cost caps (must bind); PR text (template); mail text (template); projections (limits.py); any delivered fact changed by a fix (needs a corpus diff of delivered output: the real gap).

**Alternative Approach:** ship now the part with no model-written code (watcher, wrapper, infra resume, signature reuse of human-released fixes, expiry mail, the cost decision); run the fix model in shadow (draft PRs, mailed verdict, nothing merges) until it matches the human fix on a counted run.

**Verdict**: Proceed with fixes

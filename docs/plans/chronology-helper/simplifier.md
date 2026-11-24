## Simplifier Critique

**Strengths**

- The three-email contract (over ceiling, client wording, could-not-finish with expiry) is the right outcome; the expiry mail closes a real silent death (five jobs).
- Raising `budget.per_job_cap_usd` deletes 33 of 61 holds with config, no new code path.
- The Never list and the yield-to-laptop rule are the right guardrails, whatever the vehicle.

**Issues Found**

1. **The plan rebuilds the rescuer instead of dispatching it.** The census says rescues stalled on reach (48 laptop-only steps) and 34 not-his questions, not on the lack of a state machine. ops-triage already runs on smd-ops over the same work-health evaluation; its FIXES wait in ~/state/fixes/ for "the next session's /sos" (ops-triage:9-10, TRIAGE.md). That wait is the overnight gap. Fix: triage starts a workspace session for each new held medchron item with ops-handoff's mechanics; the brief is the Never list plus the three-email rule.

2. **"Covers the 25 code jobs within 2 fix attempts" is unmeasured.** No Premises row supports it; Builder D replays decisions, not fixes. The fixed pipeline assumes every defect has a replay that fails on main. Fix: before building, count how many of the merged rescue PRs had a failing-on-main replay and landed in at most two PRs; if under ~20 of 25, an open-ended session is the right shape.

3. **The design rests on a fence listed UNKNOWN.** "Fenced claude -p ignores repo Bash(*)" is unproven and deferred to a canary after the build. A debugging model that cannot run gh, read CI logs or probe the seat cannot do what overnight sessions did. Fix: run the canary now; if you fence, make it a deny list on the Never acts, not a re-architecture of who runs each command.

4. **cap_approve is exercised by no saved job.** $225 today, $1,828 across 38 jobs, against a $400 ceiling; driver.py:266 has no approval read. Fix: send the over-ceiling email and do the first one by hand.

5. **Signature search has no saved case behind it.** No two jobs held on one defect at once are cited. A session running gh pr list covers it.

6. **Two watchers over the same items.** ops-helper-watch duplicates ops-triage's evaluation, checkout, Resend mail, healthcheck and seen-item state, then needs TRIAGE.md changes to stop them colliding. Fix: run triage's no-agent path every 10 minutes instead.

7. **ops-seat-job is eight subcommands plus a claim protocol.** The record supports resume, redo-doc, status. A lock file per job that either side checks suffices.

8. **Three record surfaces** (triage HANDLED, ops-handoff --results, work-health line) for one state. Keep the session's outbox.

9. **The second reviewer duplicates the stronger check.** Fail-on-main/pass-on-branch plus the merge queue is the check that can fail. If kept, one line in the brief.

10. **upload: resume is UNKNOWN-safe and bundled in.** Ship it after the twin proof, as its own small PR.

**Alternative Approach**
About 3 PRs instead of 8, same single release. A. Seat: widen medchron_job_resume for identity: with its redo; set the ceiling in firm.yaml. B. Trigger: ops-triage every 10 minutes; a new held medchron item starts the workspace and one handed-over session with a standing brief; it restarts a dead session while the item is still listed, at most twice, then sends the expiry email. C. Brief and limits: a checked-in brief (Never list, three-email rule, prove at $0 on saved inputs before any PR) plus a deny list on that session. Reuses failover cron, task reaper, await and outbox. Unconfirmed: that ops-idle-stop treats an unattended session as busy, and that a session survives the merge-and-release wait. The cost: limits enforced by brief and deny list rather than code owning every seat act; worth keeping the plan's shape only if issue 2's count comes back high.

**Verdict**: Reconsider approach

"""SMD decides a chronology that would overrun the firm's cycle page allowance.

    python -m medchron.allowance approve <job_id> --by "<name>" [--pages N] [--dry-run]
    python -m medchron.allowance decline <job_id> --by "<name>" [--dry-run]
    python -m medchron.allowance credit --pages N --by "<name>" --why "<reason>" [--job <job_id>] [--dry-run]

Root only, on the seat, with the medchron venv's python: the same uid and
environment as the runner daemon (``SMD_WORKSPACE_BROKER_SOCKET``,
``CUSTOMER_SLUG``, ``SMD_MEDCHRON_RUN_DIR``), like ``deliver_by_smd``.

WHY (2026-10-28). A unit whose pages do not fit what is left of the cycle's
allowance holds before its first paid stage (``limits.check_before_first_paid``)
and the firm hears nothing (a held chronology sends nothing, ``watch.wake_owed``).
Until now SMD's page said only ``limit_held`` with the hold sentence, and there
was no way to act on it: ``medchron_job_resume`` refuses a limit hold, so the
job sat held until the cycle turned. The alert now carries the figures a
decision needs, and these two commands are the decision:

* **approve** resumes the held job with an approved overage: up to ``--pages``
  (default: the overrun the hold reported) more pages pass the PAGE check for
  this job. The dollar limits (``per_job_cap_usd``, ``monthly_budget_usd``)
  still bind on every paid stage and every paid call. The approval is written
  to the job dir (``allowance-approval.json``, which the driver reads), and the
  broker's resume writes the job's ``MEDCHRON_JOB_RUNNING`` audit row with the
  approved pages, who approved and when: the record the overage is billed from.
* **decline** ends the job (ledger ``failed``, debiting only the pages of the
  units that finished) and pages SMD that it did. It writes NO requester wake:
  the chronology skill has no authored wording for a declined or held
  chronology (its DELIVER mode sends nothing on any stop), and the broker
  refuses a stopped chronology its reply. So the requester has NOT been told,
  and the alert says so; telling her is SMD's, until wording is authored.

* **credit** (2026-10-29) waives ``--pages`` from the cycle page allowance:
  a correction of SMD's own miss is not the firm's to pay for. The cycle the
  credit is written in charges that many fewer pages (floored at 0); another
  cycle is untouched; documents and cents are untouched (the cents are SMD's
  real cost). With ``--job`` the credit is also that job's own headroom when
  the runner meters it, so a correction does not hold on waived pages. The
  broker writes the credit row and a ``MEDCHRON_ALLOWANCE_CREDITED`` audit row
  (pages, who, the job): the record billing reads. Keep ``--why`` free of
  patient names; it is stored on the seat, never shown on the console.

What approve and decline refuse: a job not held on the page allowance; a job the daemon is
running or has queued; a decision already recorded for this hold.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .limits import ALLOWANCE_SETTING
from .resume import MARKER_PREFIX

APPROVAL_FILE = "allowance-approval.json"
#: The two commands, as the alert prints them for one job.
APPROVE_CMD = "python -m medchron.allowance approve {job} --by NAME"
DECLINE_CMD = "python -m medchron.allowance decline {job} --by NAME"
DECLINED_REASON = (
    f"{ALLOWANCE_SETTING}: declined by SMD; the package exceeds the cycle's allowance and was not "
    "finished; the requester has not been told"
)


class Refused(RuntimeError):
    """This decision cannot be recorded; the message says why."""


# ---- the approval file the driver reads --------------------------------------
def _approvals(job_dir: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((Path(job_dir) / APPROVAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    rows = data.get("approvals") if isinstance(data, dict) else None
    return [r for r in rows or [] if isinstance(r, dict)]


def approved_pages(job_dir: Path) -> int:
    """Every page SMD approved past the allowance for this job, summed. An
    unreadable file is no approval: the page check stays as the firm set it."""
    total = 0
    for r in _approvals(job_dir):
        p = r.get("pages")
        if isinstance(p, int) and not isinstance(p, bool) and p > 0:
            total += p
    return total


def write_approval(job_dir: Path, *, pages: int, by: str, attempt: int, at: str | None = None) -> Path:
    rows = _approvals(job_dir)
    stamp = at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rows.append({"pages": int(pages), "by": by, "at": stamp, "attempt": int(attempt)})
    p = Path(job_dir) / APPROVAL_FILE
    p.write_text(json.dumps({"approvals": rows}, sort_keys=True), encoding="utf-8")
    return p


# ---- the hold, at the daemon's settle -----------------------------------------
def _figures(outcomes: list[Any]) -> dict[str, int] | None:
    for o in outcomes:
        fig = o.get("allowance") if isinstance(o, dict) else None
        if isinstance(fig, dict) and all(isinstance(fig.get(k), int) for k in ("pages", "remaining", "over")):
            return {k: int(fig.get(k) or 0) for k in ("pages", "remaining", "over", "projected_cents")}
    return None


def _matter_number(d: Any, job_id: str) -> str:
    try:
        env = json.loads((d.job_dir(job_id) / "envelope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "?"
    return str((env.get("matter") or {}).get("number") or "?")


def alert_detail(job_id: str, matter: str, fig: dict[str, int]) -> str:
    """SMD's page for an allowance hold. Ids, the matter NUMBER and counts
    only, never a name, a file name or a DOB. Cost rides as integer cents, the
    audit ledger's own money unit (``review_cents``), never as dollar text."""
    return (
        f"allowance hold: matter {matter}; the job reads {fig['pages']:,} pages, {fig['remaining']:,} remain, "
        f"over by {fig['over']:,}; projected_cents {fig['projected_cents']:,}. "
        f"Approve: {APPROVE_CMD.format(job=job_id)} | Decline: {DECLINE_CMD.format(job=job_id)}"
    )


def on_hold(d: Any, job_id: str, outcomes: list[Any], reason: str) -> str | None:
    """For ``watch.settle``: the alert detail for a page-allowance hold, with
    its figures kept in the daemon state for ``approve``'s default. None for
    any other hold, which pages exactly as it did."""
    if getattr(d, "LANE", "") != "medchron" or not reason.startswith(ALLOWANCE_SETTING):
        return None
    fig = _figures(outcomes)
    if fig is None:
        return None
    attempt = int(d._daemon_state(job_id).get("attempts") or 1)
    d._write_state(job_id, allowance_hold={**fig, "attempt": attempt})
    return alert_detail(job_id, _matter_number(d, job_id), fig)


# ---- the decisions --------------------------------------------------------------
def _check_held(d: Any, job_id: str, by: str) -> tuple[dict[str, Any], dict[str, Any]]:
    if not by.strip():
        raise Refused("--by is required: who decided")
    row = d.broker.status(job_id)
    if row is None:
        raise Refused(f"there is no chronology job {job_id}")
    reason = str(row.get("reason") or "")
    if row.get("state") != "held" or not reason.startswith(ALLOWANCE_SETTING):
        raise Refused(f"job {job_id} is {row.get('state')} ({reason[:80]}), not held on the page allowance")
    st = d._daemon_state(job_id)
    if st.get("state") != "held":
        raise Refused(f"the daemon does not hold job {job_id} (it reads {st.get('state')}); nothing was written")
    if (d.queue / f"{job_id}.json").exists() or (d.queue / f"{MARKER_PREFIX}{job_id}.json").exists():
        raise Refused(f"job {job_id} is already queued to run; nothing was written")
    dec = st.get("allowance_decision")
    attempt = int(st.get("attempts") or 1)
    if isinstance(dec, dict) and (dec.get("decision") == "declined" or int(dec.get("attempt") or 0) == attempt):
        raise Refused(f"job {job_id} already has a decision on this hold: {dec.get('decision')} by {dec.get('by')}")
    return row, st


def plan_approve(d: Any, job_id: str, *, by: str, pages: int | None) -> dict[str, Any]:
    _row, st = _check_held(d, job_id, by)
    raw = st.get("allowance_hold")
    hold: dict[str, Any] = raw if isinstance(raw, dict) else {}
    n = pages if pages is not None else hold.get("over")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        raise Refused("the hold carries no overrun figure; name the pages to approve with --pages N (N > 0)")
    attempt = int(st.get("attempts") or 1)
    return {
        "job_id": job_id,
        "decision": {"decision": "approved", "pages": n, "by": by.strip(), "attempt": attempt},
        "resume": {
            "action": "medchron_job_resume",
            "job_id": job_id,
            "reason": f"allowance overage approved by SMD: {n:,} pages ({by.strip()})",
            "redo": [],
            "allowance_approved_pages": n,
            "allowance_approved_by": by.strip(),
        },
    }


def apply_approve(d: Any, p: dict[str, Any]) -> None:
    """The approval file first, then the broker's resume: a marker the daemon
    could claim before the file existed would only hold again. A refused
    resume takes the approval back out."""
    from .broker_client import BrokerError

    job_id, dec = p["job_id"], p["decision"]
    jd = d.job_dir(job_id)
    path = jd / APPROVAL_FILE
    before = path.read_text(encoding="utf-8") if path.is_file() else None
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_approval(jd, pages=dec["pages"], by=dec["by"], attempt=dec["attempt"], at=at)
    d._chown_child(path)
    try:
        d.broker._request(p["resume"])
    except (BrokerError, OSError, ValueError) as exc:
        if before is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(before, encoding="utf-8")
        raise Refused(f"the broker refused the resume ({exc}); the approval was taken back out") from exc
    d._write_state(job_id, allowance_decision={**dec, "at": at})


def plan_decline(d: Any, job_id: str, *, by: str) -> dict[str, Any]:
    """The job ends ``failed``, debiting only the pages a unit that did not
    stop at the allowance was built from, counted once as ``daemon._report``
    counts them (a held unit read nothing it is charged for)."""
    row, st = _check_held(d, job_id, by)
    from . import verdict as verdict_mod

    outcomes = verdict_mod.read(d.job_dir(job_id), "")
    kept = max(
        (
            int(o.get("pages") or 0)
            for o in outcomes
            if isinstance(o, dict) and not (o.get("outcome") == "held" and isinstance(o.get("allowance"), dict))
        ),
        default=0,
    )
    attempt = int(st.get("attempts") or 1)
    return {
        "job_id": job_id,
        "matter": _matter_number(d, job_id),
        "record": ("failed", {"reason": DECLINED_REASON, "pages": kept if outcomes else int(row.get("pages") or 0)}),
        "decision": {"decision": "declined", "by": by.strip(), "attempt": attempt},
    }


def apply_decline(d: Any, p: dict[str, Any]) -> None:
    job_id = p["job_id"]
    state, fields = p["record"]
    d.broker.record(job_id, state, fields)
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    d._write_state(
        job_id,
        state="failed",
        reason=fields["reason"],
        finished_at=d.clock(),
        allowance_decision={**p["decision"], "at": at},
    )
    detail = (
        f"allowance hold DECLINED by {p['decision']['by']}: matter {p['matter']}; the job ended failed. "
        "The requester has NOT been told: no authored wording exists for a declined chronology, so telling "
        "her is SMD's."
    )
    d.broker._request(
        {
            "action": "delivery_proof_alert",
            "lane": "medchron",
            "job_id": job_id,
            "attempt": int(p["decision"]["attempt"]),
            "code": "limit_held",
            "detail": detail[:300],
        }
    )


def plan_credit(d: Any, *, pages: int, by: str, why: str, job_id: str | None) -> dict[str, Any]:
    """Refused, never filled in: pages not > 0, no author, no reason, or a
    job the broker does not hold."""
    if isinstance(pages, bool) or not isinstance(pages, int) or pages <= 0:
        raise Refused("--pages must be a whole number greater than 0")
    if not by.strip():
        raise Refused("--by is required: who waived the pages")
    if not why.strip():
        raise Refused("--why is required: what SMD is not charging for, and why")
    job = (job_id or "").strip() or None
    if job is not None and d.broker.status(job) is None:
        raise Refused(f"there is no chronology job {job}; nothing was credited")
    before = d.broker.allowance()
    return {
        "request": {
            "action": "medchron_allowance_credit",
            "pages": pages,
            "by": by.strip(),
            "why": why.strip(),
            **({"job_id": job} if job else {}),
        },
        "before": {k: before.get(k) for k in ("month", "allowance", "used", "remaining", "pages_credited")},
    }


def apply_credit(d: Any, p: dict[str, Any]) -> dict[str, Any]:
    from .broker_client import BrokerError

    try:
        return d.broker._request(p["request"])
    except (BrokerError, OSError, ValueError) as exc:
        raise Refused(f"the broker refused the credit ({exc}); nothing was credited") from exc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="medchron.allowance", description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a_ap = sub.add_parser("approve", help="resume the held job with an approved page overage")
    a_ap.add_argument("job_id")
    a_ap.add_argument("--by", required=True, help="who approved")
    a_ap.add_argument(
        "--pages", type=int, default=None, help="pages approved past the allowance (default: the overrun)"
    )
    a_ap.add_argument("--dry-run", action="store_true")
    d_ap = sub.add_parser("decline", help="end the held job; the requester is not told")
    d_ap.add_argument("job_id")
    d_ap.add_argument("--by", required=True, help="who declined")
    d_ap.add_argument("--dry-run", action="store_true")
    c_ap = sub.add_parser("credit", help="waive pages from the cycle page allowance (SMD's own miss)")
    c_ap.add_argument("--pages", type=int, required=True, help="pages the firm is not charged for")
    c_ap.add_argument("--by", required=True, help="who waived them")
    c_ap.add_argument("--why", required=True, help="what is not charged, and why (no patient names)")
    c_ap.add_argument("--job", default=None, help="the job the waived pages are read by (optional)")
    c_ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    from . import daemon as daemon_mod

    d = daemon_mod.build()
    if a.cmd == "credit":
        return _main_credit(d, a)
    try:
        if a.cmd == "approve":
            p = plan_approve(d, a.job_id, by=a.by, pages=a.pages)
        else:
            p = plan_decline(d, a.job_id, by=a.by)
        print(json.dumps({"dry_run": a.dry_run, **p}, indent=1, default=str))
        if a.dry_run:
            return 0
        (apply_approve if a.cmd == "approve" else apply_decline)(d, p)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if a.cmd == "approve":
        print(f"approved {p['decision']['pages']:,} pages on {a.job_id}; the daemon re-queues it on its next tick")
    else:
        print(f"declined {a.job_id}; the job ended failed and the requester has not been told")
    return 0


def credit_wake_lines(broker: Any, job_id: str, state: str, fields: dict[str, Any]) -> list[str]:
    """For the daemon's DELIVER wake: SMD's page credits on this job (written
    by ``credit``, a person's act; the runner never writes one), so the reply's
    "consumed" is the pages the firm is charged for. Credits land in the job's
    own cycle, so this figure and the cycle's "remains" come from one rule.
    Unreadable is no line: the reply then states the job's pages as it always
    did, and the allowance tool's remainder is still the credited one."""
    from .broker_client import BrokerError

    if state != "delivered" or broker is None:
        return []
    try:
        credited = int(broker.allowance(exclude_job_id=job_id).get("job_pages_credited") or 0)
    except (BrokerError, OSError, ValueError, TypeError):
        return []
    if credited <= 0:
        return []
    consumed = max(0, int(fields.get("pages") or 0) - credited)
    return [f"Pages not charged against the allowance (SMD's correction): {credited}. Consumed: {consumed} pages."]


def _main_credit(d: Any, a: argparse.Namespace) -> int:
    try:
        p = plan_credit(d, pages=a.pages, by=a.by, why=a.why, job_id=a.job)
        print(json.dumps({"dry_run": a.dry_run, **p}, indent=1, default=str))
        if a.dry_run:
            return 0
        resp = apply_credit(d, p)
    except (Refused, OSError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    after = resp.get("allowance") or {}
    print(
        f"credited {a.pages:,} pages ({(resp.get('credit') or {}).get('id')}); {after.get('month')}: "
        f"{after.get('used')} used, {after.get('remaining')} remain of {after.get('allowance')}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

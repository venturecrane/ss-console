"""What a list prints and whether it holds, for the answer key's shapes.

Runs on two trees. ``test_litigation_observe.py`` runs it here, at the seat's
default (``status_from_events`` absent). ``litigation_observe_golden.json`` is
the same run on origin/main before the switch existed, so the test compares the
code with the code it must not change. To regenerate from a tree that predates
the switch (never from this one):

    PYTHONPATH=<old>/runners/jobs:<old>/runners/jobs/tests python <old>/runners/jobs/tests/litigation_observe_shapes.py > golden

Every name, number and date is invented.
"""

from __future__ import annotations

import copy
import datetime as dt
import inspect
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from jobs.litigation import book, carry, defstatus, gates, manifest, parity, read, vocab
from jobs.litigation.run import LitigationRun
from jobs_testkit import make_pdf
from litigation_testkit import M1, LitSeat, ScriptedLit, good_result, make_inputs, make_job, matter_docs

TODAY = dt.date(2026, 10, 7)
KEY = Path(__file__).parent / "litigation_status_answer_key.json"
#: A document for every event, and a document for each value the matter needs.
DOCS = {i: f"doc-{i}" for i in range(1, 12)}
EVENT_DOC = {
    "served": 1,
    "answered": 2,
    "sent_for_service": 3,
    "appeared": 4,
    "service_rejected": 5,
    "dismissed": 6,
    "settled": 7,
    "dropped": 8,
    "default_entered": 9,
    "default_set_aside": 10,
}
PRICING = Path(__file__).parents[3] / "adapter" / "cost_telemetry" / "anthropic_pricing.json"


class Ctx:
    by_id = {v: k for k, v in DOCS.items()}

    def source(self, n: Any, doc_date: Any = None) -> dict[str, Any] | None:
        fid = DOCS.get(n)
        return {"file_id": fid, "name": f"{fid}.pdf", "doc_date": doc_date} if fid else None


def _takes_today(fn: Any) -> bool:
    return "today" in inspect.signature(fn).parameters


def to_state(raw: dict[str, Any]) -> dict[str, Any]:
    groups = list(vocab.GROUPS)
    return (
        read.to_state(Ctx(), raw, groups, TODAY) if _takes_today(read.to_state) else read.to_state(Ctx(), raw, groups)
    )  # type: ignore[reportCallIssue]


def apply(res: dict[str, Any], verdicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if _takes_today(read.apply_verdicts):
        return read.apply_verdicts(Ctx(), res, verdicts, "verify", TODAY)
    return read.apply_verdicts(Ctx(), res, verdicts, "verify")  # type: ignore[reportCallIssue]


def settle(res: dict[str, Any]) -> list[dict[str, Any]]:
    """What ``LitigationRun.final`` does to statuses, on either tree."""
    for d in res.get("defendants") or []:
        d.pop("shadow", None)
    return defstatus.settle(res, TODAY) if hasattr(defstatus, "settle") else defstatus.reconcile(res)


def answer(row: dict[str, Any], status: str) -> dict[str, Any]:
    """The read's answer for one key row: a stated status beside every event."""
    d: dict[str, Any] = {"name": "Delta Example", "status": status, "flags": list(row.get("flags") or [])}
    for f, date in (row.get("events") or {}).items():
        d[f] = {"date": date, "doc": EVENT_DOC[f]}
    for f, date in (row.get("uncited") or {}).items():
        d[f] = {"date": date, "doc": None}
    if "out_for_service" in row:
        d["out_for_service"] = {
            "value": row["out_for_service"],
            "doc": None if row.get("out_for_service_uncited") else 1,
        }
    if "role" in row:
        d["role"] = {"value": row["role"], "doc": None if row.get("role_uncited") else 1}
    return {
        "case_name": "Gamma Example v. Delta Example",
        "court": "Superior Court of Exampletown",
        "case_number": "CV-0001",
        "firm_role": "plaintiff",
        "case_status": {"value": vocab.ACTIVE, "detail": "", "doc": 11},
        "complaint_filed": {"date": "2026-03-02", "doc": 11},
        "next_court_date": {"date": "2026-11-20", "event": "CMC", "doc": 11},
        "defendants": [d],
        "discovery_propounded": [],
        "discovery_served_on_client": [],
        "matter_flags": [],
    }


def _on_list(m: dict[str, Any]) -> dict[str, Any]:
    return {**m, "matter_id": "m1", "number": "100001", "title": "Gamma v. Delta", "responsible": "Alpha Example"}


def outcome(prior: dict[str, Any] | None, raw: dict[str, Any], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """The list's row, its gate findings and its parity holds for one read."""
    res = read.merge(prior, to_state(raw), list(vocab.GROUPS))
    log = apply(res, verdicts)
    files = set(DOCS.values())
    carry.carry_forward(prior, res, moved=set(), on_matter=files, saved={}, today=TODAY)
    log = [*log, *settle(res)]
    m = _on_list(res)
    issues = gates.run(
        [m], today=TODAY, files={"m1": files}, saved={}, text_of=lambda _m, _f: "", hits={}, discovery=True
    )
    changes = parity.compare(prior, m, moved_files=set(), overturns=log, today=TODAY)
    rows = [[str(c) for c in r["cells"]] for r in book.defendant_rows([m])]
    return {
        "printed": rows,
        "gates": json.loads(json.dumps(issues["issues"], default=str)),
        "unexplained": [c["path"] for c in parity.unexplained(changes)],
    }


_OVERTURN = {
    "path": "defendants[Delta Example].status",
    "verdict": "overturned",
    "corrected": vocab.D_SETTLED,
    "doc": 7,
}


def _variants(row: dict[str, Any]) -> dict[str, Any]:
    prior = _on_list(to_state(answer({"events": {"served": "2026-04-10"}}, vocab.SERVED_NO_ANSWER)))
    return {
        "stated as keyed": lambda: outcome(None, answer(row, row["status"]), []),
        "stated otherwise": lambda: outcome(None, answer(row, vocab.SERVED_NO_ANSWER), []),
        "over a stored list": lambda: outcome(copy.deepcopy(prior), answer(row, vocab.NOT_SERVED), []),
        "a check overturns the status": lambda: outcome(None, answer(row, vocab.NOT_SERVED), [dict(_OVERTURN)]),
    }


def shapes() -> dict[str, Any]:
    rows = json.loads(KEY.read_text(encoding="utf-8"))["rows"]
    return {f"{row['id']} | {kind}": fn() for row in rows for kind, fn in _variants(row).items()}


def shapes_one(name: str) -> dict[str, Any]:
    """One shape, for a test that names it."""
    rid, kind = name.rsplit(" | ", 1)
    rows = json.loads(KEY.read_text(encoding="utf-8"))["rows"]
    return _variants(next(r for r in rows if r["id"] == rid))[kind]()


# ---- whole runs ------------------------------------------------------------------------------
def _run(root: Path, client: ScriptedLit, seat: LitSeat, job: str, job_id: str) -> LitigationRun:
    return LitigationRun(
        make_job(root / job, job_id=job_id),
        inputs_dir=str(make_inputs(root / "inputs")),
        pricing=str(PRICING),
        state_dir=root / "state",
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
        today=lambda: TODAY,
        readback_pause=0.0,
    )


def _measure(r: LitigationRun) -> dict[str, Any]:
    v = r.run()
    out: dict[str, Any] = {"verdict": v.verdict, "reason": v.reason}
    for name in ("final.json", "gates.json", "changes.json"):
        p = r.data / name
        out[name] = json.loads(p.read_text()) if p.is_file() else None
    return out


def _with_events(n: dict[str, int]) -> dict[str, Any]:
    raw = good_result(n)
    d = raw["defendants"][0]
    d["appeared"] = {"date": "2026-04-20", "doc": n["f-a1"]}
    d["default_entered"] = {"date": "2026-05-01", "doc": n["f-p1"]}
    d["role"] = {"value": "firm_client", "doc": n["f-a1"]}
    return raw


def _noted(n: dict[str, int]) -> dict[str, Any]:
    raw = good_result(n)
    d = raw["defendants"][0]
    d.update(status=vocab.SERVED_NO_ANSWER, flags=["Default entered 2026-06-01."], answered={"date": None, "doc": None})
    return raw


def _arrive(seat: LitSeat) -> None:
    blob = make_pdf(["PROOF OF SERVICE on a different party, 09/29/2026"])
    seat.blobs["f-p2"] = blob
    seat.per[M1].append(
        {
            "id": "f-p2",
            "name": "POS Zeta",
            "ext": ".pdf",
            "size": len(blob),
            "created": "2026-09-30T00:00:00",
            "modified": "2026-09-30T00:00:00",
            "folderId": None,
            "deleted": False,
        }
    )


def _drift(values: dict[str, Any]) -> dict[str, Any]:
    values["complaint_filed"]["date"] = "2026-03-01"
    values["defendants"][0]["status"] = vocab.D_DISMISSED  # main lets a read's status stand on an update
    values["defendants"][0]["dismissed"] = {"date": "2026-09-29", "doc": 1}
    return values


def scenarios() -> dict[str, Any]:
    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "a"
        seat = LitSeat({M1: matter_docs()})
        out["a first read, with events beside its statuses"] = _measure(
            _run(root, ScriptedLit(result_fn=_with_events), seat, "j1", "01KTJ0BX0000000000000000AA")
        )
        _arrive(seat)
        out["an update whose read moves a status"] = _measure(
            _run(root, ScriptedLit(update_fn=_drift), seat, "j2", "01KTJ0BX0000000000000000BB")
        )
        out["what the lane stored"] = manifest.load_prior(root / "state", M1)
        root = Path(d) / "b"
        out["a note states a default the status does not"] = _measure(
            _run(root, ScriptedLit(result_fn=_noted), LitSeat({M1: matter_docs()}), "j1", "01KTJ0BX0000000000000000AA")
        )
        root = Path(d) / "c"
        bad = {
            "path": "defendants[Delta Example].answered",
            "verdict": "overturned",
            "corrected": {"date": "2027-01-01"},
        }
        out["a check writes a future answer date and the gates hold"] = _measure(
            _run(root, ScriptedLit(verdicts=[bad]), LitSeat({M1: matter_docs()}), "j1", "01KTJ0BX0000000000000000AA")
        )
    return json.loads(json.dumps(out, default=str))


def main() -> None:
    import jobs

    print(f"tree: {jobs.__file__}", file=sys.stderr)
    print(json.dumps({"shapes": shapes(), "runs": scenarios()}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()

"""The status derivation against an answer key, at no cost and with no seat.

Two keys, one purpose: what a firm should read, written before the code is
trusted, and every difference named.

* **A rule key** (``tests/litigation_status_answer_key.json``): defendant event
  sets, each with the status it should give. ``check`` runs ``defstatus.decide``
  over it. Synthetic, committed.
* **A list key** for a rehearsal on real inputs: ``{"<matter id>#<defendant
  index>": "<status>"}``, the statuses of the last delivered list, written from
  the lane's stored state BEFORE the rehearsal (``from_state``). ``compare`` reads
  it against the report ``jobs litigate --rehearse`` wrote (its
  ``status_derivation`` section, ``rehearse_report``). Client data: it stays
  where the state dir is.

    python -m jobs.litigation.answerkey rules <key.json>
    python -m jobs.litigation.answerkey from-state <state dir> > key.json
    python -m jobs.litigation.answerkey compare <report.json> <key.json>
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from . import defstatus, vocab


#: Why a stored status and its events differ (``classify``): the event was never
#: on file, which is every status a read asserted before events existed.
NOT_ON_FILE = "event_not_on_file"
DISAGREES = "rule_disagrees"
SAME = "same"


def classify(d: dict[str, Any], today: dt.date) -> dict[str, str]:
    """What a defendant's events alone give, beside what it prints. A stored
    status that outranks them has its event missing, as every report read
    before events existed does; a next read records it. Anything else is the
    rule and the stored status disagreeing, which is what a person reads."""
    decision = defstatus.decide(d, today, d.get("stored_status"))
    printed = d.get("status")
    if decision.status == printed:
        kind = SAME
    elif (
        defstatus.asserted(d)
        and decision.status != vocab.D_UNCLEAR
        and defstatus.rank(printed) < defstatus.rank(decision.status)
    ):
        kind = NOT_ON_FILE
    else:
        kind = DISAGREES
    return {"from_events": decision.status, "rule": decision.rule, "class": kind}


def defendant(row: dict[str, Any]) -> dict[str, Any]:
    """The defendant a rule-key row describes."""

    def doc(name: str) -> dict[str, Any]:
        return {"file_id": f"doc-{name}", "name": f"{name}.pdf", "doc_date": None}

    d: dict[str, Any] = {"name": "Defendant Example", "flags": list(row.get("flags") or [])}
    for f, date in (row.get("events") or {}).items():
        d[f] = {"date": date, "source": doc(f)}
    for f, date in (row.get("uncited") or {}).items():
        d[f] = {"date": date, "source": None}
    if "out_for_service" in row:
        cited = not row.get("out_for_service_uncited")
        d["out_for_service"] = {"value": row["out_for_service"], "source": doc("server") if cited else None}
    if "role" in row:
        d["role"] = {"value": row["role"], "source": None if row.get("role_uncited") else doc("role")}
    if row.get("marked_unclear"):
        d["status_unclear"] = row["marked_unclear"]
    return d


def check(key: dict[str, Any]) -> list[dict[str, Any]]:
    """One entry per rule-key row whose status or deciding rule differs."""
    today = dt.date.fromisoformat(key["today"])
    out = []
    for row in key["rows"]:
        got = defstatus.decide(defendant(row), today, row.get("stored_status"))
        if (got.status, got.rule) != (row["status"], row["rule"]):
            out.append({"id": row["id"], "want": [row["status"], row["rule"]], "got": [got.status, got.rule]})
    return out


def from_state(state: Path) -> dict[str, str]:
    """The list key for a lane's stored state: what the firm was last told."""
    out: dict[str, str] = {}
    for p in sorted((Path(state) / "matters").glob("*.json")):
        m = json.loads(p.read_text(encoding="utf-8"))
        for i, d in enumerate(m.get("defendants") or []):
            out[f"{p.stem}#{i}"] = str(d.get("status"))
    return out


def compare(report: dict[str, Any], key: dict[str, str]) -> dict[str, Any]:
    """Every difference between a list key and a rehearsal's report, named. A
    line that prints differently is the finding: a stored report must print as
    it did. ``from_events`` differences are expected to be mostly the class
    ``event_not_on_file`` and are for reading, never a failure alone."""
    rows = (report.get("status_derivation") or {}).get("rows") or []
    seen = {f"{r['matter_id']}#{r['index']}": r for r in rows}
    printed = [
        {"defendant": k, "key": v, "printed": seen[k]["printed"]}
        for k, v in key.items()
        if k in seen and seen[k]["printed"] != v
    ]
    return {
        "printed_differs": printed,
        "missing_from_report": sorted(k for k in key if k not in seen),
        "not_in_key": sorted(k for k in seen if k not in key),
        "from_events_differs": [r for r in rows if r["class"] != "same"],
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if len(args) == 2 and args[0] == "rules":
        bad = check(json.loads(Path(args[1]).read_text(encoding="utf-8")))
        print(json.dumps(bad, indent=1))
        return 1 if bad else 0
    if len(args) == 2 and args[0] == "from-state":
        print(json.dumps(from_state(Path(args[1])), indent=1))
        return 0
    if len(args) == 3 and args[0] == "compare":
        report = json.loads(Path(args[1]).read_text(encoding="utf-8"))
        got = compare(report, json.loads(Path(args[2]).read_text(encoding="utf-8")))
        print(json.dumps(got, indent=1))
        return 1 if got["printed_differs"] or got["missing_from_report"] else 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

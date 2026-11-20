"""``deliver_by_smd --set``: a served set SMD owed on a delivered response job.

A response job delivers the sets the Operator drafted and tells the attorney
the others are "being prepared by us" (``served.py``). SMD drafts and files
each by hand and records it here, on the same job:

* ``plan_set`` / ``apply_set``: the proof row first (every file of the job,
  this set's added), then the delivered record (its files and its served sets,
  this one filed, ``told: false``, ``recorded_at``), then the job's own copies,
  then a page for whatever is still owed. A rerun after a write that stopped
  part way finishes it: when the ledger already says the set is filed by these
  very files, only the local writes are done again.
* ``plan_told`` / ``apply_told``: after the attorney was told (the SMD note in
  his thread), the set is marked ``told: true``. Until then the set stays
  listed by work health ("tell the attorney: <set> filed as <name>"), unless a
  reply in his request's thread went after it was recorded.

Root only, on the seat, like ``deliver_by_smd`` itself.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .. import hand_delivery, spend
from ..hand_delivery import Refused
from ..proof import watch
from . import served as served_mod

#: The cumulative read-back of a response job's filed files, each set SMD
#: recorded included: what the next record's proof row extends.
PROOF_FILES = "served-proof.json"


def _stored(row: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(row.get("delivery_json") or "{}") or {}
    except ValueError:
        return {}


def filed_proof(jd: Any) -> list[dict[str, Any]] | None:
    """The delivered job's files as last proven, {file_id, size, sha256} in
    manifest order: this tool's own record of them, else the runner's verdict."""

    def verdict_files(doc: Any) -> Any:
        first = doc[0] if isinstance(doc, list) else doc
        return (first.get("proof") or {}).get("files")

    for p, pick in (
        (Path(jd) / "data" / PROOF_FILES, lambda doc: doc.get("files")),
        (Path(jd) / "verdict.json", verdict_files),
    ):
        try:
            files = pick(json.loads(p.read_text(encoding="utf-8")))
            if isinstance(files, list) and files:
                return [{"file_id": f["file_id"], "size": f["size"], "sha256": f["sha256"]} for f in files]
        except (OSError, ValueError, AttributeError, IndexError, KeyError, TypeError):
            continue
    return None


def _delivered(d: Any, job_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The delivered row and its served sets, the ledger's own (else the job's copy)."""
    row = d.broker.status(job_id)
    if row is None or row.get("state") != "delivered":
        raise Refused(f"job {job_id} is {(row or {}).get('state')}; --set records a set owed on a delivered job")
    if (d.queue / f"{job_id}.json").exists():
        raise Refused(f"job {job_id} is queued; nothing was written")
    rows = _stored(row).get("served_sets")
    if not isinstance(rows, list) or not rows:
        try:
            rows = json.loads((d.job_dir(job_id) / "data" / served_mod.OUTCOMES).read_text(encoding="utf-8"))["sets"]
        except (OSError, ValueError, KeyError) as exc:
            raise Refused(f"job {job_id} has no served sets on record ({exc}); nothing was written") from exc
    return row, rows


def _hit(job_id: str, rows: list[dict[str, Any]], set_label: str) -> dict[str, Any]:
    hit = [r for r in rows if str(r.get("set") or "") == set_label]
    if not hit:
        raise Refused(f"job {job_id} has no served set {set_label!r}; its sets: {[r.get('set') for r in rows]}")
    return hit[0]


def plan_set(d: Any, job_id: str, rec: dict[str, Any], set_label: str, *, quiet: bool) -> dict[str, Any]:
    """Every check first; nothing is written here. Raises Refused."""
    from .deliver_by_smd import parse_record

    facts = parse_record(rec)
    row, rows = _delivered(d, job_id)
    hit = _hit(job_id, rows, set_label)
    if any(f["role"] != "draft" for f in facts["files"]):
        raise Refused("--set records the set's own drafted documents only (role draft)")
    jd = d.job_dir(job_id)
    names = [f["name"] for f in facts["files"]]
    document = " and ".join(names)
    base = {"job_id": job_id, "set": set_label, "reply": None if quiet else served_mod.added_line(set_label, names)}
    if hit.get("status") == served_mod.FILED:
        local = _local_rows(jd)
        if hit.get("document") == document and _hit(job_id, local, set_label).get("status") != served_mod.FILED:
            # The ledger took this record and the job's own copy did not: finish it.
            return {**base, "finish": True, "served_sets": rows, "still_owed": owed(rows)}
        raise Refused(f"{set_label} is already filed on job {job_id}; nothing was written")
    stored = list(_stored(row).get("files") or [])
    proven = filed_proof(jd)
    if proven is None or len(proven) != len(stored):
        raise Refused(f"job {job_id}'s filed files cannot be paired with their proof; nothing was written")
    taken = {f["file_id"] for f in proven}
    again = [f["file_id"] for f in facts["files"] if f["file_id"] in taken]
    if again:
        raise Refused(f"file id {again[0]} is already filed on job {job_id}; nothing was written")
    at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    filed = {**hit, "status": served_mod.FILED, "document": document, "told": False, "recorded_at": at}
    new_rows = [filed if r is hit else r for r in rows]
    files = [*stored, *({"name": f["name"], "size": f["bytes"], "role": f["role"]} for f in facts["files"])]
    proof = hand_delivery.proof_request(d, job_id, [], review="released", attempt=watch._attempt(d, job_id))
    proof["files"] = [*proven, *({"file_id": f["file_id"], "size": f["bytes"], "sha256": f["sha256"]} for f in facts["files"])]  # fmt: skip
    cents = spend.recorded_cents(jd, d.LANE, int(row.get("cents") or 0))
    return {
        **base,
        "from_state": "delivered",
        "proof_first": True,
        "records": [("delivered", {"cents": cents, "delivery": {"files": files, "served_sets": new_rows}})],
        "proof": proof,
        "state": {"state": "delivered", "delivered_by": "smd"},
        "served_sets": new_rows,
        "still_owed": owed(new_rows),
    }


def _local_rows(jd: Any) -> list[dict[str, Any]]:
    try:
        return json.loads((Path(jd) / "data" / served_mod.OUTCOMES).read_text(encoding="utf-8"))["sets"]
    except (OSError, ValueError, KeyError):
        return []


def owed(rows: list[dict[str, Any]]) -> list[str]:
    """Sets still being prepared, and SMD-filed sets the attorney has not been told of."""
    from ..job_notes import owed_sets

    return owed_sets(rows)


def apply_set(d: Any, p: dict[str, Any]) -> None:
    """``plan_set``'s writes, in order; a page for whatever is still owed last."""
    from .. import job_notes

    data = Path(d.job_dir(p["job_id"])) / "data"
    if not p.get("finish"):
        hand_delivery.apply(d, p)
        (data / PROOF_FILES).write_text(json.dumps({"files": p["proof"]["files"]}, indent=1), encoding="utf-8")
    served_mod.write_outcomes(data, p["served_sets"])
    job_notes.tell_owed(d, p["job_id"], p["served_sets"])


def plan_told(d: Any, job_id: str, set_label: str) -> dict[str, Any]:
    """``--told``: the attorney was told an SMD-filed set is filed."""
    row, rows = _delivered(d, job_id)
    hit = _hit(job_id, rows, set_label)
    if hit.get("status") != served_mod.FILED:
        raise Refused(f"{set_label} is not filed yet on job {job_id}; record it with --set first")
    if hit.get("told") is True:
        raise Refused(f"{set_label} on job {job_id} is already marked told; nothing was written")
    new_rows = [{**r, "told": True} if r is hit else r for r in rows]
    cents = spend.recorded_cents(d.job_dir(job_id), d.LANE, int(row.get("cents") or 0))
    return {
        "job_id": job_id,
        "set": set_label,
        "records": [("delivered", {"cents": cents, "delivery": {"served_sets": new_rows}})],
        "served_sets": new_rows,
        "still_owed": owed(new_rows),
    }


def apply_told(d: Any, p: dict[str, Any]) -> None:
    for state, fields in p["records"]:
        d.broker.record(p["job_id"], state, fields)
    served_mod.write_outcomes(Path(d.job_dir(p["job_id"])) / "data", p["served_sets"])


__all__ = ["PROOF_FILES", "apply_set", "apply_told", "filed_proof", "owed", "plan_set", "plan_told"]

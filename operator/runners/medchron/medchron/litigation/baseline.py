"""The seed: two hand-run result shapes converted into the lane's prior state.

The first runs were done by hand on 2026-10-07 in two shapes:

* **firm** (three passes): per-chunk ``final.json`` lists with ``matter_id``,
  case-level values, defendants with ``status``, ``case_status`` as a string;
  sources are free text that usually names the file, sometimes with the first
  eight characters of its id.
* **two-pass** (one attorney's files, two passes): flat ``merged.json`` rows,
  one per defendant, keyed by matter number (``num2id.json`` maps it), dates
  written inside text, no case status.

What the converter will NOT do is invent a citation. A source string resolves
to a file only by its id prefix or by a file name of that matter appearing in
it; an unresolved source is left ``None``. A field group is marked read only
when every value it carries resolved, so a group the seed cannot cite is read
again on the first seat run instead of passing the gates on a guess. Discovery
is in neither shape; the 2026-10-08 discovery run is merged on afterwards
(``discovery`` mode), and a matter it cannot cite reads the group again. Two-pass matters carry
``provenance.two_pass`` as a record; the runner does not re-audit them.

The seed's manifest records which files existed (``""`` as the modified date:
the hand inventories carried dates, not the vendor's ``dateModified``), so a
file added after the seed is new and a seeded file is not "changed".

Client data: this module reads it from a path given on the command line and
writes the state dir it is given. No seed is ever committed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path
from typing import Any

from . import vocab
from .manifest import _atomic

_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_US = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")
_HEX8 = re.compile(r"\b([0-9a-f]{8})\b")
CASE_MAP = {s.lower(): s for s in vocab.CASE_STATUSES}
DEF_PREFIX = [
    ("answered original", vocab.ANSWERED_ORIGINAL),
    ("answered", vocab.ANSWERED),
    ("not served", vocab.NOT_SERVED),
    ("service rejected", vocab.SERVICE_REJECTED),
    ("out for service", vocab.OUT_FOR_SERVICE),
    ("served, appeared", vocab.APPEARED_NO_ANSWER),
    ("appeared", vocab.APPEARED_NO_ANSWER),
    ("served", vocab.SERVED_NO_ANSWER),
    ("default", vocab.DEFAULT),
    ("dismissed", vocab.D_DISMISSED),
    ("settled", vocab.D_SETTLED),
    ("dropped", vocab.DROPPED),
    ("uim", vocab.UIM),
]


def first_date(text: Any) -> str | None:
    s = str(text or "")
    m = _ISO.search(s)
    if m:
        return m.group(0)
    m = _US.search(s)
    if m:
        mo, d, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
        try:
            return dt.date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def def_status(raw: Any) -> str:
    s = str(raw or "").strip().lower()
    return next((v for p, v in DEF_PREFIX if s.startswith(p)), vocab.D_UNCLEAR)


class Resolver:
    """Source text -> ``{file_id, name, doc_date}`` against one matter's files."""

    def __init__(self, files: list[dict[str, Any]]) -> None:
        self.files = files
        self.by_prefix: dict[str, dict[str, Any]] = {}
        for f in files:
            self.by_prefix.setdefault(str(f["id"])[:8].lower(), f)

    def __call__(self, text: Any) -> dict[str, Any] | None:
        s = str(text or "")
        if not s.strip():
            return None
        for m in _HEX8.finditer(s.lower()):
            f = self.by_prefix.get(m.group(1))
            if f:
                return self._src(f, s)
        low = s.lower()
        best = None
        for f in self.files:
            for alias in f.get("names") or [f.get("name")]:
                a = str(alias or "").strip().lower()
                stem = a.rsplit(".", 1)[0] if "." in a[-6:] else a
                if len(stem) >= 6 and stem in low and (best is None or len(stem) > best[0]):
                    best = (len(stem), f)
        return self._src(best[1], s) if best else None

    @staticmethod
    def _src(f: dict[str, Any], text: str) -> dict[str, Any]:
        return {"file_id": str(f["id"]), "name": str(f.get("name") or ""), "doc_date": first_date(text)}


def _v(date: Any, src: Any, resolve: Resolver, **extra: Any) -> dict[str, Any]:
    return {"date": first_date(date) if date else None, **extra, "source": resolve(src) if date or src else None}


def _groups(m: dict[str, Any]) -> list[str]:
    def cited(v: Any) -> bool:
        return not (isinstance(v, dict) and v.get("date") and not v.get("source"))

    out = []
    cs = m.get("case_status") or {}
    if (
        cs.get("value")
        and (cs.get("source") or cs.get("value") == vocab.UNCLEAR)
        and cited(m.get("complaint_filed"))
        and cited(m.get("next_court_date"))
    ):
        out.append(vocab.GROUP_CASE)
    ds = m.get("defendants") or []
    if ds and all(cited(d.get("served")) and cited(d.get("answered")) for d in ds):
        out.append(vocab.GROUP_DEFENDANTS)
    return out


def from_firm(row: dict[str, Any], files: list[dict[str, Any]]) -> dict[str, Any]:
    """One three-pass matter (firm shape) in the state schema."""
    r = Resolver(files)
    cf, nc = row.get("complaint_filed") or {}, row.get("next_cmc") or {}
    status = CASE_MAP.get(str(row.get("case_status") or "").split(" (")[0].strip().lower(), vocab.UNCLEAR)
    cs_src = r((row.get("settled") or {}).get("source")) or r(row.get("case_status_detail")) or r(cf.get("source"))
    defs = []
    for d in row.get("defendants") or []:
        sv, an = d.get("served") or {}, d.get("answered") or {}
        defs.append(
            {
                "name": str(d.get("name") or ""),
                "status": def_status(d.get("status")),
                "out_for_service": {
                    "value": (d.get("out_for_service") or {}).get("value"),
                    "source": r((d.get("out_for_service") or {}).get("source")),
                },
                "served": _v(sv.get("date"), sv.get("source"), r, method=sv.get("method")),
                "answered": _v(an.get("date"), an.get("source"), r),
                "flags": [str(x) for x in d.get("flags") or []],
            }
        )
    m = {
        "case_name": row.get("case"),
        "court": row.get("court"),
        "case_number": row.get("case_number"),
        "firm_role": "defense"
        if "represents" in str(row.get("notes") or "").lower() and "defendant" in str(row.get("notes") or "").lower()
        else "plaintiff",
        "case_status": {"value": status, "detail": row.get("case_status_detail"), "source": cs_src},
        "complaint_filed": _v(cf.get("date"), cf.get("source"), r),
        "next_court_date": _v(nc.get("date"), nc.get("source"), r, event="next CMC / hearing"),
        "defendants": defs,
        "matter_flags": [str(x) for x in row.get("matter_flags") or []],
        "notes": str(row.get("notes") or ""),
    }
    m["fields_read"] = _groups(m)
    m["provenance"] = {"seed": "firm", "two_pass": False, "passes": 3}
    return m


def from_two_pass(rows: list[dict[str, Any]], files: list[dict[str, Any]]) -> dict[str, Any]:
    """One matter's flat defendant rows (two-pass shape) in the state schema."""
    r = Resolver(files)
    head = rows[0]
    defs = []
    for x in rows:
        served = first_date(x.get("served"))
        method = re.sub(_ISO, "", str(x.get("served") or "")).strip(" ,;") if served else None
        defs.append(
            {
                "name": str(x.get("defendant") or ""),
                "status": def_status(x.get("status")),
                "out_for_service": {"value": None, "source": None},
                "served": {
                    "date": served,
                    "method": method or None,
                    "source": r(x.get("served_src")) if served else None,
                },
                "answered": {
                    "date": first_date(x.get("answered")),
                    "source": r(x.get("answered_src")) if first_date(x.get("answered")) else None,
                },
                "flags": [f for f in str(x.get("flags") or "").split(" | ") if f.strip()],
            }
        )
    m = {
        "case_name": head.get("case"),
        "court": head.get("court"),
        "case_number": head.get("case_number"),
        "firm_role": "plaintiff",
        "complaint_filed": {"date": first_date(head.get("filed")), "source": None},
        "next_court_date": {"date": first_date(head.get("cmc")), "event": "next CMC / hearing", "source": None},
        "defendants": defs,
        "matter_flags": [],
        "notes": str(head.get("note") or ""),
    }
    m["fields_read"] = _groups(m)
    m["provenance"] = {"seed": "two_pass", "two_pass": True, "passes": 2}
    return m


def write_state(
    state: Path, matters: dict[str, dict[str, Any]], inventories: dict[str, list[dict[str, Any]]], source: str
) -> dict[str, Any]:
    for mid, m in matters.items():
        if (state / "matters" / f"{mid}.json").is_file():
            continue  # never overwrite runner state
        _atomic(state / "matters" / f"{mid}.json", m)
        _atomic(
            state / "manifest" / f"{mid}.json",
            {str(f["id"]): "" for f in inventories.get(mid, []) if not f.get("deleted")},
        )
    prov = {"source": source, "matters": len(matters), "seeded_at": dt.date.today().isoformat()}
    if not (state / "baseline.json").is_file():
        _atomic(state / "baseline.json", prov)
    return prov


# ---- the discovery seed: the 2026-10-08 discovery run, merged onto the state --------
_SET_TYPES = {
    "form_interrogatories": "Form Interrogatories",
    "special_interrogatories": "Special Interrogatories",
    "requests_for_production": "Requests for Production",
    "requests_for_admission": "Requests for Admission",
    "demand_for_inspection": "Demand for Inspection",
    "deposition_notice": "Deposition notice",
    "subpoena": "Subpoena",
    "other": "Other discovery",
}
_RESPONSES = {
    "responses_served": "yes",
    "no_responses_found": "no",
    "only_unsigned_or_draft_responses": "no",
}


def set_name(s: dict[str, Any]) -> str:
    num = str(s.get("set_number") or "").strip()
    kind = _SET_TYPES.get(str(s.get("set_type") or ""), "Other discovery")
    if num and not num.lower().startswith("set"):
        num = "Set " + num
    return f"{kind}, {num}" if num else kind


def _doc(docs: list[dict[str, Any]], n: Any) -> dict[str, Any] | None:
    """The discovery run cites ``[DOC n]`` into that matter's ordered snippet
    list; resolve it to the file, or None. Never a guess."""
    if not isinstance(n, int) or not 0 < n <= len(docs):
        return None
    d = docs[n - 1]
    return {"file_id": str(d["fid"]), "name": str(d.get("name") or ""), "doc_date": d.get("date")}


def from_discovery(sets: list[dict[str, Any]], docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    """One matter's discovery-run sets in the state schema, or None when a set
    carries a date it cannot cite (the group is then read again, not trusted)."""
    prop, theirs = [], []
    for s in sets:
        d = s.get("direction")
        if d not in ("propounded_by_our_client", "served_on_our_client"):
            continue
        date = first_date(s.get("served_date"))
        src = _doc(docs, s.get("served_source_doc"))
        if date and not src:
            return None
        if d == "propounded_by_our_client":
            prop.append(
                {"set": set_name(s), "served_on": str(s.get("responding_party") or ""), "date": date, "source": src}
            )
            continue
        rdate = first_date(s.get("response_date"))
        rsrc = _doc(docs, s.get("response_source_doc"))
        if rdate and not rsrc:
            return None
        value = _RESPONSES.get(str(s.get("response_status") or ""), "unclear")
        theirs.append(
            {
                "set": set_name(s),
                "served_by": str(s.get("propounding_party") or ""),
                "date": date,
                "source": src,
                "responses_served": {"value": value, "date": rdate, "source": rsrc},
            }
        )
    return {"discovery_propounded": prop, "discovery_served_on_client": theirs}


def merge_discovery(state: Path, root: Path) -> dict[str, int]:
    """Merge the discovery run under ``root`` (``runs/merged.json``,
    ``files.json``, ``snip/<number>.json``) onto matters already seeded in
    ``state``, marking the group read. A matter the runner already read is
    left alone, as is one whose discovery cannot be fully cited."""
    merged = json.loads((root / "runs" / "merged.json").read_text(encoding="utf-8"))
    files = json.loads((root / "files.json").read_text(encoding="utf-8"))
    out = {"merged": 0, "uncited": 0, "not_seeded": 0, "already_read": 0}
    for num, v in merged.items():
        mid = str((files.get(num) or {}).get("id") or "")
        path = state / "matters" / f"{mid}.json"
        if not mid or not path.is_file():
            out["not_seeded"] += 1
            continue
        m = json.loads(path.read_text(encoding="utf-8"))
        if vocab.GROUP_DISCOVERY in (m.get("fields_read") or []):
            out["already_read"] += 1
            continue
        docs = json.loads((root / "snip" / f"{num}.json").read_text(encoding="utf-8"))
        disc = from_discovery(v.get("sets") or [], docs)
        if disc is None:
            out["uncited"] += 1
            continue
        m.update(disc)
        m["fields_read"] = sorted(set(m.get("fields_read") or []) | {vocab.GROUP_DISCOVERY})
        m.setdefault("provenance", {})["discovery_seed"] = "2026-10-08"
        _atomic(path, m)
        out["merged"] += 1
    return out


# ---- the laptop CLI: read the hand-run directories, write a state dir -----------------
def _tsv_files(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        cols = line.split("\t")
        if cols and re.fullmatch(r"[0-9a-fA-F-]{36}", cols[0]):
            out.append({"id": cols[0], "name": cols[4] if len(cols) > 4 else "", "names": cols[4:]})
    return out


def load_firm(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    matters, invs = {}, {}
    # The chunk lists carry every matter's id; a final row may not.
    num2id = {
        str(c["num"]): str(c["id"])
        for ch in sorted(root.glob("chunk*.json"))
        for c in json.loads(ch.read_text(encoding="utf-8"))
    }
    for final in sorted(root.glob("c*/final.json")):
        for row in json.loads(final.read_text(encoding="utf-8")):
            mid = str(row.get("matter_id") or num2id.get(str(row.get("num"))) or "")
            if not mid:
                continue
            tsv = final.parent / f"names_{mid[:8]}.tsv"
            invs[mid] = _tsv_files(tsv) if tsv.is_file() else []
            matters[mid] = from_firm(row, invs[mid])
    return matters, invs


def load_two_pass(root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = json.loads((root / "merged.json").read_text(encoding="utf-8"))["rows"]
    num2id = json.loads((root / "num2id.json").read_text(encoding="utf-8"))
    inv_all = json.loads((root / "inv_all.json").read_text(encoding="utf-8"))
    by: dict[str, list[dict[str, Any]]] = {}
    for x in rows:
        by.setdefault(str(x.get("num")), []).append(x)
    matters, invs = {}, {}
    for num, rs in by.items():
        mid = num2id.get(num)
        if not mid:
            continue
        invs[mid] = [
            {
                "id": f[0],
                "name": f[1] + (f[2] if f[2] and not f[1].lower().endswith(f[2].lower()) else ""),
                "deleted": bool(f[5]),
            }
            for f in inv_all.get(mid, [])
        ]
        matters[mid] = from_two_pass(rs, invs[mid])
    return matters, invs


def main(argv: list[str] | None = None) -> int:
    """``python -m medchron.litigation.baseline firm|two_pass|discovery <hand-run dir> <state dir>``."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) == 3 and args[0] == "discovery":
        print(json.dumps(merge_discovery(Path(args[2]), Path(args[1]))))
        return 0
    if len(args) != 3 or args[0] not in ("firm", "two_pass"):
        print(
            "usage: python -m medchron.litigation.baseline firm|two_pass|discovery <hand-run dir> <state dir>",
            file=sys.stderr,
        )
        return 2
    matters, invs = (load_firm if args[0] == "firm" else load_two_pass)(Path(args[1]))
    prov = write_state(Path(args[2]), matters, invs, args[0])
    groups = {g: sum(1 for m in matters.values() if g in m["fields_read"]) for g in vocab.GROUPS}
    print(json.dumps({**prov, "groups_seeded": groups}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

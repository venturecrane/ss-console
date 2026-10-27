"""Synthetic fixtures for the case file job: a firm-inputs dir in
``casefile-firm.yaml``'s schema (with generic prompts), an envelope, a seat that
serves matters, staff, tasks, events, memos, layouts and files, and a scripted
model that answers the read with ``record_review`` and the audit with
``record_verdicts``.

Every name, number and date here is invented (Example / Exampletown). Nothing
is a firm's, a client's, or a matter's.
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from medchron_testkit import make_pdf

JOB = "01KTJ0BX0000000000000000CF"
LIBRARY = "99999999-9999-4999-8999-999999999999"
M1 = "11111111-1111-4111-8111-111111111111"
M2 = "22222222-2222-4222-8222-222222222222"
STAFF_P = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"  # the person assisting (the caseload)
STAFF_R = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"  # the responsible attorney
STAFF_X = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"  # the approver
CLIENT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
TASK_OPEN = "e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1"
TASK_MONEY = "e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2"
TASK_DONE = "e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3"
TASK2 = "e4e4e4e4-e4e4-4e4e-8e4e-e4e4e4e4e4e4"
DOC_REPORT = "f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1"
DOC_BILL = "f2f2f2f2-f2f2-4f2f-8f2f-f2f2f2f2f2f2"
DOC_SCAN = "f3f3f3f3-f3f3-4f3f-8f3f-f3f3f3f3f3f3"
DOC_M2 = "f4f4f4f4-f4f4-4f4f-8f4f-f4f4f4f4f4f4"
MED_ITEM = "a0a0a0a0-a0a0-4a0a-8a0a-a0a0a0a0a0a0"
FOLDER = "0f0f0f0f-0f0f-40f0-80f0-0f0f0f0f0f0f"
DESIGN = "PersonalInjurySettlementDetailsItem"

CONTRACT = "Reader contract (synthetic). Record what the file shows; a gap is a question."
READER = "Read the pack, then call record_review."
AUDITOR = "Try to refute each closure; answer with record_verdicts."


def firm_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "firm": {"slug": "example", "display_name": "Example Law Office"},
        "models": {"read": "claude-sonnet-5", "audit": "claude-sonnet-5"},
        "per_job_cap_usd": 20.0,
        "monthly_budget_usd": 100.0,
        "at_stake_terms": ["lien", "settlement", "court", "statute"],
        "statute_reminder_patterns": [r"1 week before statute runs"],
        "file_status_folder": "File Status",
        "pack": {"bill_patterns": [r"bill|statement|ledger"], "correspondence_patterns": [r"report|letter"]},
        "rank": {"court_event_patterns": [r"hearing|trial"], "gov_claim_patterns": [r"government claim"]},
        "read_workers": 1,
        "tool_iterations": 6,
        "timezone": "America/Los_Angeles",
    }
    data.update(over)
    return data


def make_inputs(root: Path, **over: Any) -> Path:
    (root / "prompts").mkdir(parents=True, exist_ok=True)
    (root / "casefile-firm.yaml").write_text(yaml.safe_dump(firm_data(**over), sort_keys=False), encoding="utf-8")
    for name, text in (("contract.md", CONTRACT), ("reader.md", READER), ("auditor.md", AUDITOR)):
        (root / "prompts" / name).write_text(text, encoding="utf-8")
    return root


def envelope(job_id: str = JOB, **over: Any) -> dict[str, Any]:
    env = {
        "kind": "casefile",
        "job_id": job_id,
        "trigger": "request",
        "mode": "full",
        "requester": "admin@firm.example",
        "message_ref": "<req-1@firm.example>",
        "request_text": "Please review Pat Example's files.",
        "scope": {"assisting": {"names": ["Pat Example"]}},
        "file_to_matter_id": LIBRARY,
        "file_to_matter_number": "OPS-LIBRARY",
        "folder_name": "Case File Manager",
    }
    env.update(over)
    return env


def make_job(job_dir: Path, cents: int = 0, **over: Any) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {**envelope(**over), "slug": "example", "month_cents_used": cents}
    (job_dir / "job.json").write_text(json.dumps(doc), encoding="utf-8")
    return job_dir


REPORT = "EXAMPLETOWN POLICE DEPARTMENT\nTRAFFIC COLLISION REPORT\nParty: Gamma Example\nDate 2026-01-12"
BILL = (
    "EXAMPLETOWN CLINIC\nITEMIZED STATEMENT\nPatient: Gamma Example\nDate of service 2026-01-13\nTotal charges 1,250.00"
)


def fake_chrome(root: Path) -> Path:
    """An executable that 'prints' any page to a small PDF, for SMD_CHROME."""
    exe = root / "fake-chrome"
    exe.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "out=[a.split('=',1)[1] for a in sys.argv if a.startswith('--print-to-pdf=')][0]\n"
        "open(out,'wb').write(b'%PDF-1.4 fake file status ' + sys.argv[-1].encode())\n",
        encoding="utf-8",
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe


class Client:
    def __init__(self, seat: "CfSeat") -> None:
        self.seat = seat
        self.writes: list[tuple[str, str, Any]] = []

    def get(self, path: str, **params: Any) -> Any:
        s = self.seat
        if path == "/matters":
            return {"value": [m for m in s.matters if m["status"] == params.get("Status")]}
        if path == "/staff":
            return {"value": [dict(x) for x in s.staff]}
        if path == "/tasks":
            done = params.get("IsCompleted")
            return {
                "value": [
                    dict(t)
                    for t in s.tasks
                    if t["matter"]["id"] == params.get("MatterId") and bool(t["isCompleted"]) == bool(done)
                ]
            }
        if path.startswith("/tasks/"):
            return dict(next(t for t in s.tasks if t["id"] == path.split("/")[2]))
        if path == "/events":
            return {"value": [e for e in s.events if e["matter"] == params.get("MatterId")]}
        if path.startswith("/contacts/"):
            return {"person": {"firstName": "Gamma", "lastName": "Example"}}
        m = re.fullmatch(r"/matters/([^/]+)(/memos|/layouts(?:/[^/]+)?)?", path)
        if m and m.group(2) is None:
            row = next(x for x in s.matters if x["id"] == m.group(1))
            return {**row, "clientIds": [CLIENT], "otherSideIds": [], "matterType": {"name": "Motor Vehicle Accident"}}
        if m and m.group(2) == "/memos":
            return {"value": []}
        if m and m.group(2) == "/layouts":
            return {"value": [{"id": MED_ITEM, "layoutDesign": {"id": DESIGN}}]} if m.group(1) == M1 else {"value": []}
        if m:
            return {"values": [{"key": k, "value": v} for k, v in s.layout.items()]}
        raise AssertionError(path)

    def request(self, method: str, path: str, json: Any = None, **_: Any) -> Any:
        self.writes.append((method, path, json))
        if method == "PUT" and path.startswith("/tasks/"):
            t = next(t for t in self.seat.tasks if t["id"] == path.split("/")[2])
            t.update(isCompleted=bool(json.get("isCompleted")), note=json.get("note"))
            return {}
        raise AssertionError((method, path))


class CfSeat:
    """Two matters on one caseload. Uploads land in the matter's listing at once."""

    def __init__(self) -> None:
        self.blobs: dict[str, bytes] = {
            DOC_REPORT: make_pdf([REPORT]),
            DOC_BILL: make_pdf([BILL]),
            DOC_SCAN: make_pdf([""]),
            DOC_M2: make_pdf(["EXAMPLE INTAKE FORM\nClient: Delta Example"]),
        }
        self.files: dict[str, list[dict[str, Any]]] = {
            M1: [
                self._row(DOC_REPORT, "Police report.pdf", "2026-01-20"),
                self._row(DOC_BILL, "Clinic bill.pdf", "2026-02-01"),
                self._row(DOC_SCAN, "Imaging bill.pdf", "2026-02-02"),
            ],
            M2: [self._row(DOC_M2, "Intake.pdf", "2026-03-01")],
            LIBRARY: [],
        }
        self.folders: dict[str, list[dict[str, Any]]] = {
            M1: [{"id": FOLDER, "name": "Correspondence", "parentId": None, "path": "/Correspondence"}],
            M2: [],
            LIBRARY: [],
        }
        self.matters = [
            {"id": M1, "number": "100001", "title": "Gamma v. Example", "status": "Open",
             "personResponsibleStaffId": STAFF_R, "personAssistingStaffId": STAFF_P},
            {"id": M2, "number": "100002", "title": "Delta intake", "status": "Pending",
             "personResponsibleStaffId": STAFF_R, "personAssistingStaffId": STAFF_P},
            # The firm's library matter, found by its number when no id is authored.
            {"id": LIBRARY, "number": "OPS-LIBRARY", "title": "Operator Library", "status": "Open"},
        ]  # fmt: skip
        self.staff = [
            {"id": STAFF_P, "firstName": "Pat", "lastName": "Example", "email": "pat@firm.example"},
            {"id": STAFF_R, "firstName": "Riley", "lastName": "Example", "email": "riley@firm.example"},
            {"id": STAFF_X, "firstName": "Admin", "lastName": "Example", "email": "admin@firm.example"},
        ]
        self.tasks = [
            self._task(TASK_OPEN, M1, "Request police report"),
            self._task(TASK_MONEY, M1, "Confirm lien amount"),
            {**self._task(TASK_DONE, M1, "Send intake letter"), "isCompleted": True, "completedDate": "2026-01-05"},
            self._task(TASK2, M2, "Call client"),
        ]
        self.events: list[dict[str, Any]] = []
        self.layout: dict[str, Any] = {"Providers[0]/Provider/DisplayName": "Example Imaging"}
        self.client = Client(self)
        self.uploads = 0

    @staticmethod
    def _row(fid: str, name: str, when: str) -> dict[str, Any]:
        stem, ext = name.rsplit(".", 1)
        return {
            "id": fid,
            "name": stem,
            "ext": "." + ext,
            "size": None,
            "folderId": FOLDER,
            "created": when,
            "modified": when,
            "deleted": False,
        }

    @staticmethod
    def _task(tid: str, mid: str, subject: str) -> dict[str, Any]:
        return {
            "id": tid,
            "subject": subject,
            "matter": {"id": mid},
            "createdBy": {"id": STAFF_R},
            "createdDate": "2026-01-02",
            "isCompleted": False,
            "note": "",
        }

    def list_files(self, matter_id: str) -> list[dict[str, Any]]:
        rows = [dict(r) for r in self.files.get(matter_id, [])]
        for r in rows:
            if r["size"] is None:
                r["size"] = len(self.blobs[r["id"]])
        return rows

    def folder_tree(self, matter_id: str) -> list[dict[str, Any]]:
        return [dict(f) for f in self.folders.get(matter_id, [])]

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        return [{"id": f, "url": f"https://fake.invalid/{f}", "size": len(self.blobs[f])} for f in file_ids]

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        data = self.blobs[url.rsplit("/", 1)[1]]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    def create_folder(self, matter_id: str, name: str) -> dict[str, Any]:
        fid = f"folder-{matter_id[:4]}-{len(self.folders[matter_id])}"
        self.folders[matter_id].append({"id": fid, "name": name, "parentId": None, "path": "/" + name})
        return {"id": fid}

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict[str, Any]:
        self.uploads += 1
        fid = f"up-{self.uploads:04d}"
        self.blobs[fid] = data
        stem, _dot, ext = name.rpartition(".")
        self.files[matter_id].append(
            {
                "id": fid,
                "name": stem,
                "ext": "." + ext,
                "size": len(data),
                "folderId": folder_id,
                "created": "2026-10-26",
                "modified": "2026-10-26",
                "deleted": False,
            }
        )
        return {"fileId": fid}


NAMES = {
    DOC_REPORT: "Police report.pdf",
    DOC_BILL: "Clinic bill.pdf",
    DOC_SCAN: "Imaging bill.pdf",
    DOC_M2: "Intake.pdf",
}


def doc_n(pack: str, doc_id: str, name: str | None = None) -> int | None:
    """The ``[doc N]`` a listing row gives a document (found by its name: the
    listing carries no ids), or None when the pack does not list it."""
    nm = name or NAMES.get(doc_id)
    if nm is None:
        return None
    m = re.search(r"\[doc (\d+)\] \| [^|\n]* \| " + re.escape(nm) + r" \|", pack)
    return int(m.group(1)) if m else None


def cite(pack: str, doc_id: str) -> str:
    """How a reader cites a document: ``[doc N]`` when listed, else its id."""
    n = doc_n(pack, doc_id)
    return f"[doc {n}]" if n is not None else doc_id


def review_for(number: str, pack: str) -> dict[str, Any]:
    """A record a careful reader would write from the synthetic pack."""
    base: dict[str, Any] = {
        "number": number,
        "matter_id": M1 if number == "100001" else M2,
        "clients": ["Gamma Example"] if number == "100001" else ["Delta Example"],
        "matter_type": "Motor Vehicle Accident",
        "date_of_loss": "2026-01-12",
        "statute_date": "2028-01-12",
        "opened": "2026-01-15",
        "status": "Open",
        "responsible": "Riley Example",
        "assisting": "Pat Example",
        "headline": "The police report is in; one bill is a scan.",
        "posture": "pre_demand",
        "posture_why": "No demand in the file.",
        "facilities": [],
        "done_tasks_checked": [],
        "records_to_order": [],
        "medicals": [],
        "insurance": {},
        "injury_flags": [],
        "time_sensitive": [],
        "letters": [],
        "questions": [],
        "unreadable_docs": [],
    }
    if number == "100001":
        base["open_tasks"] = [
            {"task_id": TASK_OPEN, "subject": "Request police report", "action": "mark_complete",
             "why": "Proof: police report names the client.", "proof_doc_ids": [cite(pack, DOC_REPORT)]},
            {"task_id": TASK_MONEY, "subject": "Confirm lien amount", "action": "leave_open", "why": "Money."},
        ]  # fmt: skip
        base["done_tasks_checked"] = [{"task_id": TASK_DONE, "subject": "Send intake letter", "check": "not_found"}]
        if doc_n(pack, DOC_BILL) is not None:
            base["medicals"] = [
                {"provider": "Exampletown Clinic", "service_start": "2026-01-13", "service_end": "2026-01-13",
                 "charge": 1250.0, "source_doc_id": cite(pack, DOC_BILL), "page": 1, "existing_row": False,
                 "note": "Itemized."}
            ]  # fmt: skip
    else:
        base["open_tasks"] = [
            {"task_id": TASK2, "subject": "Call client", "action": "leave_open", "why": "Nothing yet."}
        ]
    return base


def first_text(params: dict[str, Any]) -> str:
    c = params["messages"][0]["content"]
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


class Scripted:
    """The read answers ``record_review`` on its first turn (``review_fn``),
    or a ``mutate``d record first to see the validator refuse it; the audit
    answers ``verdict`` for every closure."""

    def __init__(
        self, review_fn: Any = review_for, verdict: str = "confirmed", mutate: Any = None, patch_fn: Any = None
    ) -> None:
        self.review_fn, self.verdict, self.mutate, self.patch_fn = review_fn, verdict, mutate, patch_fn
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._msg)

    def _msg(self, **params: Any) -> Any:
        self.calls.append(params)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=300, cache_creation_input_tokens=50
        )
        tools = {t["name"] for t in params.get("tools") or []}
        if "record_verdicts" in tools:
            items = json.loads(first_text(params))
            rows = [{"task_id": it["task_id"], "verdict": self.verdict, "reason": "the report itself"} for it in items]
            use = {"type": "tool_use", "id": "v", "name": "record_verdicts", "input": {"verdicts": rows}}
            return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)
        pack = first_text(params)
        number = re.search(r"# MATTER (\S+):", pack).group(1)
        if self.patch_fn is not None and "PRIOR REVIEW RECORD" in pack:
            patch = self.patch_fn(number, pack, len(params["messages"]))
            if patch is not None:  # None: this turn answers with the whole record
                use = {"type": "tool_use", "id": f"p{len(self.calls)}", "name": "record_review_patch", "input": patch}
                return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)
        rec = self.review_fn(number, pack)
        opened = json.dumps(params["messages"][1:])
        cited = [d for t in rec.get("open_tasks") or [] for d in t.get("proof_doc_ids") or []]
        cited += [m["source_doc_id"] for m in rec.get("medicals") or [] if m.get("source_doc_id")]
        for d in cited:
            ref = re.fullmatch(r"\[doc (\d+)\]", str(d))
            n = int(ref.group(1)) if ref else doc_n(pack, str(d))
            if n is not None and f"### [doc {n}]" not in pack and f"[doc {n}]" not in opened:
                use = {"type": "tool_use", "id": f"f{len(self.calls)}", "name": "fetch_doc", "input": {"doc": n}}
                return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)
        if self.mutate is not None and len(params["messages"]) == 1:
            rec = self.mutate(rec)
        use = {"type": "tool_use", "id": f"r{len(self.calls)}", "name": "record_review", "input": rec}
        return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)

    def _stream(self, **params: Any) -> Any:
        from demand_testkit import _Stream

        return _Stream(self._msg(**params))


def environ(tmp: Path) -> dict[str, str]:
    return {"SMD_CHROME": str(fake_chrome(tmp)), "MEDCHRON_DATA_DIR": str(tmp / "volume")}


__all__ = [name for name in dir() if not name.startswith("_") and name != "annotations"]

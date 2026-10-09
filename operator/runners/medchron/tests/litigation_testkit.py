"""Synthetic fixtures for the litigation job: a firm-inputs dir in
``litigation-firm.yaml``'s schema, an envelope, a seat that lists matters and
staff, and a scripted model that answers the three reads with tool calls.

Every name, number and date here is invented (Example / Exampletown / CV-0001).
Nothing is a firm's, a client's, or a matter's.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from medchron.litigation import vocab
from medchron_testkit import FakeSeat, doc_row, make_pdf

JOB = "01KTJ0BX0000000000000000AA"
LIBRARY = "99999999-9999-4999-8999-999999999999"
M1 = "11111111-1111-4111-8111-111111111111"
M2 = "22222222-2222-4222-8222-222222222222"
STAFF_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
STAFF_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def firm_data(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "firm": {"slug": "example", "display_name": "Example & Example, LLP"},
        "models": {"read": "claude-sonnet-5", "verify": "claude-sonnet-5", "audit": "claude-sonnet-5"},
        "per_job_cap_usd": 50.0,
        "monthly_budget_usd": 100.0,
        "court_paper_patterns": [r"complaint", r"summons", r"\bPOS\b|proof of service", r"answer", r"\bCMC\b"],
        "process_server_names": ["Example Legal Process"],
        "settlement_terms": [r"\bhas settled\b", r"notice of settlement", r"settlement check"],
        "form_hints": {"FORM-110": "case management statement; items 2-3 list each party's service and answer"},
        "email_recent_n": 2,
        "case_statuses": [*vocab.CASE_STATUSES, *vocab.DEFENDANT_STATUSES],
        "discovery_patterns": [r"interrogator", r"request for production"],
        "tool_iterations": 6,
        "timezone": "America/Los_Angeles",
    }
    data.update(over)
    return data


def make_inputs(root: Path, **over: Any) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "litigation-firm.yaml").write_text(yaml.safe_dump(firm_data(**over), sort_keys=False), encoding="utf-8")
    return root


def envelope(job_id: str = JOB, **over: Any) -> dict[str, Any]:
    env = {
        "kind": "litigation",
        "job_id": job_id,
        "trigger": "request",
        "requester": "admin@firm.example",
        "message_ref": "<req-1@firm.example>",
        "request_text": "Send me a fresh litigation status list.",
        "scope": {"all": True},
        "file_to_matter_id": LIBRARY,
        "file_to_matter_number": "OPS-LIBRARY",
        "folder_name": "Litigation Status",
    }
    env.update(over)
    return env


def make_job(job_dir: Path, cents: int = 0, **over: Any) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    doc = {**envelope(**over), "slug": "example", "month_cents_used": cents}
    (job_dir / "job.json").write_text(json.dumps(doc), encoding="utf-8")
    return job_dir


COMPLAINT = "SUPERIOR COURT OF EXAMPLETOWN\nGAMMA EXAMPLE v. DELTA EXAMPLE\nCase No. CV-0001\nCOMPLAINT FOR DAMAGES\nFILED 03/02/2026"
POS = "PROOF OF SERVICE OF SUMMONS\nDefendant DELTA EXAMPLE was personally served on 04/10/2026."
ANSWER = "ANSWER OF DEFENDANT DELTA EXAMPLE\nFiled April 30, 2026"


def matter_docs() -> list[tuple[str, str, bytes, str]]:
    return [
        ("f-c1", "Complaint Gamma.pdf", make_pdf([COMPLAINT]), "2026-03-03T10:00:00"),
        ("f-p1", "POS Delta.pdf", make_pdf([POS]), "2026-04-11T10:00:00"),
        ("f-a1", "Answer Delta.pdf", make_pdf([ANSWER]), "2026-05-01T10:00:00"),
        ("f-b1", "Medical bill.pdf", make_pdf(["Exampletown Clinic statement"]), "2026-05-02T10:00:00"),
    ]


class Client:
    def __init__(self, seat: "LitSeat") -> None:
        self.seat = seat

    def get(self, path: str, **params: Any) -> Any:
        if path == "/matters":
            rows = [m for m in self.seat.matters if params.get("Status") == "Open"]
            off = int(params.get("Offset") or 0)
            return {"value": rows[off : off + 500]}
        if path == "/staff":
            return {
                "value": [
                    {"id": STAFF_A, "firstName": "Alpha", "lastName": "Example"},
                    {"id": STAFF_B, "firstName": "Beta", "lastName": "Example"},
                ]
            }
        raise AssertionError(path)


class LitSeat(FakeSeat):
    """Per-matter listings (FakeSeat lists one set for every matter)."""

    def __init__(self, per_matter: dict[str, list[tuple[str, str, bytes, str]]]) -> None:
        blobs = {fid: blob for docs in per_matter.values() for fid, _n, blob, _d in docs}
        super().__init__([], [], blobs)
        self.per = {}
        for mid, docs in per_matter.items():
            rows = []
            for fid, name, blob, when in docs:
                r = doc_row(fid, name, None, len(blob))
                r.update(created=when, modified=when)
                rows.append(r)
            self.per[mid] = rows
        self.matters = [
            {"id": M1, "number": "100001", "title": "Gamma v. Delta", "personResponsibleStaffId": STAFF_A},
            {"id": M2, "number": "100002", "title": "Epsilon intake", "personResponsibleStaffId": STAFF_B},
        ]
        self.client = Client(self)
        self.library_rows: list[dict] = []

    def list_files(self, matter_id: str) -> list[dict]:
        if matter_id == LIBRARY:
            self._materialize()
            return [dict(d) for d in self.docs]
        return [dict(d) for d in self.per.get(matter_id, [])]

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict]:
        out = []
        for fid in file_ids:
            if fid.startswith("up-"):
                sent = self.sent[int(fid[3:]) - 1]
                self.blobs[fid] = sent["data"]
            out.append(super().mint(matter_id, [fid])[0])
        return out

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict:
        r = super().add_file(matter_id, folder_id, name, data)
        self.sent[-1]["data"] = data
        return r


def source(doc: int) -> dict[str, Any]:
    return {"doc": doc}


def good_result(n: dict[str, int]) -> dict[str, Any]:
    """A pass-1 answer; ``n`` maps file id to the doc number the context gave it."""
    return {
        "case_name": "Gamma Example v. Delta Example",
        "court": "Superior Court of Exampletown",
        "case_number": "CV-0001",
        "firm_role": "plaintiff",
        "case_status": {"value": vocab.ACTIVE, "detail": "no dismissal in the file", "doc": n["f-c1"]},
        "complaint_filed": {"date": "2026-03-02", "doc": n["f-c1"], "doc_date": "2026-03-02"},
        "next_court_date": {"date": None, "event": "", "doc": None},
        "defendants": [
            {
                "name": "Delta Example",
                "status": vocab.ANSWERED,
                "out_for_service": {"value": "no", "doc": None},
                "served": {"date": "2026-04-10", "method": "personal", "doc": n["f-p1"]},
                "answered": {"date": "2026-04-30", "doc": n["f-a1"]},
                "flags": [],
            }
        ],
        "discovery_propounded": [],
        "discovery_served_on_client": [],
        "matter_flags": [],
        "notes": "",
    }


def first_text(params: dict[str, Any]) -> str:
    c = params["messages"][0]["content"]
    return c if isinstance(c, str) else "".join(b.get("text", "") for b in c if isinstance(b, dict))


class ScriptedLit:
    """Answers each read: pass 1 opens a document once (a tool turn), then
    records ``result_fn(doc numbers)``; verify and audit record ``verdicts``."""

    def __init__(
        self,
        result_fn: Any = good_result,
        verdicts: list[dict] | None = None,
        audit: list[dict] | None = None,
        never_finish: bool = False,
        resolve_hits: bool = False,
        update_fn: Any = None,
    ) -> None:
        self.resolve_hits = resolve_hits
        #: An update read copies the CURRENT VALUES it is handed, then applies
        #: ``update_fn(values)`` (identity by default), as a model told to
        #: change only what the arrived documents change would.
        self.update_fn = update_fn or (lambda values: values)
        self.result_fn, self.verdicts, self.audit = result_fn, verdicts or [], audit or []
        self.never_finish = never_finish
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(stream=self._stream, create=self._create)

    @staticmethod
    def _numbers(params: dict[str, Any]) -> dict[str, int]:
        import re

        first = first_text(params)
        out = {}
        for num, name in re.findall(r"\[doc (\d+)\] ([^|]+?) \|", first):
            out[
                {"Complaint Gamma.pdf": "f-c1", "POS Delta.pdf": "f-p1", "Answer Delta.pdf": "f-a1"}.get(
                    name.strip(), name.strip()
                )
            ] = int(num)
        return out

    def _msg(self, params: dict[str, Any]) -> Any:
        self.calls.append(params)
        system = json.dumps(params.get("system"))
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        if "Transcribe every word" in json.dumps(params["messages"]):
            return SimpleNamespace(content=[{"type": "text", "text": "OCR TEXT"}], stop_reason="end_turn", usage=usage)
        turn = len(params["messages"])
        if self.never_finish or turn == 1:
            use = {
                "type": "tool_use",
                "id": f"t{len(self.calls)}",
                "name": "search_text",
                "input": {"pattern": "answer"},
            }
            return SimpleNamespace(content=[use], stop_reason="tool_use", usage=usage)
        if "YOUR PASS: determine" in system and "THIS IS AN UPDATE" in system:
            marker = "CURRENT VALUES (on the list now):\n"
            values = json.loads(first_text(params).split(marker, 1)[1])
            final = {"type": "tool_use", "id": "rec", "name": "record_result", "input": self.update_fn(values)}
        elif "YOUR PASS: determine" in system:
            final = {
                "type": "tool_use",
                "id": "rec",
                "name": "record_result",
                "input": self.result_fn(self._numbers(params)),
            }
        else:
            vs = self.audit if "last check before" in system else self.verdicts
            reviewed = []
            if self.resolve_hits:
                import re

                hits = re.search(r"SETTLEMENT-SCAN HITS to resolve in settlement_reviewed: (.*)", first_text(params))
                reviewed = (
                    [
                        {"doc": int(n), "finding": "an unrelated case"}
                        for n in re.findall(r"\[doc (\d+)\]", hits.group(1))
                    ]
                    if hits
                    else []
                )
            final = {
                "type": "tool_use",
                "id": "rec",
                "name": "record_verdicts",
                "input": {"verdicts": vs, "settlement_reviewed": reviewed},
            }
        return SimpleNamespace(content=[final], stop_reason="tool_use", usage=usage)

    def _stream(self, **params: Any) -> Any:
        from demand_testkit import _Stream

        return _Stream(self._msg(params))

    def _create(self, **params: Any) -> Any:
        return self._msg(params)

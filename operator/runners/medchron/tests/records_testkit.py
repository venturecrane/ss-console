"""Fakes for the records lane tests: a Smokeball client, the Messages API at
the page finder's HTTP seam, a doorway client for the parse, and a job dir.
Every name here is invented; no client material."""

from __future__ import annotations

import io
import json
import re
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import yaml
from pypdf import PdfWriter

from medchron_testkit import PRICING

MATTER = "11111111-2222-3333-4444-555555555555"
JOB = "01JRECXRDS0000000000000000"
MODEL = "claude-opus-5"
REQUEST = "Please send the 3/3/2021 Northside Clinic visit and the discharge summary. Thanks"
#: What the fake model "reads" on each page: a visit on 1-4, a discharge on 5-10.
TRUTH = {
    **{n: ("3/3/2021", "Northside Clinic", "progress note", n == 1) for n in range(1, 5)},
    **{n: ("6/30/2024", "Northside Clinic", "discharge summary", n == 5) for n in range(5, 11)},
}
LABEL = re.compile(r"^Page (\d+):$")
PARSED = {
    "items": [
        {
            "asked": "the 3/3/2021 Northside Clinic visit",
            "hints": {"date": "3/3/2021", "provider": "Northside Clinic", "pages": ""},
            "for_another_person": False,
        },
        {
            "asked": "the discharge summary",
            "hints": {"date": "", "provider": "", "pages": ""},
            "for_another_person": False,
        },
    ]
}
MATCH = {
    "the 3/3/2021 Northside Clinic visit": [
        {"asked": "the 3/3/2021 Northside Clinic visit", "found": True, "sections": ["S1"], "reason": "S1"}
    ],
    "the discharge summary": [{"asked": "the discharge summary", "found": True, "sections": ["S2"], "reason": "S2"}],
}


def pdf(pages: int) -> bytes:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


def fid(n: int) -> str:
    return f"{n:08d}-aaaa-bbbb-cccc-dddddddddddd"


class _Resp:
    def __init__(self, data: bytes) -> None:
        self.status_code, self._data = 200, data

    def iter_bytes(self):
        yield self._data


class _Http:
    def __init__(self, blobs: dict[str, bytes]) -> None:
        self.blobs = blobs

    @contextmanager
    def stream(self, _method: str, url: str):
        yield _Resp(self.blobs[url])


class FakeSmokeball:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.meta: dict[str, dict[str, Any]] = {}
        self.blobs: dict[str, bytes] = {}
        self.downloads: list[str] = []
        self.writes: list[str] = []
        self._http = _Http(self.blobs)

    def add(self, n: int, name: str, data: bytes) -> str:
        f = fid(n)
        row = {"id": f, "name": name, "fileExtension": ".pdf", "sizeBytes": len(data), "dateModified": "2026-01-01"}
        self.rows.append(row)
        self.meta[f] = {**row, "matter": {"href": f"/matters/{MATTER}"}}
        self.blobs[f"https://s3/{f}"] = data
        return f

    def get(self, path: str, **params: Any) -> Any:
        if path.endswith("/documents/files"):
            off = params.get("Offset", 0)
            return {"value": self.rows[off : off + params.get("Limit", 500)]}
        if path.endswith("/documents/folders"):
            return {"value": [{"folders": []}]}
        return self.meta[path.rsplit("/", 1)[-1]]

    def request(self, method: str, path: str, **_: Any) -> Any:
        if method != "GET":
            self.writes.append(path)
            raise AssertionError("a records job writes nothing to the firm's system")
        f = path.split("/files/")[1].split("/")[0]
        self.downloads.append(f)
        return {"downloadUrl": f"https://s3/{f}"}


class FakeFinderModel:
    """The Messages API at ``vision._http_client``: index requests answered from
    the code-written labels, match requests from :data:`MATCH` by the request words."""

    def __init__(
        self,
        match: dict[str, list[dict[str, Any]]] | None = None,
        *,
        refuse: frozenset[int] = frozenset(),
        fail: frozenset[int] = frozenset(),
    ) -> None:
        self.match = {k: list(v) for k, v in (MATCH if match is None else match).items()}
        self.bodies: list[dict[str, Any]] = []
        #: Pages the model declines on every request that carries them (billed input, as a refusal is).
        self.refuse = refuse
        #: Pages whose request fails on the wire every time (no usage).
        self.fail = fail

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        content = body["messages"][0]["content"]
        if "pages" in body["output_config"]["format"]["schema"]["properties"]:
            labels = {
                int(m.group(1)) for b in content if b["type"] == "text" for m in [LABEL.match(b.get("text", ""))] if m
            }
            if labels & self.fail:
                return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})
            if labels & self.refuse:
                return httpx.Response(
                    200,
                    json={
                        "content": [],
                        "stop_reason": "refusal",
                        "usage": {"input_tokens": 1600 * len(labels), "output_tokens": 0},
                    },
                )
            rows = []
            for b in content:
                m = LABEL.match(b.get("text", "")) if b["type"] == "text" else None
                if not m:
                    continue
                n = int(m.group(1))
                date, provider, kind, starts = TRUTH.get(n, ("", "", "", False))
                sec = {"heading": kind.upper(), "document_type": kind, "date": date, "provider": provider}
                rows.append(
                    {
                        "page": n,
                        "continues_previous": not starts,
                        "sections": [sec] if starts else [],
                        "dates": [f"DOS {date}"] if date else [],
                        "running_header": "",
                        "page_of": "",
                        "legible": True,
                    }
                )
            answer: dict[str, Any] = {"pages": rows}
        else:
            words = next(b["text"] for b in content if b["text"].startswith("THE COLLEAGUE'S REQUEST:"))
            what = words.split("\n", 1)[1].strip()
            answer = {"items": self.match.get(what, [])}
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": json.dumps(answer)}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1600, "output_tokens": 80},
            },
        )

    def index_calls(self) -> int:
        return sum(1 for b in self.bodies if "pages" in b["output_config"]["format"]["schema"]["properties"])

    def match_calls(self) -> int:
        return len(self.bodies) - self.index_calls()


class FakeParseClient:
    """The doorway's Anthropic client: one structured answer for the parse."""

    def __init__(self, answer: dict[str, Any] | None = None) -> None:
        self.answer = PARSED if answer is None else answer
        self.calls = 0

    @property
    def messages(self) -> Any:
        return self

    def create(self, **params: Any) -> Any:
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(self.answer))],
            usage=SimpleNamespace(
                input_tokens=500, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0
            ),
            stop_reason="end_turn",
            model=params.get("model"),
        )


def setup_env(tmp: Path, monkeypatch: Any, model: FakeFinderModel) -> dict[str, Path]:
    from smokeball_connector import vision

    pricing = tmp / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    inputs = tmp / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "records-firm.yaml").write_text(
        yaml.safe_dump({"per_job_cap_usd": 5.0, "monthly_budget_usd": 100.0, "usd_per_page": 0.01}), encoding="utf-8"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("SMOKEBALL_VISION_DISABLED", raising=False)
    monkeypatch.setenv("SMOKEBALL_PAGE_FINDER_MODEL", MODEL)
    monkeypatch.setenv("SMOKEBALL_PAGE_INDEX_DIR", str(tmp / "index"))
    monkeypatch.setenv("SMOKEBALL_VISION_WORKERS", "1")
    monkeypatch.setattr(vision, "_http_client", lambda _t: httpx.Client(transport=httpx.MockTransport(model)))
    return {"pricing": pricing, "inputs": inputs, "state": tmp / "state"}


def job_dir(tmp: Path, **extra: Any) -> Path:
    jd = tmp / "job"
    (jd / "data").mkdir(parents=True, exist_ok=True)
    doc = {
        "kind": "records",
        "job_id": JOB,
        "trigger": "request",
        "slug": "example",
        "requester": "pat.lane@example-firm.test",
        "message_ref": "AAMkAGraphMessageId0001",
        "request_text": REQUEST,
        "matter_id": MATTER,
        "ack": "turn",
        "month_cents_used": 0,
        "per_job_cap_usd": 5.0,
        "monthly_budget_usd": 100.0,
        **extra,
    }
    (jd / "job.json").write_text(json.dumps(doc), encoding="utf-8")
    return jd

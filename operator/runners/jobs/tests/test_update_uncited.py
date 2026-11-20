"""The update's `update_uncited` stage (``stages/update_uncited.py``): every
page the writer read and cited nothing from is settled by code or classed by
the reader; a dated record is written again by the EXISTING writer, its
entries reach assemble like any other; a dated record still uncited holds.
Through the real StageRun, Doorway, Composer, parsers and stage runner, with
only the SDK client scripted. Every name, date and value is invented."""

from __future__ import annotations

import json
import re
from dataclasses import replace as dc_replace
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable

import pytest

from jobs.medchron import dag
from jobs.medchron.update import body as B, collect as C, uncited_read as UR
from jobs.stages import assemble, compose, entry_audit_io as IO
from jobs.stages import update_uncited as UU, update_uncited_plan as UP, update_uncited_write as UW
from jobs.stages.chunking import sha
from jobs_testkit import make_pdf
from test_audit import Usage, _sr

T2 = "Exhibit 2 - Example Family Clinic (Medical Records)"
T3 = "Exhibit 3 - Example Pharmacy (Medical Records)"
VISIT = "Example Family Clinic. Office visit on 03/02/2019 for a sore throat. Throat exam was red without exudate."
IMMS = "Immunizations (continued). Influenza vaccine given 10/01/2018, left deltoid, given by a nurse."
TB = "TB skin test placed 09/03/2018, right forearm. Read 09/05/2018, 0 mm induration, negative."
FAX = "Fax cover sheet. Pages to follow: 4. Confidential."
DOC2 = [VISIT, IMMS, TB, FAX]
DOC3 = ["Prescription order 08/20/2018: amoxicillin 500 mg three times daily for ten days, by an example doctor."]


def _entry(date: str, prov: str, sentence: str, title: str, page: str) -> str:
    return f"{date}\n{prov} | Medical Diagnoses\n\nMedical Diagnoses\n{sentence} (FILE: {title}.pdf, p. {page})"


def map_text(entries: list[str], seen: str) -> str:
    body = "\n\n".join(entries) if entries else "none in this chunk"
    return (
        f"## ENTRIES\n{body}\n\n## INDEX\nnone in this chunk\n\n## BILLING-DATES\nnone in this chunk\n\n"
        f"## CONFLICTS / REFERENCED-BUT-ABSENT\nnone observed\n\n## FILES-SEEN\n{seen}\n"
    )


BASE_MAP = map_text(
    [_entry("03/02/2019", "Example Family Clinic", "Throat exam was red without exudate.", T2, "1")],
    f"=== FILE: {T2}.pdf (fileId n2) === entries: 1",
)


class _Stream:
    def __init__(self, text: str) -> None:
        self.m = NS(content=[NS(type="text", text=text)], stop_reason="end_turn", usage=Usage())

    def __enter__(self) -> _Stream:
        return self

    def __exit__(self, *a: object) -> bool:
        return False

    def get_final_message(self) -> Any:
        return self.m


def reader_all(klass: str = UR.DATED) -> Callable[[str], str]:
    """A reader reply giving every shown page `klass`."""

    def reply(text: str) -> str:
        pages = [int(p) for p in re.findall(r"^Exhibit \d+ p\. (\d+):", text, re.M)]
        rows = [{"page": p, "class": klass, **({"dates": ["10/01/2018"]} if klass == UR.DATED else {})} for p in pages]
        return json.dumps({"pages": rows, "complete": True})

    return reply


def writer_citing(text: str) -> str:
    """A writer reply: one entry per page of the chunk, cited to its page."""
    out = []
    for name, _fid in re.findall(r"=== FILE: (.+?) \(fileId ([\w.-]+)\) ===", text):
        for p in re.findall(r"\[p\.(\d+)\]", text):
            out.append(_entry(f"09/{int(p):02d}/2018", "Example Nurse", f"Record on page {p}.", name[:-4], p))
    return map_text(out, "\n".join(re.findall(r"=== FILE: .+? ===", text)))


class Client:
    """The SDK client, scripted: the reader by `read(text)`, the writer by `write(text)`."""

    def __init__(self, read: Callable[[str], str] | None = None, write: Callable[[str], str] = writer_citing) -> None:
        self.read, self.write = read or reader_all(), write
        self.calls: list[str] = []
        self.requests: list[dict[str, Any]] = []
        self.messages = self

    def stream(self, **kw: Any) -> _Stream:
        system = kw["system"][0]["text"] if isinstance(kw["system"], list) else kw["system"]
        content = kw["messages"][0]["content"]
        self.requests.append(kw)
        if "UNCITED PAGES" in system:
            self.calls.append("read")
            return _Stream(self.read("\n".join(str(b.get("text", "")) for b in content)))
        self.calls.append("write")
        return _Stream(self.write(content))


def _doc(sd: Path, rid: str, title: str, pages: list[str]) -> dict[str, Any]:
    (sd / "text").mkdir(parents=True, exist_ok=True)
    (sd / "text" / f"{rid}.txt").write_text("".join(f"[p.{i}]\n{t}\n" for i, t in enumerate(pages, 1)))
    out = sd / "out" / "alpha"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{title}.pdf").write_bytes(make_pdf(pages))
    return {"id": rid, "name": title, "ext": ".pdf", "text_path": str(sd / "text" / f"{rid}.txt"), "compose": True}


def _chunk(rd: Path, i: int, rows: list[tuple[str, str, list[int], list[str]]]) -> str:
    text = "".join(
        f"=== FILE: {t}.pdf (fileId {rid}) ===\n" + "\n".join(f"[p.{p}]\n{pages[p - 1]}" for p in ps) + "\n\n"
        for t, rid, ps, pages in rows
    )
    (rd / f"chunk-{i:02d}.txt").write_text(text)
    (rd / f"chunk-{i:02d}.sha").write_text(sha(text) + "\n")
    compose.append_jsonl(rd / "usage.jsonl", {"chunk": i, "in": 1, "out": 1, "stop": "end_turn", "sha": sha(text)})
    return text


@pytest.fixture
def scene(job_dir: Path, firm_config_path: Path, data_root: Path):
    def make(client: Client | None = None, maps: list[str] | None = None, docs3: bool = False, fresh: bool = True):
        log: list[str] = []
        sr = _sr(job_dir, firm_config_path, data_root, client, log)
        sd, rd = sr.slug_dir, sr.slug_dir / "runs" / "alpha"
        if fresh:
            rd.mkdir(parents=True, exist_ok=True)
            rows = [_doc(sd, "n2", T2, DOC2)] + ([_doc(sd, "n3", T3, DOC3)] if docs3 else [])
            (sd / "units").mkdir(exist_ok=True)
            (sd / "units" / "alpha.json").write_text(json.dumps(rows))
            ink = [{"doc": r["id"], "page": p, "evidence": {"inked": True}} for r in rows for p in range(1, 5)]
            (rd / "pages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in ink))
            _chunk(rd, 1, [(T2, "n2", [1, 2, 3, 4], DOC2)])
            if docs3:
                _chunk(rd, 2, [(T3, "n3", [1], DOC3)])
            for i, m in enumerate(maps or [BASE_MAP], 1):
                (rd / f"map-{i:02d}.md").write_text(m)
        sr.doorway.sleep = lambda _s: None
        return sr, rd, log

    return make


def _rec(rd: Path) -> dict[str, Any]:
    return json.loads((rd / UU.RECORD).read_text())


def _fax_is_non_record(text: str) -> str:
    pages = [int(p) for p in re.findall(r"^Exhibit \d+ p\. (\d+):", text, re.M)]
    rows = [
        {"page": p, "class": UR.NON_RECORD} if p == 4 else {"page": p, "class": UR.DATED, "dates": []} for p in pages
    ]
    return json.dumps({"pages": rows, "complete": True})


# ---- the thin path -------------------------------------------------------------------
def test_a_dated_printout_block_no_entry_cited_is_written_by_the_existing_writer(scene) -> None:
    client = Client(read=_fax_is_non_record)
    sr, rd, log = scene(client)
    assert UU.run(sr) == 0, log
    assert client.calls == ["read", "write"]  # one run of pages 2-4, one extra chunk
    wrote = client.requests[1]
    assert wrote["system"][0]["text"] == client.requests[1]["system"][0]["text"]
    assert "ENTRIES" in wrote["system"][0]["text"] and "UNCITED PAGES" not in wrote["system"][0]["text"]
    assert re.findall(r"\[p\.(\d+)\]", wrote["messages"][0]["content"]) == ["2", "3"]
    assert "10/01/2018" not in json.dumps(UW.read_extras(rd)) and (rd / "map-02.md").is_file()
    rec = _rec(rd)
    assert rec["uncited"] == 3 and rec["classed"] == {UR.DATED: 2, UR.NON_RECORD: 1}
    assert rec["routed"] == 2 and rec["extra_chunks"] == 1 and rec["entries_written"] == 2
    assert rec["still_uncited"] == 0 and "Influenza" not in json.dumps(rec)  # counts only, no record text


def test_a_page_that_continues_an_entry_and_holds_a_dated_record_is_routed(scene) -> None:
    def reply(text: str) -> str:
        rows = [{"page": p, "class": UR.DATED, "dates": ["09/03/2018"]} for p in (2, 3)]
        rows.append({"page": 4, "class": UR.CONTINUATION, "entry": "03/02/2019 | Example Family Clinic"})
        return json.dumps({"pages": rows, "complete": True})

    client = Client(read=reply)
    sr, rd, log = scene(client)
    assert UU.run(sr) == 0, log
    assert "03/02/2019 | Example Family Clinic" in client.requests[0]["messages"][0]["content"][0]["text"]
    assert _rec(rd)["classed"] == {UR.DATED: 2, UR.CONTINUATION: 1} and _rec(rd)["routed"] == 2


def test_an_update_with_no_uncited_page_does_nothing_and_costs_nothing(scene) -> None:
    every = map_text(
        [_entry("03/02/2019", "Example Family Clinic", "Visit and records.", T2, "1-4")],
        f"=== FILE: {T2}.pdf (fileId n2) === entries: 1",
    )
    client = Client()
    sr, rd, log = scene(client, maps=[every])
    assert UU.run(sr) == 0, log
    assert client.calls == [] and _rec(rd)["uncited"] == 0 and not (rd / "map-02.md").exists()


def test_code_settles_billing_blank_named_unread_and_picture_copies_without_a_model(scene) -> None:
    client = Client()
    sr, rd, log = scene(client)
    text = sr.slug_dir / "text" / "n2.txt"
    text.write_text(text.read_text().replace(FAX, ""))  # page 4: no text, and no ink
    (rd / "pages.jsonl").write_text(
        "".join(json.dumps({"doc": "n2", "page": p, "evidence": {"inked": p != 4}}) + "\n" for p in range(1, 5))
    )
    (rd / compose.UNREAD).write_text(json.dumps([{"chunk": "1", "file": f"{T2}.pdf", "pages": [2], "reason": "empty"}]))
    ud = rd / "update"
    ud.mkdir()
    (ud / "page_identity.json").write_text(json.dumps({"pages": {"n2": {"3": ["already", "exhibit:1", 2, "exact"]}}}))
    assert UU.run(sr) == 0, log
    assert client.calls == []
    assert _rec(rd)["settled_by_code"] == {UP.NAMED_UNREAD: 1, UP.DELIVERED_COPY: 1, UP.BLANK: 1}


def test_a_page_with_no_text_layer_is_shown_to_the_reader_as_its_picture(scene) -> None:
    client = Client(read=reader_all(UR.NON_RECORD))
    sr, rd, log = scene(client)
    text = sr.slug_dir / "text" / "n2.txt"
    text.write_text(text.read_text().replace(FAX, ""))  # page 4: no text, but ink: a picture-only page
    assert UU.run(sr) == 0, log
    content = client.requests[0]["messages"][0]["content"]
    labels = [b.get("text", "") for b in content]
    at = next(i for i, t in enumerate(labels) if t.startswith("Exhibit 2 p. 4"))
    assert content[at + 1]["type"] == "image"
    assert _rec(rd)["classed"] == {UR.NON_RECORD: 3} and _rec(rd)["routed"] == 0


def test_a_split_chunk_counts_each_page_once(scene) -> None:
    half = map_text([], f"=== FILE: {T2}.pdf [continued] === nothing extractable: no dated encounter on pp. 3-4")
    client = Client(read=reader_all(UR.NON_RECORD))
    sr, rd, log = scene(client, maps=[])
    (rd / "map-01-1.md").write_text(BASE_MAP)
    (rd / "map-01-2.md").write_text(half)
    (rd / "chunk-01.parts").write_text(json.dumps({"expected": [1, 2], "next": 3}))
    assert UU.run(sr) == 0, log
    assert _rec(rd)["uncited"] == 3 and client.calls == ["read"]


# ---- what the new entries reach ----------------------------------------------------------
def test_a_document_no_entry_cited_becomes_cited_and_is_not_an_uncited_exhibit(scene) -> None:
    client = Client()
    sr, rd, log = scene(
        client, maps=[BASE_MAP, map_text([], f"=== FILE: {T3}.pdf (fileId n3) === entries: 0")], docs3=True
    )
    assert UU.run(sr) == 0, log
    assert assemble.run(sr) == 0
    provisional = [{"exhibit": 2, "title": T2}, {"exhibit": 3, "title": T3}]
    _entries, keep, uncited, _remap = C.compact(C.composed(rd), provisional)
    # update_collect writes new_exhibits.uncited.json from exactly this list
    assert [r["exhibit"] for r in keep] == [2, 3] and uncited == []


def test_new_pre_incident_entries_reach_the_summary_step_after_this_stage(scene) -> None:
    names = [s.name for s in dag.stages(update=True)]
    at = names.index("update_uncited")
    assert names[at - 1] == "map" and names[at + 1] == "repair_truncated" and names.index("update_summary") > at
    client = Client()
    sr, rd, log = scene(client)
    assert UU.run(sr) == 0, log
    assert assemble.run(sr) == 0
    added_pre = [e for e in C.composed(rd) if B.iso(e) < "2018-12-31"]  # run_summary's own test
    assert len(added_pre) == 3  # pages 2, 3 and 4 written as their own entries


# ---- resume, cap, replay ------------------------------------------------------------------
def test_a_resume_pays_for_nothing_already_answered(scene) -> None:
    sr, rd, log = scene(Client())
    assert UU.run(sr) == 0, log
    again = Client(read=lambda _t: "never", write=lambda _t: "never")
    sr2, _rd, log2 = scene(again, fresh=False)
    assert UU.run(sr2) == 0, log2
    assert again.calls == [] and _rec(rd)["entries_written"] == 3


def test_the_reads_past_the_cap_are_the_firms_ordinary_cap_hold(scene, monkeypatch, pricing_path) -> None:
    from jobs.limits import CAP_SETTING, LimitHold

    monkeypatch.setenv("JOBS_PRICING_JSON", str(pricing_path))
    client = Client()
    sr, rd, log = scene(client)
    sr.job = dc_replace(sr.job, cap_usd=0.000001)
    with pytest.raises(LimitHold) as hold:
        UU.run(sr)
    assert client.calls == [] and hold.value.reason.startswith(CAP_SETTING) and "update_uncited" in hold.value.reason


def test_a_replay_answers_the_reader_and_the_writer_from_files_at_no_cost(scene, monkeypatch) -> None:
    monkeypatch.setenv(IO.REPLAY_ENV, "1")
    never = Client(read=lambda _t: pytest.fail("a replay never calls the reader"), write=lambda _t: "")
    sr, rd, log = scene(never)
    assert UU.run(sr) == UU.EXIT_PENDING, log
    req = json.loads((rd / UW.DIR / "x2-p2-4.read1.request.json").read_text())
    assert [b for b in req["content"] if b["type"] == "image"][0] == {"type": "image", "exhibit": 2, "page": 2}
    rows = [{"page": p, "class": UR.DATED, "dates": []} for p in (2, 3, 4)]
    answer = {"hash": req["hash"], "text": json.dumps({"pages": rows, "complete": True}), "stop_reason": "end_turn"}
    (rd / UW.DIR / "x2-p2-4.read1.json").write_text(json.dumps(answer))
    assert UU.run(sr) == UU.EXIT_PENDING, log
    wreq = json.loads((rd / UW.DIR / UW.WRITER / "map-02.request.json").read_text())
    assert wreq["chunk"] == "2" and "[p.3]" in wreq["content"]
    (rd / UW.DIR / UW.WRITER / wreq["answer"]).write_text(writer_citing(wreq["content"]))
    assert UU.run(sr) == 0, log
    assert never.calls == [] and _rec(rd)["entries_written"] == 3


# ---- the holds ---------------------------------------------------------------------------
def test_a_dated_record_the_extra_pass_writes_no_entry_for_holds_naming_its_pages(scene) -> None:
    client = Client(write=lambda _t: map_text([], f"=== FILE: {T2}.pdf (fileId n2) === nothing extractable: none"))
    sr, rd, log = scene(client)
    assert UU.run(sr) == UU.EXIT_STILL_UNCITED, log
    assert _rec(rd)["held"] == [{"exhibit": 2, "pages": [2, 3, 4]}] and client.calls.count("write") == 1
    assert UU.EXITS[UU.EXIT_STILL_UNCITED][0] == "held"


def test_a_reply_unreadable_after_one_retry_holds(scene) -> None:
    client = Client(read=lambda _t: '{"pages": [], "complete": true}')
    sr, rd, log = scene(client)
    assert UU.run(sr) == UU.EXIT_UNREAD, log
    assert client.calls == ["read", "read"] and _rec(rd)["unread"][0]["pages"] == [2, 3, 4]


def test_a_network_failure_is_the_infrastructure_exit(scene) -> None:
    from jobs.deadline import CallDeadlineError

    class Down(Client):
        def stream(self, **kw: Any) -> _Stream:
            raise CallDeadlineError("no reply")

    sr, rd, log = scene(Down())
    assert UU.run(sr) == UU.EXIT_INFRA, log


def test_the_reply_parser_takes_one_class_per_page_shown() -> None:
    run = UR.Run(2, (2, 3))
    ok = {"pages": [{"page": 2, "class": "non_record"}, {"page": 3, "class": "duplicate", "of": 2}], "complete": True}
    assert UR.parse(json.dumps(ok), run)[3].of == 2
    for bad in (
        {**ok, "complete": False},
        {"pages": ok["pages"][:1], "complete": True},
        {"pages": [*ok["pages"], {"page": 9, "class": "non_record"}], "complete": True},
        {"pages": [{"page": 2, "class": "maybe"}, ok["pages"][1]], "complete": True},
        {"pages": [{"page": 2, "class": "continuation"}, ok["pages"][1]], "complete": True},
    ):
        with pytest.raises(UR.Unparsed):
            UR.parse(json.dumps(bad), run)


def test_runs_are_adjacent_pages_of_one_exhibit_and_bounded() -> None:
    pages = [(2, p) for p in range(1, 9)] + [(2, 11), (3, 1)]
    assert UP.runs(pages) == [(2, (1, 2, 3, 4, 5, 6)), (2, (7, 8)), (2, (11,)), (3, (1,))]


#: The thin path's reader request, hashed as its saved answer is keyed
#: (``entry_audit_io.request_hash``): a change to the prompt, the page blocks or
#: the context lines moves it, and every saved answer of a proof run with it.
PINNED_READ = "f6ca1a555e1115dd920e7ab5fc245b294f2e4eabce3cabbbbc2e69baf87aa577"


def test_the_reader_request_is_pinned(scene) -> None:
    sr, rd, log = scene(Client(read=_fax_is_non_record))
    assert UU.run(sr) == 0, log
    assert json.loads((rd / UW.DIR / "x2-p2-4.read1.json").read_text())["hash"] == PINNED_READ

"""A revision of EVERY filed discovery response of a request (revise/set_run.py,
2026-11-21): the firm's standing rules made in each filed document, on top of
the staff's latest edits, as new versions of the same files.

Driven through the real classes: ``ReviseSetRun`` over a versioned seat, the
real doorway over a scripted client, the real wording store and its switch,
the real in-place editor and its proof. Every name, number, rule and sentence
is invented (Example / Exampletown). Each test names the line it defends.
"""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import docx
import pytest

from jobs.drafting import job as job_mod
from jobs.drafting.forms import saved
from jobs.drafting.revise import checklist as ck, responses as rs, rules as rules_mod, set_units as su, versions

NO_DOCS = "After a diligent search and a reasonable inquiry, responding party has no responsive documents."
NEVER = "Responding party is informed that no such documents have ever existed."
OBJECT = "Responding party objects to this request as overbroad and unduly burdensome."
RULES = [
    {
        "rule_id": "wr-nodocs",
        "text": "Where no documents exist, use the firm's no-documents wording.",
        "wording": [f'Where no documents exist, say "{NO_DOCS}" or, if they never existed, "{NEVER}"'],
        "doc_kinds": ["discovery_response"],
        "attorney": None,
    },
    {
        "rule_id": "wr-object",
        "text": "Object to overbroad requests first.",
        "wording": [f'Open an overbroad request\'s response with "{OBJECT}"'],
        "doc_kinds": ["discovery_response"],
        "attorney": None,
    },
    {
        "rule_id": "wr-tone",
        "text": "Keep responses short.",
        "wording": ["Keep responses short."],
        "doc_kinds": ["discovery_response"],
        "attorney": None,
    },
]
LABELS = {"special": "SPECIAL INTERROGATORY", "rfp": "REQUEST FOR PRODUCTION", "form": "FORM INTERROGATORY"}
NAMES = {
    "special": "Responses to Special Interrogatories, Set One.docx",
    "rfp": "Responses to Requests for Production, Set One.docx",
    "form": "Responses to Form Interrogatories, Set One.docx",
}
KINDS = {"special": "special_interrogatories", "rfp": "requests_for_production", "form": "form_interrogatories"}
NUMBERS = {"special": ["1", "2", "3"], "rfp": ["1", "2", "3"], "form": ["1.1", "2.1"]}


def response_doc(kind: str, answers: dict[str, str] | None = None) -> bytes:
    """A filed response document: a caption, then each request restated and answered."""
    d = docx.Document()
    d.add_paragraph("SUPERIOR COURT OF THE STATE OF EXAMPLE")
    d.add_paragraph(f"PLAINTIFF'S {NAMES[kind][:-5].upper()}")
    for n in NUMBERS[kind]:
        d.add_paragraph().add_run(f"{LABELS[kind]} NO. {n}:").bold = True
        d.add_paragraph(f"Identify every document about item {n}.")
        d.add_paragraph().add_run(f"RESPONSE TO {LABELS[kind]} NO. {n}:").bold = True
        d.add_paragraph((answers or {}).get(n, f"Responding party answers item {n}. No documents exist."))
    d.add_paragraph("DATED: December 1, 2026")
    d.add_paragraph("By: Example Lawyer, attorney for Plaintiff")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def text_of(blob: bytes) -> list[str]:
    return [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs]


# ---- the seat ---------------------------------------------------------------------------------
class SetSeat:
    """A matter with versioned files; a staff save is a new version."""

    def __init__(self, files: dict[str, tuple[str, bytes]]) -> None:
        self.names = {fid: name for fid, (name, _b) in files.items()}
        self.files = {fid: [b] for fid, (_n, b) in files.items()}
        self.sent: list[str] = []
        #: Called on every fetch: a test that saves a staff version mid-run.
        self.on_fetch: Callable[[str], None] = lambda fid: None

    def _vid(self, fid: str) -> str:
        return f"{fid}-v{len(self.files[fid])}"

    def staff_save(self, fid: str, blob: bytes) -> None:
        self.files[fid].append(blob)

    def file_meta(self, matter_id: str, fid: str) -> dict:
        return {
            "id": fid,
            "versionId": self._vid(fid),
            "isDeleted": False,
            "folder": {"id": "folder-0001"},
            "name": self.names[fid][:-5],
            "fileExtension": ".docx",
            "sizeBytes": len(self.files[fid][-1]),
        }

    def mint(self, matter_id: str, ids: list[str]) -> list[dict]:
        return [{"id": i, "url": f"https://example.invalid/{i}"} for i in ids]

    def fetch(self, url: str, dest: Path, size: Any) -> int:
        fid = url.rsplit("/", 1)[1]
        blob = self.files[fid][-1]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(blob)
        self.on_fetch(fid)
        return len(blob)

    def add_file_version(self, matter_id, fid, data, *, expected_version, expected_folder=None, expected_name=None):
        if expected_version != self._vid(fid):
            raise RuntimeError("version moved; nothing sent")
        assert expected_folder == "folder-0001" and expected_name == self.names[fid]
        self.sent.append(fid)
        self.files[fid].append(data)
        return {"fileId": fid}

    def matter_number(self, matter_id: str) -> str | None:
        from demand_testkit import MATTER

        return "100001" if matter_id == MATTER else None

    def matter_facts(self, matter_id: str) -> dict:
        return {
            "matter_number": "100001",
            "client_name": "Alpha Example",
            "responsible_attorney": "Example Lawyer",
            "errors": [],
        }


class ValuesClient:
    """The Anthropic client the doorway calls: the values for a document, picked by its label."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers, self.calls = answers, []
        self.messages = SimpleNamespace(create=self._create, stream=None)

    def _create(self, **params: Any) -> Any:
        user = params["messages"][-1]["content"]
        user = user if isinstance(user, str) else user[0]["text"]
        kind = next(k for k, label in LABELS.items() if f"{label} NO." in user)
        self.calls.append(kind)
        got = self.answers[kind]
        text = json.dumps(got(user) if callable(got) else got)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)


def _value(req: str, rule: str, action: str, phrase: int = 1, sentence: str = "") -> dict:
    return {"request": req, "rule": rule, "action": action, "phrase": phrase, "sentence": sentence}


ANSWERS = {
    "special": {"values": [_value("2", "wr-nodocs", "replace", 1, "No documents exist.")]},
    "rfp": {
        "values": [
            _value("1", "wr-nodocs", "replace", 2, "No documents exist."),
            _value("3", "wr-object", "start"),
            {
                "request": "2",
                "rule": "wr-nodocs",
                "action": "conflict",
                "why": "the response says documents will be produced",
            },
        ]
    },
    "form": {"values": []},
}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_firm_wording import _store, _switch

    _switch(tmp_path, monkeypatch, True)
    spec = tmp_path / "spec"
    _store(spec, RULES)
    monkeypatch.setenv("JOBS_SPEC_DIR", str(spec))
    monkeypatch.setattr(versions, "PAUSE_SECONDS", 0)
    return tmp_path


def _units(seat: SetSeat, prior: str = "01JAAAAAAAAAAAAAAAAAAAAAAA") -> list[dict]:
    return [
        {
            "file_id": fid,
            "name": seat.names[fid],
            "size": len(seat.files[fid][0]),
            "sha256": hashlib.sha256(seat.files[fid][0]).hexdigest(),
            "set_type": next((KINDS[k] for k, n in NAMES.items() if n == seat.names[fid]), ""),
            "prior_job_id": prior,
            "folder_id": "folder-0001",
        }
        for fid in seat.names
    ]


def _run(
    world: Path,
    seat: SetSeat,
    answers: dict,
    request: str = "Please apply all new rules to the already prepared responses.",
):
    from drafting_testkit import envelope, make_inputs
    from jobs_testkit import PRICING

    from jobs.drafting.revise.set_run import ReviseSetRun

    jd = world / "jobs" / "01JBBBBBBBBBBBBBBBBBBBBBBB"
    jd.mkdir(parents=True, exist_ok=True)
    units = _units(seat)
    env = {
        **envelope(job_id=jd.name, cls="discovery_response"),
        "request_text": request,
        "slug": "example",
        "month_cents_used": 0,
        "allowance_remaining": 0,
        "revise": {"prior_job_id": units[0]["prior_job_id"], "folder_id": "folder-0001", "units": units},
    }
    (jd / "job.json").write_text(json.dumps(env))
    (world / "pricing.json").write_text(json.dumps(PRICING))
    client = ValuesClient(answers)
    run = ReviseSetRun(
        jd,
        inputs_dir=str(make_inputs(world / "inputs")),
        pricing=str(world / "pricing.json"),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
    )
    return run.run(), client, jd


def _three() -> SetSeat:
    return SetSeat({f"file-{k}": (NAMES[k], response_doc(k)) for k in ("special", "rfp", "form")})


# ---- the whole job ------------------------------------------------------------------------------
def test_every_filed_document_is_revised_by_the_rules_in_place(world) -> None:
    """FALSIFIER: revise one document only, or write a model's words, and the
    second filed document is unchanged or carries text the firm never wrote."""
    seat = _three()
    v, client, jd = _run(world, seat, ANSWERS)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert sorted(client.calls) == ["form", "rfp", "special"]  # one values read per document
    assert seat.sent == ["file-special", "file-rfp"]  # the form set changed nothing: no version filed
    special = text_of(seat.files["file-special"][-1])
    assert special.count(f"Responding party answers item 2. {NO_DOCS}") == 1
    assert special.count("Responding party answers item 1. No documents exist.") == 1  # untouched
    rfp = text_of(seat.files["file-rfp"][-1])
    assert f"Responding party answers item 1. {NEVER}" in rfp
    assert f"{OBJECT} Responding party answers item 3. No documents exist." in rfp
    assert "Responding party answers item 2. No documents exist." in rfp  # the conflict is not applied
    notes = (jd / "data" / "revise-set-notes.md").read_text()
    assert "Response 2: rule wr-nodocs not applied" in notes and "wr-tone" in notes
    assert [f["file_id"] for f in v.proof["files"]] == ["file-special", "file-rfp"]
    assert [f["name"] for f in v.files] == [NAMES["special"], NAMES["rfp"]]
    items = json.loads((jd / "data" / "checklist.json").read_text())["items"]
    assert [i["source"] for i in items] == ["rule:wr-nodocs", "rule:wr-object"]  # rules as code-authored items


def index_doc() -> bytes:
    """A request's Index of Exhibits delivered as a file of its own: no response heading."""
    d = docx.Document()
    d.add_paragraph("SUPERIOR COURT OF THE STATE OF EXAMPLE")
    d.add_paragraph("PLAINTIFF'S INDEX OF EXHIBITS TO RESPONSES TO REQUESTS FOR PRODUCTION")
    for line in ("EXHIBIT A: SAMPLE RECORDS", "1. Records of Example Clinic (Request Nos. 1)",
                 "EXHIBIT B: SAMPLE BILLS", "1. Bills of Example Clinic (Request Nos. 2, 3)"):  # fmt: skip
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_an_index_file_among_the_units_is_kept_and_never_fails_the_request(world) -> None:
    """FALSIFIER: read the index file as a response document and the whole
    request fails revise_base_unproven (4 of 7 real requests, 2026-11-23)."""
    seat = _three()
    seat.names["file-idx"] = "Index of Exhibits, Set One.docx"
    seat.files["file-idx"] = [index_doc()]
    v, client, jd = _run(world, seat, ANSWERS)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent == ["file-special", "file-rfp"] and len(seat.files["file-idx"]) == 1  # kept as filed
    assert sorted(client.calls) == ["form", "rfp", "special"]  # no values asked for the index
    base = json.loads((jd / "data" / "units" / "03" / "base.json").read_text())
    assert base["index"] is True and base["in_scope"] is False
    notes = (jd / "data" / "revise-set-notes.md").read_text()
    assert "An index of exhibits; left as filed" in notes


def test_the_revision_is_made_on_the_staffs_latest_version(world) -> None:
    """The base is the file AS IT IS NOW. FALSIFIER: revise the delivered bytes
    (the unit's sha256) and the staff's edit to response 1 is written over."""
    seat = _three()
    seat.staff_save("file-special", response_doc("special", {"1": "Staff wrote this answer by hand."}))
    v, _c, jd = _run(world, seat, ANSWERS)
    assert v.outcome == "delivered", v.reason
    special = text_of(seat.files["file-special"][-1])
    assert "Staff wrote this answer by hand." in special and f"answers item 2. {NO_DOCS}" in " ".join(special)
    assert json.loads((jd / "data" / "units" / "00" / "base.json").read_text())["newer_than_delivery"] is True


def test_a_save_after_the_base_was_read_is_rebased_never_written_over(world, monkeypatch) -> None:
    """Staff saved again while the revision ran. FALSIFIER: file on the pinned
    version and the save is lost, or hold and the revision never files."""
    from jobs.drafting.revise import set_run

    seat = _three()
    later = response_doc("special", {"3": "Staff answered item three again."})
    real = set_run.ReviseSetRun._manifest_set

    def manifest_then_save(self):
        real(self)
        seat.staff_save("file-special", later)

    monkeypatch.setattr(set_run.ReviseSetRun, "_manifest_set", manifest_then_save)
    v, _c, jd = _run(world, seat, ANSWERS)
    assert v.outcome == "delivered", v.reason
    special = text_of(seat.files["file-special"][-1])
    assert "Staff answered item three again." in special and f"answers item 2. {NO_DOCS}" in " ".join(special)
    assert json.loads((jd / "data" / "units" / "00" / "base.json").read_text())["rebased"] == 1


def test_one_document_refused_files_nothing(world) -> None:
    """All or nothing before filing. FALSIFIER: file each unit as it is made
    and the special responses go out while the production set is refused."""
    seat = _three()
    bad = {"values": [_value("2", "wr-nodocs", "replace", 1, "A sentence the response never had.")]}
    v, client, _jd = _run(world, seat, {**ANSWERS, "rfp": bad})
    assert v.outcome == "failed" and v.reason.startswith("revise_unchecked:"), v.reason
    assert client.calls.count("rfp") == 2  # the one retry, told what failed
    assert seat.sent == []


def test_the_request_names_which_documents(world) -> None:
    """FALSIFIER: ignore the named set and the special responses change too."""
    seat = _three()
    v, client, _jd = _run(world, seat, ANSWERS, request="Please apply the new rules to the production responses.")
    assert v.outcome == "delivered", v.reason
    assert client.calls == ["rfp"] and seat.sent == ["file-rfp"]


def test_no_rule_with_the_firms_words_holds_with_nothing_spent(world, monkeypatch) -> None:
    from test_firm_wording import _store

    _store(world / "spec", [RULES[2]])
    v, client, _jd = _run(world, _three(), ANSWERS)
    assert v.outcome == "held" and v.reason.startswith("revise_unchanged:") and client.calls == []


def test_a_document_whose_headings_disagree_with_its_delivery_is_never_edited(world) -> None:
    seat = SetSeat({"file-special": (NAMES["special"], response_doc("rfp"))})
    v, client, _jd = _run(world, seat, ANSWERS)
    assert v.outcome == "failed" and v.reason.startswith("revise_base_unproven:") and client.calls == []


def test_the_cap_prices_every_document_before_the_first_call(world, monkeypatch) -> None:
    """FALSIFIER: price one document at a time and the third read runs over the cap."""
    from jobs.drafting.revise import set_run

    from jobs.drafting.revise import run as run_mod

    v, _client, jd = _run(world, _three(), ANSWERS)
    est = json.loads((jd / "data" / "revise-estimate.json").read_text())
    assert est["documents"] == 3 and est["calls"] == 6
    # A cap the three documents' reads exceed together stops the job before any read.
    one = est["projected_usd"] - run_mod.FIXED_USD
    monkeypatch.setattr(run_mod, "REVISE_CAP_USD", run_mod.FIXED_USD + one / 2)
    (world / "jobs").rename(world / "jobs-done")
    v, client, _jd = _run(world, _three(), ANSWERS)
    assert v.outcome == "failed" and v.reason.startswith("limit:") and client.calls == []
    assert set_run.STAGES.index("revise-estimate") < set_run.STAGES.index("revise-values")


# ---- the parts ---------------------------------------------------------------------------------
def test_responses_pin_to_request_numbers_and_say_what_changed() -> None:
    from jobs.drafting.revise import prior

    tmp = Path(__import__("tempfile").mkdtemp())
    (tmp / "a.docx").write_bytes(response_doc("form"))
    doc = rs.read(prior.docx_to_md(tmp / "a.docx"))
    assert doc.numbers == ["1.1", "2.1"] and doc.set_type == "form_interrogatories"
    assert rs.numbered_as_served(doc, None) == []
    (tmp / "b.docx").write_bytes(response_doc("special"))
    sp = rs.read(prior.docx_to_md(tmp / "b.docx"))
    assert rs.numbered_as_served(sp, [1, 2, 3]) == [] and rs.numbered_as_served(sp, [1, 2, 3, 4])
    new, _did, _s = rs.apply(sp, [rs.Value("3", "r", "end", "Added.")])
    assert rs.changed(sp.md, new.md) == (["3"], False)
    assert rs.changed(sp.md, sp.md.replace("DATED:", "Signed:"))[1] is True  # outside every response


def test_code_refuses_a_value_it_cannot_place() -> None:
    from jobs.drafting.revise import prior

    tmp = Path(__import__("tempfile").mkdtemp())
    (tmp / "b.docx").write_bytes(response_doc("special"))
    doc = rs.read(prior.docx_to_md(tmp / "b.docx"))
    raw = {
        "values": [
            _value("9", "wr-nodocs", "end"),
            _value("1", "wr-nope", "end"),
            _value("1", "wr-nodocs", "end", phrase=3),
            _value("2", "wr-nodocs", "replace", 1, "Not in it."),
            _value("3", "wr-nodocs", "end", 2),
        ]
    }
    values, _c, problems = rules_mod.check(raw, RULES[:2], doc)
    assert len(problems) == 4 and [v.request for v in values] == ["3"] and values[0].phrase == NEVER


def test_a_rule_item_is_sourced_by_its_rule_id_not_the_request() -> None:
    items = ck.rule_items(RULES[:1])
    assert items[0].source == "rule:wr-nodocs" and items[0].params["phrases"] == [NO_DOCS, NEVER]
    raw = {"items": [i.to_dict() for i in items]}
    assert ck.validate(raw, "apply all new rules", [], RULES) == items
    with pytest.raises(ck.ChecklistError):
        ck.validate(raw, "apply all new rules", [], RULES[1:])  # a rule not in force
    with pytest.raises(ck.ChecklistError):
        ck.validate(raw, "apply all new rules", [])  # no rules given: never accepted


def test_the_units_block_is_rechecked_by_the_runner() -> None:
    unit = {
        "file_id": "file-0001",
        "name": "R.docx",
        "size": 9,
        "sha256": "a" * 64,
        "set_type": "",
        "prior_job_id": "01JAAAAAAAAAAAAAAAAAAAAAAA",
        "folder_id": "folder-0001",
    }
    block = {"prior_job_id": unit["prior_job_id"], "folder_id": "folder-0001", "units": [unit]}
    assert job_mod._revise(block) == block
    for bad in (
        {**block, "units": [unit, unit]},
        {**block, "units": [{**unit, "set_type": "other"}]},
        {**block, "units": []},
    ):
        with pytest.raises(job_mod.DraftingJobError):
            job_mod._revise(bad)


def test_two_drafts_of_one_role_are_each_filed_once(tmp_path) -> None:
    """FALSIFIER: key versions.json by role and the second draft reads as already sent."""
    seat = SetSeat({"a": ("A.docx", b"filed a"), "b": ("B.docx", b"filed b")})
    plan = []
    for fid in ("a", "b"):
        new = tmp_path / f"{fid}.docx"
        new.write_bytes(f"revised {fid}".encode())
        plan.append({"key": f"unit-{fid}", "role": "draft", "file_id": fid, "name": seat.names[fid], "local_path": str(new),
                     "sha256": hashlib.sha256(new.read_bytes()).hexdigest(), "bytes": new.stat().st_size,
                     "base_sha256": hashlib.sha256(seat.files[fid][0]).hexdigest(), "base_version": seat._vid(fid)})  # fmt: skip
    versions.file_versions(seat, "m", plan, tmp_path, lambda m: None, folder_id="folder-0001", pause=0)
    assert seat.sent == ["a", "b"]


def test_the_served_sets_are_kept_with_the_delivered_job_and_purged_with_it(tmp_path) -> None:
    """The served sets used to be purged with the job dir. FALSIFIER: keep only
    form records and a response job leaves nothing a revision can pin to."""
    data = tmp_path / "job" / "data"
    data.mkdir(parents=True)
    (data.parent / "job.json").write_text(json.dumps({"matter_id": "m-1"}))
    sets = [{"set_type": "special_interrogatories", "set_number": 1, "propounding_party": "D", "responding_party": "P",
             "request_count": 2, "request_text": "SPECIAL INTERROGATORY NO. 1: a\nSPECIAL INTERROGATORY NO. 2: b",
             "drafted_by": "operator"}]  # fmt: skip
    (data / "served-plan.json").write_text(json.dumps({"sets": sets}))
    rows = [{"set": "Responses to Special Interrogatories, Set One", "document": NAMES["special"][:-5]}]
    (data / "served-outcomes.json").write_text(json.dumps({"sets": rows}))
    run = tmp_path / "run"
    assert saved.keep(run, "01JAAAAAAAAAAAAAAAAAAAAAAA", data)
    rev = tmp_path / "rev"
    assert saved.hand_all(run, ["01JAAAAAAAAAAAAAAAAAAAAAAA", "01JZZZZZZZZZZZZZZZZZZZZZZZ"], rev) == [
        "01JAAAAAAAAAAAAAAAAAAAAAAA"
    ]
    assert su.served_numbers(su.prior_records(rev), NAMES["special"], "special_interrogatories") == [1, 2]
    assert saved.purge(run, matter="m-1") == ["01JAAAAAAAAAAAAAAAAAAAAAAA.json"]


# ---- the $0 rehearsal on answer keys -------------------------------------------------------------
def _keys(tmp: Path) -> Path:
    """Invented filed responses and their hand-revised keys, as a workspace lays them out."""
    units = tmp / "units"
    for kind, key_answers in (
        ("special", {"2": f"Responding party answers item 2. {NO_DOCS}"}),
        ("rfp", {"1": f"Responding party answers item 1. {NEVER}", "3": f"{OBJECT} Responding party answers item 3. No documents exist."}),
    ):  # fmt: skip
        d = units / kind
        d.mkdir(parents=True)
        (d / "base.docx").write_bytes(response_doc(kind))
        (d / "key.docx").write_bytes(response_doc(kind, key_answers))
    (tmp / "request.txt").write_text("Please apply all new rules to the already prepared responses.")
    (tmp / "wording.json").write_text(json.dumps({"rules": RULES}))
    return units


def _record(units: Path, needed: list[dict], answers: dict) -> None:
    """A recording per needed call, answered as the key's revision was made (the units dir is read sorted)."""
    order = ["rfp", "special"]
    rows = [{"sha": n["sha"], "text": json.dumps(answers[order[i]]), "stop": "end_turn", "in": 1, "out": 1, "stage": n["stage"]}
            for i, n in enumerate(needed)]  # fmt: skip
    (units / "recordings.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _rehearse(tmp: Path, units: Path) -> tuple[int, dict]:
    from jobs.drafting.revise import rehearse

    out = tmp / "out"
    args = ["--units", str(units), "--request", str(tmp / "request.txt"), "--rules", str(tmp / "wording.json")]
    code = rehearse.main([*args, "--out", str(out)])
    return code, json.loads((out / "rehearsal.json").read_text())


def test_the_rehearsal_names_the_calls_it_needs_then_passes_on_the_keys(tmp_path) -> None:
    units = _keys(tmp_path)
    code, rep = _rehearse(tmp_path, units)
    assert code == 1 and len(rep["needed"]) == 2 and all(not d["ok"] for d in rep["documents"])
    _record(units, rep["needed"], ANSWERS)
    code, rep = _rehearse(tmp_path, units)
    assert code == 0, rep
    assert {d["document"]: d["changed"] for d in rep["documents"]} == {"rfp": ["1", "3"], "special": ["2"]}


def test_the_rehearsal_refuses_a_revision_that_changes_a_response_the_key_did_not(tmp_path) -> None:
    """FALSIFIER: pass on 'every key change was made' alone and an extra edit passes."""
    units = _keys(tmp_path)
    _code, rep = _rehearse(tmp_path, units)
    more = [*ANSWERS["special"]["values"], _value("3", "wr-nodocs", "replace", 1, "No documents exist.")]
    _record(units, rep["needed"], {**ANSWERS, "special": {"values": more}})
    code, rep = _rehearse(tmp_path, units)
    assert code == 1
    special = next(d for d in rep["documents"] if d["document"] == "special")
    assert special["ok"] is False and special["changed"] == ["2", "3"] and special["key_changed"] == ["2"]


def test_a_request_naming_kinds_scopes_the_documents() -> None:
    kinds = list(KINDS.values())
    assert su.in_scope("apply the rules to the responses", kinds) == [True, True, True]
    assert su.in_scope("only the special rogs", kinds) == [True, False, False]
    assert su.in_scope("the interrogatories, please", kinds) == [True, False, True]


# ---- 2026-11-21 rehearsal: scope read from an aside; a count read as a new format finding -------
ASIDE = (
    "For the document requests, a new rule: do not list documents already identified elsewhere "
    "(such as in form or special interrogatories)."
)


@pytest.mark.parametrize(
    ("request_text", "want"),
    [
        # the target is named outside the aside: only that document
        (ASIDE, [False, True, False]),
        # no target once the asides are set apart: every filed document
        ("Please fix: do not list documents identified elsewhere (such as in form or special rogs).", [True] * 3),
        ("Do not repeat what was already answered, e.g. in the special interrogatories.", [True] * 3),
        ("Do not restate facts, for example the form interrogatories; apply the rule throughout.", [True] * 3),
        ("Do not list documents already identified in the special interrogatories.", [True] * 3),
        ("Apply the rules (see the note on the form interrogatories [and the special rogs]).", [True] * 3),
        # a target still counts beside an aside
        ("Only the special rogs, please (not the production set).", [True, False, False]),
    ],
)
def test_a_set_named_in_an_aside_is_not_the_document_to_change(request_text: str, want: list[bool]) -> None:
    """FALSIFIER: match set names anywhere in the request and the production
    responses the email asked to fix fall out of scope."""
    assert su.in_scope(request_text, list(KINDS.values())) == want


def test_a_rule_about_document_requests_revises_the_production_responses(world) -> None:
    """The rehearsal's email, in invented words. FALSIFIER: read "(e.g. form
    rogs)" as the target and the production set is never read, the one
    document the email asked to change."""
    seat = _three()
    v, client, _jd = _run(world, seat, ANSWERS, request="Do not list documents identified elsewhere (e.g. form rogs).")
    assert v.outcome == "delivered", v.reason
    assert sorted(client.calls) == ["form", "rfp", "special"] and "file-rfp" in seat.sent


def _split_runs(blob: bytes, starts: str) -> bytes:
    """The paragraph starting ``starts`` as three runs, as staff edits leave a filed file."""
    d = docx.Document(io.BytesIO(blob))
    p = next(p for p in d.paragraphs if p.text.startswith(starts))
    words = p.text.split(" ")
    p.clear()
    for part in (" ".join(words[:2]) + " ", " ".join(words[2:5]) + " ", " ".join(words[5:])):
        p.add_run(part)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def test_a_format_finding_the_filed_file_already_had_is_not_brought_in(world) -> None:
    """A filed file already failing the firm's font rule fails it on fewer
    runs after the edit. FALSIFIER: compare findings with their counts and the
    job refuses with format_check, nothing filed."""
    seat = _three()
    seat.files["file-rfp"][0] = _split_runs(seat.files["file-rfp"][0], "Responding party answers item 1.")
    v, _client, _jd = _run(world, seat, ANSWERS)
    assert v.outcome == "delivered", v.reason
    assert seat.sent == ["file-special", "file-rfp"]


def test_a_format_finding_of_a_new_kind_still_refuses(tmp_path, monkeypatch) -> None:
    """The real check, with one finding of a kind the filed file did not have
    added to the revision's. FALSIFIER: drop the comparison, or compare only
    the part before the colon, and the regression is filed."""
    from drafting_testkit import make_inputs

    from jobs.drafting import firm as firm_mod, format_check

    filed = tmp_path / "filed.docx"
    filed.write_bytes(_split_runs(response_doc("rfp"), "Responding party answers item 1."))
    md, doc = su.read_base(filed, tmp_path)
    picked = {"values": [_value("1", "wr-nodocs", "replace", 1, "No documents exist.")]}
    values, _c, problems = rules_mod.check(picked, RULES, doc)
    assert not problems
    fmt = firm_mod.load(str(make_inputs(tmp_path / "inputs"))).data["format"]
    real = format_check.check

    def check(path: Path, *a: Any, **kw: Any) -> format_check.Result:
        res = real(path, *a, **kw)
        if path != filed:
            res.fails.append("font: a chart run is not the firm's chart size")
        return res

    monkeypatch.setattr(format_check, "check", check)
    with pytest.raises(su.Refused) as err:
        su.revise_one(md, filed, values, tmp_path / "out.docx", "discovery_response", fmt)
    assert err.value.code == "format_check" and "chart run" in err.value.why and "run(s)" not in err.value.why
    assert su.finding_kind("font: 182 run(s) are not Example Serif 12") == su.finding_kind(
        "font: 178 run(s) are not Example Serif 12"
    )

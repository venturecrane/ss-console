"""Revise mode (drafting/revise): an attorney's changes to a filed brief, made
to that brief, checked in code, filed as new versions of the same files.

Every name, number and sentence is invented (Example / Exampletown). No test
makes a model call: the model doorways are scripted, so each test pins what
CODE does with a model's answer, including a wrong one.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from jobs import lane_proof
from jobs.drafting import job as job_mod
from jobs.drafting.outcome import DraftingFailed, DraftingHold
from jobs.drafting.revise import checklist as ck, checks, core, edit, prior, rehearse, sections as sec, versions

BRIEF = """**SUPERIOR COURT OF THE STATE OF EXAMPLE**

| Plaintiff: Pat Example | Case No. CV-0001 |
| --- | --- |

# I. INTRODUCTION

Plaintiff Pat Example was injured on March 3, 2024, when a delivery van struck her car (FILE: Police Report.pdf, p. 2).

# IV. STATEMENT OF FACTS

The van ran a red light at Main and First (FILE: Police Report.pdf, p. 3). A witness saw it (Lee Dep. 12:4-9).

Ms. Example went by ambulance to Exampletown General (FILE: EMS Run Sheet.pdf, p. 1). She was admitted.

She followed up with Dr. Rowe for six months (FILE: Rowe Records.pdf, pp. 4-9). She still has pain.

# V. LIABILITY

A driver who runs a red light is negligent per se. Smith v. Jones (2001) 12 Cal.4th 345, 350. The van's driver ran the light.

# VI. DAMAGES

| Provider | Billed | Paid |
| --- | --- | --- |
| Exampletown General | $41,200.00 | $12,000.00 |
| Dr. Rowe | $8,100.00 | $3,000.00 |

Her past medical specials total $49,300.00 billed. Her future care is expected to continue.

# X. SUMMARY OF CASE VALUE

{{ATTORNEY: settlement authority, the target figure, and any bracket}}

# XI. CONCLUSION

Plaintiff asks the mediator to recognize the strength of her case.
"""

REQUEST = """Thanks. A few changes please:
1. Take out the record cites in the statement of facts.
2. Remove the case citations from the liability section.
3. Shorten the statement of facts to two paragraphs.
4. Break damages into Past Medical and Future Medical subheadings.
5. Double space the whole brief, headings too.
6. Make the table font 10 point.
7. In the conclusion change "recognize the strength of her case" to "value her case fairly".
8. The conclusion should ask for a fair resolution.
9. Add the surgeon's deposition testimony on causation.
"""

KEY = {
    "items": [
        {
            "id": "c1",
            "quote": "Take out the record cites in the statement of facts",
            "kind": "remove_citations",
            "target": "Statement of Facts",
            "params": {"cite_kind": "record"},
        },
        {
            "id": "c2",
            "quote": "Remove the case citations from the liability section",
            "kind": "remove_citations",
            "target": "Liability",
            "params": {"cite_kind": "case"},
        },
        {
            "id": "c3",
            "quote": "Shorten the statement of facts to two paragraphs",
            "kind": "shorten_section",
            "target": "IV. STATEMENT OF FACTS",
            "params": {"max_paragraphs": 2},
        },
        {
            "id": "c4",
            "quote": "Break damages into Past Medical and Future Medical subheadings",
            "kind": "restructure_section",
            "target": "Damages",
            "params": {"subheadings": ["Past Medical", "Future Medical"]},
        },
        {
            "id": "c5",
            "quote": "Double space the whole brief, headings too",
            "kind": "formatting",
            "target": "document",
            "params": {"layout": {"line_spacing": 2.0, "heading_line_spacing": 2.0}},
        },
        {
            "id": "c6",
            "quote": "Make the table font 10 point",
            "kind": "formatting",
            "target": "document",
            "params": {"layout": {"table_font_pt": 10}},
        },
        {
            "id": "c7",
            "quote": 'change "recognize the strength of her case" to "value her case fairly"',
            "kind": "replace_text",
            "target": "Conclusion",
            "params": {"old": "recognize the strength of her case", "new": "value her case fairly"},
        },
        {
            "id": "c8",
            "quote": "The conclusion should ask for a fair resolution",
            "kind": "other",
            "target": "Conclusion",
            "params": {"must_include": ["fair resolution"]},
        },
        {
            "id": "c9",
            "quote": "Add the surgeon's deposition testimony on causation",
            "kind": "other",
            "target": "Liability",
            "params": {},
        },
    ]
}

FACTS_SHORT = """# IV. STATEMENT OF FACTS

The van ran a red light at Main and First. A witness saw it.

Ms. Example went by ambulance to Exampletown General and followed up with Dr. Rowe for six months. She still has pain.

"""
LIABILITY_EDIT = """# V. LIABILITY

A driver who runs a red light is negligent per se. The van's driver ran the light.

"""
DAMAGES_EDIT = """# VI. DAMAGES

## Past Medical

| Provider | Billed | Paid |
| --- | --- | --- |
| Exampletown General | $41,200.00 | $12,000.00 |
| Dr. Rowe | $8,100.00 | $3,000.00 |

Her past medical specials total $49,300.00 billed.

## Future Medical

Her future care is expected to continue.

"""
CONCLUSION_EDIT = """# XI. CONCLUSION

Plaintiff asks the mediator to value her case fairly and to reach a fair resolution.
"""


def scripted_edit(overrides: dict[str, str] | None = None):
    """The edit doorway: the answer for a section, picked by its heading."""
    answers = {
        "IV. STATEMENT OF FACTS": FACTS_SHORT,
        "V. LIABILITY": LIABILITY_EDIT,
        "VI. DAMAGES": DAMAGES_EDIT,
        "XI. CONCLUSION": CONCLUSION_EDIT,
        **(overrides or {}),
    }
    calls: list[str] = []

    def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
        assert stage == "revise-edit"
        section = user.split("THE SECTION AS FILED:\n", 1)[1]
        head = section.splitlines()[0].lstrip("# ").strip()
        calls.append(head)
        return json.dumps({"text": answers[head]})

    ask.calls = calls  # type: ignore[attr-defined]
    return ask


def scripted_read(ask_log: list[str] | None = None):
    def ask(stage: str, system: str, user: str, max_tokens: int) -> str:
        assert stage == "revise-read"
        asks = json.loads(user.split("THE CHANGES ASKED FOR:\n", 1)[1].split("\n\nAS FILED:", 1)[0])
        out = []
        for a in asks:
            met = a["id"] != "c9"  # the surgeon's testimony is nowhere in the brief: not made
            out.append({"id": a["id"], "met": met, "why": "ok" if met else "the brief carries no such testimony"})
        return json.dumps({"items": out, "new_assertions": []})

    return ask


def never(*_: Any) -> str:
    raise AssertionError("no checklist call when the checklist is given")


def items() -> list[ck.Item]:
    return ck.validate(KEY, REQUEST, sec.split(BRIEF))


# ---- sections and the checklist ---------------------------------------------------------
def test_split_join_is_exact_and_finds_sections_by_title() -> None:
    s = sec.split(BRIEF)
    assert sec.join(s) == BRIEF
    assert [x.title for x in sec.find(s, "statement of facts")] == ["IV. STATEMENT OF FACTS"]
    assert len(sec.find(s, "document")) == 6
    assert sec.find(s, "Medical") == []  # names none uniquely: never a guess


def test_the_answer_key_is_a_valid_checklist() -> None:
    assert [i.kind for i in items()].count("formatting") == 2


@pytest.mark.parametrize(
    "mutate, why",
    [
        (lambda k: k["items"][0].update(quote="remove every footnote"), "attorney's own words"),
        (lambda k: k["items"][0].update(kind="rewrite"), "kind is one of"),
        (lambda k: k["items"][0].update(target="Exhibits"), "names no single section"),
        (lambda k: k["items"][4]["params"]["layout"].update(line_spacing=9), "number from"),
        (lambda k: k["items"][4]["params"]["layout"].update(page_color="red"), "is not one of"),
        (lambda k: k["items"][2].update(params={}), "names a limit"),
    ],
)
def test_a_checklist_code_cannot_trust_is_refused(mutate, why) -> None:
    """FALSIFIER: drop a validate rule and its row passes."""
    key = json.loads(json.dumps(KEY))
    mutate(key)
    with pytest.raises(ck.ChecklistError) as e:
        ck.validate(key, REQUEST, sec.split(BRIEF))
    assert why in str(e.value)


def test_a_refused_checklist_is_asked_for_once_more_told_why() -> None:
    seen: list[str] = []

    def ask(stage, system, user, n):
        seen.append(user)
        bad = {"items": [{"id": "c1", "quote": "invented ask", "kind": "other", "target": "Conclusion"}]}
        return json.dumps(bad if len(seen) == 1 else KEY)

    got = core.make_checklist(REQUEST, sec.split(BRIEF), ask)
    assert len(got) == 9 and "REFUSED BY CODE" in seen[1]


# ---- the revision ------------------------------------------------------------------------
def test_a_nine_item_request_is_made_and_checked_and_nothing_else_moves() -> None:
    ed = scripted_edit()
    out = core.revise(BRIEF, REQUEST, checklist_ask=never, edit_ask=ed, read_ask=scripted_read(), items=items())
    assert out.blocking() == []
    st = {s.id: s for s in out.statuses}
    for i in ("c1", "c2", "c3", "c4", "c7"):
        assert (st[i].status, st[i].how) == ("met", "code"), st[i]
    assert st["c8"].status == "met"
    # Needs facts the brief does not carry: flagged for the attorney, never invented, never a stop.
    assert (st["c9"].status, st["c9"].how) == ("not_met", "reading")
    # Code made c1 (record cites) and c7 (the replacement); the model never saw them as asks.
    assert "the record cites" not in json.dumps(ed.calls)
    md = out.md
    for untouched in ("# I. INTRODUCTION", "# X. SUMMARY OF CASE VALUE"):
        a = BRIEF.split(untouched, 1)[1].split("\n# ", 1)[0]
        assert a in md
    # The damages table is byte for byte.
    assert sec.tables(sec.split(BRIEF)[4].body)[0] in md


def test_a_figure_the_brief_does_not_carry_stops_the_filing() -> None:
    """FALSIFIER: drop the new-token invariant and an invented $250,000 files."""
    bad = CONCLUSION_EDIT.replace("fair resolution.", "fair resolution of $250,000.")
    ed = scripted_edit({"XI. CONCLUSION": bad})
    out = core.revise(BRIEF, REQUEST, checklist_ask=never, edit_ask=ed, read_ask=scripted_read(), items=items())
    assert out.rounds == 2  # one re-edit, told why
    assert any("$250,000" in b for b in out.blocking())


def test_a_table_changed_without_an_ask_stops_the_filing() -> None:
    bad = DAMAGES_EDIT.replace("$12,000.00", "$12,500.00")
    out = core.revise(
        BRIEF, REQUEST, checklist_ask=never, edit_ask=scripted_edit({"VI. DAMAGES": bad}), read_ask=None, items=items()
    )
    assert any("a table in 'VI. DAMAGES' changed" in b for b in out.blocking())


def test_a_section_no_item_names_never_changes() -> None:
    prior_s = sec.split(BRIEF)
    edited = [sec.Section(s.id, s.heading, s.level, s.body) for s in prior_s]
    edited[1].body = edited[1].body.replace("delivery van", "truck")
    f = checks.invariants(prior_s, edited, items(), REQUEST)
    assert any("INTRODUCTION" in x and "no item asks" in x for _s, x in f)


def test_an_answer_that_drops_the_heading_leaves_the_section_as_filed() -> None:
    s = sec.split(BRIEF)[5]
    assert edit.accept(s, json.dumps({"text": "A brand new section."})) is None


def test_a_removed_attorney_marker_is_a_finding() -> None:
    prior_s = sec.split(BRIEF)
    edited = [sec.Section(s.id, s.heading, s.level, s.body) for s in prior_s]
    assert edited[5].title == "X. SUMMARY OF CASE VALUE"
    edited[5].body = "\nWe ask for fairness.\n\n"
    key = {
        "items": [
            {
                "id": "c1",
                "quote": "Take out the record cites in the statement of facts",
                "kind": "other",
                "target": "Summary of Case Value",
            }
        ]
    }
    f = checks.invariants(prior_s, edited, ck.validate(key, REQUEST, prior_s), REQUEST)
    assert any("attorney marker" in x for _s, x in f)


# ---- the base: the filed brief, proven -----------------------------------------------------
@pytest.fixture
def renderer(tmp_path: Path):
    from drafting_testkit import make_inputs

    from jobs.drafting import firm as firm_mod, render

    fmt = firm_mod.load(str(make_inputs(tmp_path / "inputs"))).data["format"]

    def r(md: str, out: Path, layout: dict | None = None) -> Path:
        f = json.loads(json.dumps(fmt))
        if layout:
            f.setdefault("layout", {}).setdefault("mediation_brief", {}).update(layout)
        return render.render(md, "mediation_brief", out, f)[0]

    return r


def test_the_filed_docx_reads_back_into_markdown_that_prints_it(tmp_path, renderer) -> None:
    filed = renderer(BRIEF, tmp_path / "filed.docx")
    rec = prior.resolve(filed, [], lambda md, p: renderer(md, p), tmp_path)
    assert rec["source"] == "filed"
    assert prior.prove((tmp_path / "base.md").read_text(), filed, lambda md, p: renderer(md, p), tmp_path) == []


def test_the_prior_jobs_markdown_wins_only_when_it_prints_the_filed_file(tmp_path, renderer) -> None:
    filed = renderer(BRIEF, tmp_path / "filed.docx")
    stale = BRIEF.replace("She still has pain.", "She has recovered.")
    rec = prior.resolve(filed, [("prior_job", stale), ("prior_job2", BRIEF)], lambda md, p: renderer(md, p), tmp_path)
    assert rec["source"] == "prior_job2" and rec["tried"][0].startswith("prior_job:")


def test_a_base_nothing_proves_stops_before_any_edit(tmp_path, renderer) -> None:
    filed = renderer(BRIEF, tmp_path / "filed.docx")
    with pytest.raises(DraftingFailed, match="revise_base_unproven"):
        prior.resolve(filed, [], lambda md, p: renderer(md.replace("pain", "ache"), p), tmp_path)


def test_formatting_asks_are_read_in_the_word_xml(tmp_path, renderer) -> None:
    """The asks are the renderer's firm style keys (#3757), applied as layout
    options and read back in the XML: met when the file carries them, never on
    trust."""
    layout = {"table_font_pt": 10, "heading_line_spacing_pt": 24, "table_header_fill": "D9E2F3"}
    path = renderer(BRIEF, tmp_path / "rev.docx", layout)
    assert checks.check_docx(path, layout, 12, 1) == []
    plain_path = renderer(BRIEF, tmp_path / "plain.docx")
    # The firm's authored body (line_spacing_pt 24, the testkit layout) reads met; 30 pt does not.
    assert checks.check_docx(plain_path, {"line_spacing_pt": 24}, 12, 1) == []
    assert checks.check_docx(plain_path, {"line_spacing_pt": 30}, 12, 1)
    plain = checks.check_docx(plain_path, {"table_font_pt": 10}, 12, 1)
    assert plain, "a file whose tables are not 10 pt must not read as met"


# ---- new versions in place -----------------------------------------------------------------
class VersionSeat:
    """A matter with versioned files: a new version keeps the id and moves versionId."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = {fid: [b] for fid, b in files.items()}
        self.sent: list[str] = []
        self.moved_by_firm: set[str] = set()

    def _vid(self, fid: str) -> str:
        return f"{fid}{len(self.files[fid]):032x}"

    def file_meta(self, matter_id: str, fid: str) -> dict:
        return {
            "id": fid,
            "versionId": self._vid(fid),
            "isDeleted": False,
            "folder": {"id": "folder-0001"},
            "name": {"notes-0001": "Brief - attorney notes", "draft-0001": "Brief"}[fid],
            "fileExtension": ".docx",
            "sizeBytes": len(self.files[fid][-1]),
        }

    def mint(self, matter_id: str, ids: list[str]) -> list[dict]:
        return [{"id": i, "url": f"https://example.invalid/{i}"} for i in ids]

    def fetch(self, url: str, dest: Path, size: Any) -> int:
        blob = self.files[url.rsplit("/", 1)[1]][-1]
        dest.write_bytes(blob)
        return len(blob)

    def add_file_version(self, matter_id, fid, data, *, expected_version, expected_folder=None, expected_name=None):
        assert expected_version == self._vid(fid) and expected_folder == "folder-0001"
        assert expected_name == self.file_meta(matter_id, fid)["name"] + ".docx"
        self.sent.append(fid)
        self.files[fid].append(data)
        return {"fileId": fid}


def _plan(tmp_path: Path, seat: VersionSeat) -> list[dict]:
    out = []
    for role, fid, name in (
        ("draft", "draft-0001", "Brief.docx"),
        ("attorney_notes", "notes-0001", "Brief - attorney notes.docx"),
    ):
        new = tmp_path / f"new-{role}.docx"
        new.write_bytes(f"revised {role}".encode())
        base = seat.files[fid][-1]
        out.append(
            {
                "role": role,
                "file_id": fid,
                "name": name,
                "local_path": str(new),
                "sha256": hashlib.sha256(new.read_bytes()).hexdigest(),
                "bytes": new.stat().st_size,
                "base_sha256": hashlib.sha256(base).hexdigest(),
                "base_version": seat._vid(fid),
            }
        )
    return out


def test_new_versions_go_brief_first_on_the_same_ids_and_read_back(tmp_path) -> None:
    seat = VersionSeat({"draft-0001": b"filed brief", "notes-0001": b"filed notes"})
    plan = _plan(tmp_path, seat)
    proof = versions.file_versions(seat, "m", plan, tmp_path, lambda m: None, folder_id="folder-0001", pause=0)
    assert seat.sent == ["draft-0001", "notes-0001"]
    assert [p["file_id"] for p in proof] == ["draft-0001", "notes-0001"]
    assert all(p["sha256"] == hashlib.sha256(seat.files[p["file_id"]][-1]).hexdigest() for p in proof)
    # A resume after delivery sends nothing twice.
    versions.file_versions(seat, "m", plan, tmp_path, lambda m: None, folder_id="folder-0001", pause=0)
    assert seat.sent == ["draft-0001", "notes-0001"]


def test_a_held_brief_files_no_notes_describing_a_revision_that_did_not_file(tmp_path) -> None:
    """FALSIFIER (D7): file the notes first and a held brief leaves notes
    describing a revision that never reached the matter. Also: drop the
    compare-and-stop and the firm's own edit is buried."""
    seat = VersionSeat({"draft-0001": b"filed brief", "notes-0001": b"filed notes"})
    plan = _plan(tmp_path, seat)
    seat.files["draft-0001"].append(b"the attorney's own edit")
    with pytest.raises(DraftingHold, match="filing_refused"):
        versions.file_versions(seat, "m", plan, tmp_path, lambda m: None, folder_id="folder-0001", pause=0)
    assert seat.sent == []  # the brief held, so its notes were never touched
    assert seat.files["notes-0001"] == [b"filed notes"]


# ---- the envelope and the lane key ---------------------------------------------------------
REVISE = {
    "prior_job_id": "01JAAAAAAAAAAAAAAAAAAAAAAA",
    "folder_id": "folder-0001",
    "files": [{"role": "draft", "file_id": "draft-0001", "name": "Brief.docx", "size": 10, "sha256": "a" * 64}],
}


def test_the_revise_block_is_rechecked_by_the_runner() -> None:
    assert job_mod._revise(REVISE) == REVISE
    for bad in ({**REVISE, "extra": 1}, {**REVISE, "files": [{**REVISE["files"][0], "role": "attorney_notes"}]}):
        with pytest.raises(job_mod.DraftingJobError):
            job_mod._revise(bad)


def test_a_revision_waits_for_its_own_proof_run() -> None:
    env = {"document_class": "mediation_brief"}
    assert lane_proof.lane_key("drafting", env) == "document-drafter.mediation_brief"
    key = lane_proof.lane_key("drafting", {**env, "revise": REVISE})
    assert key == "document-drafter.revise_mediation_brief" and lane_proof.KEY_RE.match(key)


# ---- the $0 rehearsal ----------------------------------------------------------------------
def test_the_rehearsal_passes_the_hand_revision_and_refuses_a_bad_one(tmp_path) -> None:
    (tmp_path / "base.md").write_text(BRIEF)
    good = sec.join(
        core.revise(BRIEF, REQUEST, checklist_ask=never, edit_ask=scripted_edit(), read_ask=None, items=items()).edited
    )
    (tmp_path / "good.md").write_text(good)
    key = {"items": [i for i in KEY["items"] if i["kind"] != "formatting"]}
    rep = rehearse.rehearse(REQUEST, tmp_path / "base.md", tmp_path / "good.md", key)
    assert rep["ok"], rep
    (tmp_path / "bad.md").write_text(good.replace("A witness saw it.", "Three witnesses saw it."))
    assert not rehearse.rehearse(REQUEST, tmp_path / "base.md", tmp_path / "bad.md", key)["ok"]


# ---- the whole job, off the seat -----------------------------------------------------------
class RunSeat(VersionSeat):
    def matter_number(self, matter_id: str) -> str | None:
        from demand_testkit import MATTER

        return "100001" if matter_id == MATTER else None


class RunClient:
    """The Anthropic client the doorway calls: answers by the system prompt."""

    def __init__(self, edit_ask, read_ask, checklist: dict) -> None:
        from types import SimpleNamespace

        self.edit, self.read, self.checklist, self.stages = edit_ask, read_ask, checklist, []
        self.messages = SimpleNamespace(create=self._create, stream=None)

    def _create(self, **params: Any) -> Any:
        from types import SimpleNamespace

        system = json.dumps(params.get("system"))
        user = params["messages"][-1]["content"]
        user = user if isinstance(user, str) else user[0]["text"]
        if "into a checklist" in system:
            self.stages.append("checklist")
            text = json.dumps(self.checklist)
        elif "You revise ONE section" in system:
            self.stages.append("edit")
            text = self.edit("revise-edit", "", user, 0)
        else:
            self.stages.append("read")
            text = self.read("revise-read", "", user, 0)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)


def test_a_revision_runs_off_the_seat_to_new_versions_on_the_same_ids(tmp_path, renderer) -> None:
    import io

    import docx
    from demand_testkit import MATTER
    from drafting_testkit import envelope
    from jobs_testkit import PRICING

    from jobs.demand import deliver
    from jobs.drafting.revise.run import ReviseRun

    filed = renderer(BRIEF, tmp_path / "filed.docx").read_bytes()
    notes_md = "# Attorney notes\n\nThe filed draft's notes.\n"
    notes = deliver.render_plain(notes_md, tmp_path, "n.docx", "Example").read_bytes()
    seat = RunSeat({"draft-0001": filed, "notes-0001": notes})
    files = [
        {
            "role": "draft",
            "file_id": "draft-0001",
            "name": "Brief.docx",
            "size": len(filed),
            "sha256": hashlib.sha256(filed).hexdigest(),
        },
        {
            "role": "attorney_notes",
            "file_id": "notes-0001",
            "name": "Brief - attorney notes.docx",
            "size": len(notes),
            "sha256": hashlib.sha256(notes).hexdigest(),
        },
    ]
    jd = tmp_path / "jobs" / "01JBBBBBBBBBBBBBBBBBBBBBBB"
    jd.mkdir(parents=True)
    env = {
        **envelope(job_id=jd.name),
        "request_text": REQUEST,
        "slug": "example",
        "month_cents_used": 0,
        "allowance_remaining": 0,
        "revise": {**REVISE, "files": files},
    }
    assert env["matter_id"] == MATTER
    (jd / "job.json").write_text(json.dumps(env))
    (tmp_path / "pricing.json").write_text(json.dumps(PRICING))
    key = {"items": [i for i in KEY["items"] if i["kind"] != "formatting"]}
    client = RunClient(scripted_edit(), scripted_read(), key)
    r = ReviseRun(
        jd,
        inputs_dir=str(tmp_path / "inputs"),
        pricing=str(tmp_path / "pricing.json"),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
    )
    v = r.run()
    assert v.outcome == "delivered", (v.stage, v.reason)
    # It never pulled the matter file: a checklist, the edited sections, their readings.
    assert set(client.stages) == {"checklist", "edit", "read"}
    assert seat.sent == ["draft-0001", "notes-0001"]
    assert v.proof["mode"] == "revise" and [f["file_id"] for f in v.proof["files"]] == ["draft-0001", "notes-0001"]
    assert v.folder_id == REVISE["folder_id"] and v.dollars < 1.0
    text = "\n".join(p.text for p in docx.Document(io.BytesIO(seat.files["draft-0001"][-1])).paragraphs)
    assert "value her case fairly" in text
    assert "FILE:" not in text.split("STATEMENT OF FACTS")[1].split("LIABILITY")[0]
    ntext = "\n".join(p.text for p in docx.Document(io.BytesIO(seat.files["notes-0001"][-1])).paragraphs)
    assert "Not made, and why" in ntext and "c9" in ntext and "The filed draft's notes." in ntext

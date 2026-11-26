"""A revision of the propounded sets a request filed (revise/propounded_run.py,
2026-11-25): the attorney's define, add and cut made in each filed set, as new
versions of the same files, every change decided by code.

Driven through the real classes: ``PropoundedSetRun`` over a versioned seat,
the real doorway over a scripted client, the real in-place editor and its
proof. No model is called ($0). Every name, number and sentence is invented
(Example / Exampletown). Each test names the line it defends.
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

from jobs.drafting import set_items
from jobs.drafting.revise import propounded_ask as pa, versions

SROG = "special_interrogatories"
RFA = "requests_for_admission"
RFP = "requests_for_production"
LABEL = {SROG: "SPECIAL INTERROGATORY", RFA: "REQUEST FOR ADMISSION", RFP: "REQUEST FOR PRODUCTION"}
NAMES = {
    SROG: "Special Interrogatories, Set One.docx",
    RFA: "Requests for Admission, Set One.docx",
    RFP: "Requests for Production, Set One.docx",
}
DEFENDANT = "Defendant DELTA EXAMPLE"


def set_doc(
    variant: str = SROG,
    n: int = 4,
    party: str = DEFENDANT,
    decl: str | None = None,
    word_numbered: bool = False,
) -> bytes:
    """A filed propounded set: a caption, definitions, each request under its bold label."""
    d = docx.Document()
    d.add_paragraph("SUPERIOR COURT OF THE STATE OF EXAMPLE")
    d.add_paragraph("PROPOUNDING PARTY: Plaintiff ALPHA EXAMPLE")
    d.add_paragraph(f"RESPONDING PARTY: {party}")
    d.add_paragraph("SET NO.: One")
    d.add_paragraph().add_run("DEFINITIONS").bold = True
    d.add_paragraph('1. "INCIDENT" means the collision on June 1, 2026 in Exampletown.')
    d.add_paragraph('2. "YOU" means the responding party.')
    d.add_paragraph().add_run(set_items.NAMES[variant][1].upper()).bold = True
    for k in range(1, n + 1):
        if word_numbered:  # Word numbers the label itself: the text carries no number
            d.add_paragraph(style="List Number").add_run(f"{LABEL[variant]} NO.:").bold = True
        else:
            d.add_paragraph().add_run(f"{LABEL[variant]} NO. {k}:").bold = True
        d.add_paragraph(f"State fact number {k} about the INCIDENT.")
    d.add_paragraph("Dated: December 1, 2026")
    if decl:
        d.add_paragraph().add_run("DECLARATION FOR ADDITIONAL DISCOVERY").bold = True
        d.add_paragraph(decl)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def text_of(blob: bytes) -> list[str]:
    return [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs]


# ---- the seat ---------------------------------------------------------------------------------
class PropSeat:
    """A matter with versioned files, each in its own folder; a staff save is a new version."""

    def __init__(self, files: dict[str, tuple[str, bytes, str]]) -> None:
        self.names = {fid: name for fid, (name, _b, _f) in files.items()}
        self.folders = {fid: folder for fid, (_n, _b, folder) in files.items()}
        self.files = {fid: [b] for fid, (_n, b, _f) in files.items()}
        self.sent: list[str] = []
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
            "folder": {"id": self.folders[fid]},
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
        assert expected_folder == self.folders[fid] and expected_name == self.names[fid]
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


class ScriptClient:
    """The Anthropic client the doorway calls: each stage's answer picked by its system prompt."""

    def __init__(
        self, plan: Any, definition: str = "", items: list[str] | None = None, ranking: Any = None, follow: Any = None
    ):
        self.answers = {
            pa.PLAN_SYSTEM: plan,
            pa.DEFINE_SYSTEM: {"definition": definition},
            pa.ADD_SYSTEM: {"items": items or []},
            pa.FOLLOW_SYSTEM: follow,
            pa.CUT_SYSTEM: ranking,
        }
        self.calls: list[str] = []
        #: Called before each answer with its stage: a test that saves a staff version mid-job.
        self.before: Callable[[str], None] = lambda stage: None
        self.messages = SimpleNamespace(create=self._create, stream=None)

    def _create(self, **params: Any) -> Any:
        system = params["system"]
        system = system if isinstance(system, str) else system[0]["text"]
        key = next(s for s in self.answers if s in system)
        stage = {
            pa.PLAN_SYSTEM: "plan",
            pa.DEFINE_SYSTEM: "define",
            pa.ADD_SYSTEM: "add",
            pa.FOLLOW_SYSTEM: "follow",
        }.get(key, "cut")
        self.before(stage)
        self.calls.append(stage)
        got = self.answers[key]
        user = params["messages"][-1]["content"]
        user = user if isinstance(user, str) else user[0]["text"]
        text = json.dumps(got(user) if callable(got) else got)
        usage = SimpleNamespace(
            input_tokens=1000, output_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=0
        )
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", usage=usage)


def op(kind: str, quote: str, number: str = "", term: str = "", types=(), party: str = "") -> dict:
    return {
        "op": kind,
        "set_types": list(types),
        "party_quote": party,
        "term": term,
        "number_quote": number,
        "quote": quote,
    }


def rank_all_but(*first: int) -> Callable[[str], dict]:
    """A ranking naming every request in the set, ``first`` least important."""

    def answer(user: str) -> dict:
        nums = sorted({int(n) for n in __import__("re").findall(r"INTERROGATORY NO\. (\d+)", user)})
        return {"ranking": [*first, *[n for n in nums if n not in first]]}

    return answer


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(versions, "PAUSE_SECONDS", 0)
    return tmp_path


def _units(seat: PropSeat, hinted: bool = True, prior: dict[str, str] | None = None) -> list[dict]:
    return [
        {
            "file_id": fid,
            "name": seat.names[fid],
            "size": len(seat.files[fid][0]),
            "sha256": hashlib.sha256(seat.files[fid][0]).hexdigest(),
            "set_type": next((k for k, n in NAMES.items() if n == seat.names[fid]), "") if hinted else "",
            "prior_job_id": (prior or {}).get(fid, "01JAAAAAAAAAAAAAAAAAAAAAAA"),
            "folder_id": seat.folders[fid],
        }
        for fid in seat.names
    ]


def _run(
    world: Path,
    seat: PropSeat,
    client: ScriptClient,
    request: str,
    job_id: str = "01JBBBBBBBBBBBBBBBBBBBBBBB",
    units: list[dict] | None = None,
    notes: str | None = None,
):
    from drafting_testkit import envelope, make_inputs
    from jobs_testkit import PRICING

    from jobs.drafting.revise.propounded_run import PropoundedSetRun

    jd = world / "jobs" / job_id
    jd.mkdir(parents=True, exist_ok=True)
    units = units or _units(seat)
    revise: dict[str, Any] = {
        "prior_job_id": units[0]["prior_job_id"],
        "folder_id": units[0]["folder_id"],
        "units": units,
    }
    if notes:
        blob = seat.files[notes][0]
        revise["notes"] = {
            "file_id": notes,
            "name": seat.names[notes],
            "size": len(blob),
            "sha256": hashlib.sha256(blob).hexdigest(),
            "folder_id": seat.folders[notes],
        }
    env = {
        **envelope(job_id=jd.name, cls="discovery_set"),
        "request_text": request,
        "slug": "example",
        "month_cents_used": 0,
        "allowance_remaining": 0,
        "revise": revise,
    }
    (jd / "job.json").write_text(json.dumps(env))
    (world / "pricing.json").write_text(json.dumps(PRICING))
    run = PropoundedSetRun(
        jd,
        inputs_dir=str(make_inputs(world / "inputs")),
        pricing=str(world / "pricing.json"),
        seat_factory=lambda: seat,
        client=client,
        log=lambda m: None,
    )
    return run.run(), run


def _one(variant: str = SROG, **kw: Any) -> PropSeat:
    return PropSeat({"file-0001": (NAMES[variant], set_doc(variant, **kw), "folder-0001")})


def _labels(blob: bytes, variant: str = SROG) -> list[str]:
    return [t for t in text_of(blob) if t.startswith(LABEL[variant])]


# ---- the whole job ------------------------------------------------------------------------------
def test_define_add_and_cut_are_made_in_the_filed_set_as_a_new_version(world) -> None:
    """FALSIFIER: write the model's numbers, or file a new document, and the
    set's labels are not 1..n in order or the file is not the filed one."""
    email = "Please define VEHICLE, add two more, and cut by 3."
    seat = _one()
    client = ScriptClient(
        {"ops": [op("define", "define VEHICLE", term="VEHICLE"), op("add", "add two more", "two more"),
                 op("cut", "cut by 3", "by 3")], "other": []},
        '"VEHICLE" means the 2020 Example sedan.',
        ["Describe the VEHICLE's damage.", "Identify every passenger in the VEHICLE."],
        rank_all_but(2, 3, 5),
    )  # fmt: skip
    v, run = _run(world, seat, client, email)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent == ["file-0001"]
    new = seat.files["file-0001"][-1]
    assert _labels(new) == [f"SPECIAL INTERROGATORY NO. {k}:" for k in (1, 2, 3)]
    body = text_of(new)
    assert body[body.index("DEFINITIONS") + 2 : body.index("DEFINITIONS") + 4] == [
        '2. "VEHICLE" means the 2020 Example sedan.',
        '3. "YOU" means the responding party.',
    ]
    assert "Identify every passenger in the VEHICLE." in body
    assert "State fact number 2 about the INCIDENT." not in body
    assert client.calls == ["plan", "add", "define", "cut"]
    changes = v.proof["changes"]
    assert changes[0] == f"{NAMES[SROG]} (4 special interrogatories, now 3):"
    assert any("former No. 4 is now No. 2" in ln for ln in changes)
    assert v.proof["mode"] == "revise"
    (line,) = v.proof["set_lines"]  # the reply's one line per set (drafting_lane carries it)
    assert v.proof["sets_left"] == []
    assert line.startswith(f'"{NAMES[SROG]}" (4 special interrogatories, now 3): Added the definition of "VEHICLE".')


def test_a_resume_asks_the_model_nothing_twice(world) -> None:
    """FALSIFIER: keep the answers in memory only, and a resumed job pays twice."""
    email = "Please add two more."
    seat = _one()
    client = ScriptClient(
        {"ops": [op("add", "add two more", "two more")], "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )
    v, run = _run(world, seat, client, email)
    assert v.outcome == "delivered", (v.stage, v.reason)
    kept = json.loads((run.data / "propounded-answers.json").read_text())
    assert kept == {
        f"additions:{NAMES[SROG]}#0": ["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."]
    }
    assert client.calls == ["plan", "add"]


def test_an_ask_that_is_none_of_the_three_holds_for_smd_and_asks_nothing_more(world) -> None:
    """FALSIFIER: let a rewording through, and code edits what no op names."""
    email = "Can you reword No. 2 to be about the driver?"
    seat = _one()
    client = ScriptClient({"ops": [], "other": [{"quote": "reword No. 2 to be about the driver"}]})
    v, _run_ = _run(world, seat, client, email)
    assert (v.outcome, v.reason.split(":")[0]) == ("held", "revise_set_unsupported")
    assert client.calls == ["plan"] and seat.sent == []


def test_a_party_the_email_names_that_no_set_is_served_on_holds_for_smd(world) -> None:
    """FALSIFIER: apply to every set when the party matches none, and the wrong
    defendant's set is cut."""
    email = "Cut the special interrogatories to Defendant OMEGA EXAMPLE by 2."
    seat = _one()
    client = ScriptClient(
        {"ops": [op("cut", "Cut the special interrogatories to Defendant OMEGA EXAMPLE by 2", "by 2",
                    types=[SROG], party="Defendant OMEGA EXAMPLE")], "other": []}
    )  # fmt: skip
    v, _r = _run(world, seat, client, email)
    assert (v.outcome, v.reason.split(":")[0]) == ("held", "revise_scope_unclear")
    assert client.calls == ["plan"] and seat.sent == []


def test_a_cut_across_35_holds_before_any_write_call(world) -> None:
    """FALSIFIER: cut first and check after, and the model is paid for a set
    whose declaration code then cannot settle."""
    email = "Cut these down to 30."
    seat = _one(n=38, decl="Plaintiff is propounding 38 special interrogatories.")
    client = ScriptClient({"ops": [op("cut", "Cut these down to 30", "down to 30")], "other": []})
    v, _r = _run(world, seat, client, email)
    assert (v.outcome, v.reason.split(":")[0]) == ("held", "revise_declaration")
    assert client.calls == ["plan"] and seat.sent == []


def test_the_declaration_count_is_rewritten_in_the_same_file(world) -> None:
    """FALSIFIER: leave the declaration's count, and the filed set misstates its size."""
    email = "Cut these down to 36."
    seat = _one(n=38, decl="Plaintiff is propounding 38 special interrogatories.")
    client = ScriptClient(
        {"ops": [op("cut", "Cut these down to 36", "down to 36")], "other": []}, ranking=rank_all_but(37, 38)
    )
    v, _r = _run(world, seat, client, email)
    assert v.outcome == "delivered", (v.stage, v.reason)
    body = text_of(seat.files["file-0001"][-1])
    assert "Plaintiff is propounding 36 special interrogatories." in body
    assert len(_labels(seat.files["file-0001"][-1])) == 36


def test_word_numbered_labels_are_unproven_and_never_edited(world) -> None:
    """FALSIFIER: read a set whose numbers Word draws as zero requests, and a
    cut removes the wrong ones or an add numbers from 1."""
    seat = _one(word_numbered=True)
    client = ScriptClient({"ops": [op("add", "add two more", "two more")], "other": []})
    v, _r = _run(world, seat, client, "Please add two more.")
    assert (v.outcome, v.reason.split(":")[0]) == ("failed", "revise_base_unproven")
    assert client.calls == [] and seat.sent == []


def test_a_hand_filed_set_reads_its_type_from_its_name_and_party_from_the_document(world) -> None:
    """FALSIFIER: require the delivery's set type, and every set SMD filed by
    hand (no served sets on its record) is refused."""
    email = "Please add two more."
    seat = _one()
    client = ScriptClient(
        {"ops": [op("add", "add two more", "two more")], "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )
    v, run = _run(world, seat, client, email, units=_units(seat, hinted=False))
    assert v.outcome == "delivered", (v.stage, v.reason)
    base = json.loads((run.data / "units" / "00" / "base.json").read_text())
    assert (base["set_type"], base["party"]) == (SROG, DEFENDANT)
    assert len(_labels(seat.files["file-0001"][-1])) == 6


def test_two_jobs_files_in_one_thread_are_each_filed_in_their_own_folder(world) -> None:
    """FALSIFIER: file every set in the first job's folder, and the second
    defendant's set lands beside the wrong one."""
    other = "Defendant OMEGA EXAMPLE"
    seat = PropSeat({
        "file-0001": (NAMES[SROG], set_doc(SROG), "folder-0001"),
        "file-0002": ("Special Interrogatories to Omega, Set One.docx", set_doc(SROG, party=other), "folder-0002"),
    })  # fmt: skip
    units = _units(seat, prior={"file-0002": "01JCCCCCCCCCCCCCCCCCCCCCCC"})
    units[1]["set_type"] = SROG
    email = "Add two more to Defendant OMEGA EXAMPLE."
    client = ScriptClient(
        {"ops": [op("add", "Add two more to Defendant OMEGA EXAMPLE", "two more", party="Defendant OMEGA EXAMPLE")],
         "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )  # fmt: skip
    v, _r = _run(world, seat, client, email, units=units)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent == ["file-0002"]
    assert len(_labels(seat.files["file-0002"][-1])) == 6 and len(seat.files["file-0001"]) == 1
    assert any("left as filed; the email does not ask to change it" in ln for ln in v.proof["changes"])
    assert len(v.proof["set_lines"]) == 1 and v.proof["sets_left"] == [
        f'"{NAMES[SROG]}": the email does not ask to change it.'
    ]


def test_two_revise_jobs_on_the_same_file_each_revise_the_version_before_them(world) -> None:
    """The daemon runs one job per lane at a time (``daemon.py``: "One job at
    a time per lane, oldest first"), so two revisions of one file run in turn;
    the second reads the file as the first filed it. FALSIFIER: revise the
    delivered copy, and the second job's version undoes the first's cut."""
    seat = _one()
    first = ScriptClient({"ops": [op("cut", "cut by 1", "by 1")], "other": []}, ranking=rank_all_but(4))
    v1, _r = _run(world, seat, first, "Please cut by 1.")
    assert v1.outcome == "delivered", (v1.stage, v1.reason)
    second = ScriptClient({"ops": [op("cut", "cut by 1", "by 1")], "other": []}, ranking=rank_all_but(3))
    v2, run2 = _run(world, seat, second, "Please cut by 1.", job_id="01JDDDDDDDDDDDDDDDDDDDDDDD", units=_units(seat))
    assert v2.outcome == "delivered", (v2.stage, v2.reason)
    assert len(seat.files["file-0001"]) == 3 and len(_labels(seat.files["file-0001"][-1])) == 2
    assert json.loads((run2.data / "units" / "00" / "base.json").read_text())["version"] == "file-0001-v2"


def test_a_staff_save_mid_job_that_changes_the_set_holds_and_writes_nothing(world) -> None:
    """FALSIFIER: replay the edit on the staff's version, and their words are overwritten."""
    seat = _one()
    client = ScriptClient(
        {"ops": [op("add", "add two more", "two more")], "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )
    client.before = lambda stage: seat.staff_save("file-0001", set_doc(SROG, n=5)) if stage == "add" else None
    v, _r = _run(world, seat, client, "Please add two more.")
    assert (v.outcome, (v.reason or "").split(":")[0]) == ("held", "filing_refused"), (v.outcome, v.stage, v.reason)
    assert seat.sent == [] and len(seat.files["file-0001"]) == 2


def test_over_the_cap_fails_before_any_call(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: price after the plan call, and an oversized set is paid for."""
    monkeypatch.setattr(pa, "plan_chars", lambda email, mds: 10**12)
    seat = _one()
    client = ScriptClient({"ops": [op("add", "add two more", "two more")], "other": []})
    v, _r = _run(world, seat, client, "Please add two more.")
    assert v.outcome == "failed" and client.calls == [] and seat.sent == []


def test_follow_ups_are_filed_after_their_parents(world) -> None:
    """D9 through the runner. FALSIFIER: an add that asks for follow-ups written
    at the end, or not at all, and the attorney's reply is not delivered."""
    seat = _one()
    client = ScriptClient(
        {"ops": [op("add", "Please add follow-up questions")], "other": []},
        follow={
            "items": [{"after": 2, "text": "Identify each witness to it."}, {"after": 2, "text": "And each document."}]
        },
    )
    v, _r = _run(world, seat, client, "Please add follow-up questions.")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert client.calls == ["plan", "follow"]
    paras = text_of(seat.files["file-0001"][-1])
    at = {t: i for i, t in enumerate(paras)}
    assert len(_labels(seat.files["file-0001"][-1])) == 6
    two, three = at["State fact number 2 about the INCIDENT."], at["State fact number 3 about the INCIDENT."]
    assert two < at["Identify each witness to it."] < at["And each document."] < three


def test_a_file_citing_a_renumbered_set_is_filed_again_in_the_same_job(world) -> None:
    """FALSIFIER: renumber the admissions and leave the interrogatories, and an
    interrogatory filed with them asks about the wrong request."""
    srog = docx.Document(io.BytesIO(set_doc(SROG, n=2)))
    for para in srog.paragraphs:
        if para.text == "State fact number 2 about the INCIDENT.":
            para.runs[0].text = "State all facts for your denial of Request for Admission No. 4."
    buf = io.BytesIO()
    srog.save(buf)
    seat = PropSeat({
        "file-0001": (NAMES[SROG], buf.getvalue(), "folder-0001"),
        "file-0002": (NAMES[RFA], set_doc(RFA, n=4), "folder-0001"),
    })  # fmt: skip
    email = "Cut the requests for admission by 2."
    client = ScriptClient(
        {"ops": [op("cut", "Cut the requests for admission by 2", "by 2", types=[RFA])], "other": []},
        ranking={"ranking": [1, 2, 3, 4]},
    )
    v, _r = _run(world, seat, client, email)
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert sorted(seat.sent) == ["file-0001", "file-0002"]
    assert "State all facts for your denial of Request for Admission No. 2." in text_of(seat.files["file-0001"][-1])
    assert any("follow the new numbering (No. 4 is now No. 2)" in ln for ln in v.proof["changes"])


def test_the_edits_are_priced_after_the_plan_before_any_write_call(world, monkeypatch: pytest.MonkeyPatch) -> None:
    """FALSIFIER: price one definition per job, and a plan defining many terms
    on many sets runs past the cap."""
    monkeypatch.setattr(pa, "edit_chars", lambda email, mds, ops, scoped: 10**12)
    seat = _one()
    client = ScriptClient({"ops": [op("add", "add two more", "two more")], "other": []})
    v, _r = _run(world, seat, client, "Please add two more.")
    assert v.outcome == "failed" and client.calls == ["plan"] and seat.sent == []


def test_the_dispatcher_sends_a_propounded_set_with_units_to_this_runner() -> None:
    from jobs.drafting.revise.propounded_run import PropoundedSetRun, runner_for
    from jobs.drafting.revise.run import ReviseRun
    from jobs.drafting.revise.set_run import ReviseSetRun
    from jobs.drafting.run import DraftingRun

    assert runner_for({"units": []}, "discovery_set") is PropoundedSetRun
    assert runner_for({"units": []}, "discovery_response") is ReviseSetRun
    assert runner_for({"prior_job_id": "x"}, "discovery_set") is ReviseRun
    assert runner_for(None, "discovery_set") is DraftingRun


def _record_until_answered(units: Path, request: str, answers: dict[str, Any]) -> dict[str, Any]:
    """Run the rehearsal, answering each call it lists as needing a recording
    from ``answers`` by stage, until it needs none: how a recording is made."""
    from jobs.drafting.revise import propounded_rehearse as pr

    rec = units / "recordings.jsonl"
    for _ in range(6):
        rep = pr.rehearse(units, request, rec)
        if not rep["needed"]:
            return rep
        with rec.open("a", encoding="utf-8") as fh:
            for n in rep["needed"]:
                fh.write(json.dumps({"sha": n["sha"], "text": json.dumps(answers[n["stage"]])}) + "\n")
    raise AssertionError("the rehearsal kept needing recordings")


def test_the_rehearsal_passes_when_the_revision_equals_the_hand_revised_key(tmp_path: Path) -> None:
    """FALSIFIER: score the count alone, and a cut of the wrong requests passes."""
    units = tmp_path / "units"
    (units / "Special Interrogatories, Set One").mkdir(parents=True)
    d = units / "Special Interrogatories, Set One"
    (d / "base.docx").write_bytes(set_doc(SROG, n=4))
    key = docx.Document(io.BytesIO(set_doc(SROG, n=3)))
    for p in key.paragraphs:  # the key keeps former 1, 2 and 4: No. 3 is "fact number 4"
        if p.text == "State fact number 3 about the INCIDENT.":
            p.runs[0].text = "State fact number 4 about the INCIDENT."
    buf = io.BytesIO()
    key.save(buf)
    (d / "key.docx").write_bytes(buf.getvalue())
    request = "Please cut by 1."
    answers = {
        "revise-plan": {"ops": [op("cut", "cut by 1", "by 1")], "other": []},
        "revise-cut": {"ranking": [3, 1, 2, 4]},
    }
    rep = _record_until_answered(units, request, answers)
    assert rep["ok"] and rep["request"]["outcome"] == "delivered", rep
    answers["revise-cut"] = {"ranking": [4, 1, 2, 3]}
    (units / "recordings.jsonl").unlink()
    rep = _record_until_answered(units, request, answers)
    assert not rep["ok"] and "first differing No. [3]" in rep["documents"][0]["why"], rep


def test_a_staff_save_that_leaves_the_set_as_read_is_rebased_and_filed(world) -> None:
    """FALSIFIER: refuse every new version, and a re-save with no change holds the job."""
    seat = _one()
    client = ScriptClient(
        {"ops": [op("add", "add two more", "two more")], "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )
    client.before = lambda stage: seat.staff_save("file-0001", seat.files["file-0001"][0]) if stage == "add" else None
    v, run = _run(world, seat, client, "Please add two more.")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent == ["file-0001"] and len(_labels(seat.files["file-0001"][-1])) == 6
    assert json.loads((run.data / "units" / "00" / "base.json").read_text())["rebased"] == 1
    assert client.calls == ["plan", "add"]  # the kept answers re-made the edit; nothing paid twice


def test_the_attorney_notes_get_a_new_version_naming_what_changed(world) -> None:
    """FALSIFIER: file the sets and leave the notes, and the firm's notes keep the old count."""
    notes = docx.Document()
    notes.add_paragraph("Attorney notes for the special interrogatories.")
    buf = io.BytesIO()
    notes.save(buf)
    seat = PropSeat({
        "file-0001": (NAMES[SROG], set_doc(SROG), "folder-0001"),
        "notes-0001": ("Attorney Notes.docx", buf.getvalue(), "folder-0001"),
    })  # fmt: skip
    units = [u for u in _units(seat) if u["file_id"] == "file-0001"]
    client = ScriptClient(
        {"ops": [op("add", "add two more", "two more")], "other": []},
        items=["Describe the INCIDENT's cause.", "Identify every witness to the INCIDENT."],
    )
    v, _r = _run(world, seat, client, "Please add two more.", units=units, notes="notes-0001")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert seat.sent == ["file-0001", "notes-0001"]
    text = " ".join(text_of(seat.files["notes-0001"][-1]))
    assert "Added 2 special interrogatories, Nos. 5, 6." in text and "Attorney notes for the special" in text


SET_LINES_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
out = [{"unit": "drafting", "kind": "drafting", "outcome": "delivered", "stage": "file", "reason": None,
        "dollars": 1.0, "document_class": "discovery_set", "folder_id": "folder-9",
        "files": [{"name": "A.docx", "size": 10, "role": "draft"}],
        "proof": {"readback": "confirmed", "files": [{"name": "A.docx", "file_id": "f1", "size": 10,
                  "sha256": "a" * 64}], "review": "passed", "review_cents": 0,
                  "set_lines": ['"A.docx" (40 special interrogatories, now 38): Removed former Nos. 4, 9.'],
                  "sets_left": ['"B.docx": the email does not ask to change it.']}}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""


def test_the_lane_records_a_propounded_revisions_one_line_per_set(tmp_path):
    """FALSIFIER: leave ``set_lines`` in the proof, and the reply has nothing to
    list: the attorney is told only that new versions were filed."""
    import test_drafting_lane as dl

    lane, client = dl._lane(tmp_path, script=SET_LINES_RUNNER)
    dl._submit(lane, client)
    assert lane.tick() == "delivered"
    delivery = client.records[-1][2]["delivery"]
    assert delivery["set_lines"] == ['"A.docx" (40 special interrogatories, now 38): Removed former Nos. 4, 9.']
    assert delivery["sets_left"] == ['"B.docx": the email does not ask to change it.']
    assert "uploads_started" not in delivery


def test_a_brokers_propounded_revise_block_loads_and_takes_the_propounded_runner():
    """The broker's block (``drafting_revise_units.collect``) passes the job's own
    check and picks the propounded runner. FALSIFIER: a key the job refuses, and
    every routed reply fails at load."""
    from jobs.drafting import job as drafting_job
    from jobs.drafting.revise.propounded_run import PropoundedSetRun, runner_for

    unit = {
        "file_id": "file-set-0000", "name": "Special Interrogatories, Set One.docx", "size": 3000,
        "sha256": "e" * 64, "set_type": "special_interrogatories", "prior_job_id": "01M5ABCDEFGHJKMNPQRSTVWXYZ",
        "folder_id": "folder-sets-0001",
    }  # fmt: skip
    block = {"prior_job_id": unit["prior_job_id"], "folder_id": unit["folder_id"], "units": [unit]}
    revise = drafting_job._revise(block)
    assert runner_for(revise, "discovery_set") is PropoundedSetRun


PARTWAY_RUNNER = """
import json, sys, pathlib
jd = pathlib.Path(sys.argv[1])
(jd / "data").mkdir(exist_ok=True)
(jd / "data" / "versions.json").write_text(json.dumps({"draft:0": {"sent": True}, "draft:1": {}}))
out = [{"unit": "drafting", "kind": "drafting", "outcome": "failed", "stage": "file",
        "reason": "filing_failed: the second set did not read back", "dollars": 0.4,
        "document_class": "discovery_set", "files": []}]
(jd / 'verdict.json').write_text(json.dumps(out))
print(json.dumps(out))
"""


def test_a_revision_that_failed_after_sending_a_version_says_so_to_the_broker(tmp_path):
    """FALSIFIER: record the failure without ``uploads_started``, and the broker
    lets the same email start a second job that makes the same edits again."""
    import test_drafting_lane as dl

    lane, client = dl._lane(tmp_path, script=PARTWAY_RUNNER)
    dl._submit(lane, client)
    assert lane.tick() == "failed"
    assert client.records[-1][2]["delivery"]["uploads_started"] is True

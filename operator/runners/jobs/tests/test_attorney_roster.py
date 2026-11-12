"""The firm's attorney roster in the jobs runner: the validator the publish and
the seat's applier run, the twin it reads with, the seat check that counts each
disagreement, and every reader switched to it (design 2026-11-06, "Unified: who
an attorney is").

FOR EVERY READER, TWO PROOFS. With NO roster installed (every seat today) the
reader's output is exactly today's: the existing suites already pin that, and
the cases below re-run a whole job both ways. With a roster that says what the
lane's own list says, the job's output is byte for byte the same; with a roster
that says something else, the roster wins. Every roster is a real file read by
the real loader; every lane list comes from the real loaders over made-up inputs.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import docx
import pytest

import library_testkit as lt
from operator_connector_sdk.library import roster as sdk_roster
from smokeball_connector import form_letter_facts as flf

from jobs.attorneys import agree, firm as roster_firm, roster as twin

RUNNER = Path(__file__).resolve().parents[1]
LEAD_BLOCK = [
    "Lead Attorney, Esq. (SBN 123456)",
    "EXAMPLE & EXAMPLE, LLP",
    "100 Main Street",
    "Exampletown, California 90000",
    "Telephone: (555) 555-0100",
    "Email: lead@firm.example",
]
DEMAND_SIG = {"signer": "Example A. Lawyer", "title": "Attorney at Law", "initials": "EAL/dm"}


def roster_doc(**over) -> dict:
    """The kits' own attorney, as a roster says it: 'Example Lawyer' is the
    demand and drafting kits' responsible attorney, lead@firm.example the
    drafting kit's authored block."""
    lawyer = {
        "name": "Example Lawyer",
        "emails": ["lead@firm.example"],
        "bar_number": "123456",
        "caption_block": list(LEAD_BLOCK),
        "signs": {"demand": dict(DEMAND_SIG)},
    }
    lawyer.update(over)
    return {"schema_version": 1, "attorneys": [lawyer]}


def install(tmp_path: Path, monkeypatch, doc) -> Path:
    folder = tmp_path / "roster-installed"
    folder.mkdir(exist_ok=True)
    (folder / sdk_roster.FILE_NAME).write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    monkeypatch.setenv(sdk_roster.DIR_ENV, str(folder))
    return folder


@pytest.fixture(autouse=True)
def _no_roster(monkeypatch, tmp_path):
    """Every case starts on a seat with no roster installed."""
    monkeypatch.setenv(sdk_roster.DIR_ENV, str(tmp_path / "no-roster-here"))


def test_the_runners_reader_is_the_toolkits():
    # The bytes are held equal by operator/bin/gen-skill-shared.py --check
    # (module attorney_roster in operator/contracts/skill-shared-modules.yaml).
    assert twin.SCHEMA_VERSION == sdk_roster.SCHEMA_VERSION


def test_the_validator_passes_a_roster_and_refuses_anything_else(tmp_path, monkeypatch, capsys):
    good = install(tmp_path, monkeypatch, roster_doc())
    assert roster_firm.main([str(good)]) == 0
    assert "1 attorney(s), 0 staff signer(s)" in capsys.readouterr().out
    assert roster_firm.main([str(tmp_path / "empty-dir")]) == 1
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / sdk_roster.FILE_NAME).write_text(json.dumps(roster_doc(emails=["x"])), encoding="utf-8")
    assert roster_firm.main([str(tmp_path / "bad")]) == 1
    err = capsys.readouterr().err
    assert "emails" in err and "Example Lawyer" not in err  # the first line never quotes a value
    assert roster_firm.main([]) == 2


def test_the_template_roster_passes_the_validator():
    template = RUNNER.parents[1] / "customers" / "_template" / "attorneys"
    assert roster_firm.main([str(template)]) == 0


# ---- the seat check -------------------------------------------------------------------


@pytest.fixture
def lane_lists(tmp_path, monkeypatch):
    """Real drafting and demand inputs and a real customer.yaml, on their seat env paths."""
    from demand_testkit import make_inputs as demand_inputs
    from drafting_testkit import make_inputs as drafting_inputs

    monkeypatch.setenv(
        "JOBS_DRAFTING_INPUTS",
        str(drafting_inputs(tmp_path / "drafting", attorneys=[{"email": "lead@firm.example", "block": LEAD_BLOCK}])),
    )
    monkeypatch.setenv("JOBS_DEMAND_INPUTS", str(demand_inputs(tmp_path / "demand")))
    cy = tmp_path / "customer.yaml"
    cy.write_text(
        "form_letters:\n  signers:\n    Example Lawyer:\n      name: Example A. Lawyer\n      initials: EAL\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(flf.CUSTOMER_YAML_ENV, str(cy))
    return tmp_path


def test_no_roster_installed_passes_with_nothing_to_compare(lane_lists):
    code, line = agree.check(sdk_roster.load())
    assert code == 0 and "nothing to compare" in line


def test_a_roster_that_says_what_every_lane_says_passes(lane_lists, monkeypatch):
    doc = roster_doc()
    doc["attorneys"][0]["signs"]["letters"] = {"name": "Example A. Lawyer", "initials": "EAL"}
    install(lane_lists, monkeypatch, doc)
    assert agree.check(sdk_roster.load()) == (0, "every lane list agrees with the attorney roster (1 attorney(s))")


def test_each_disagreement_fails_the_check_with_its_count(lane_lists, monkeypatch):
    """FALSIFIER: the roster gives the demand signer another title and drops the
    form-letter signature; the check must fail, counting both."""
    install(lane_lists, monkeypatch, roster_doc(signs={"demand": {**DEMAND_SIG, "title": "Partner"}}))
    code, line = agree.check(sdk_roster.load())
    assert code == 1 and line.startswith("2 disagreement(s)")
    assert "demand attorneys entry 1" in line and "form letter signers entry 1" in line
    assert "Example" not in line


def test_an_unreadable_roster_fails_the_check(lane_lists, monkeypatch):
    install(lane_lists, monkeypatch, "{")
    assert agree.check(sdk_roster.load())[0] == 1


# ---- (e) form letters' signers --------------------------------------------------------


def test_form_letter_signers_come_from_the_roster_when_one_is_installed(tmp_path, monkeypatch):
    cy = tmp_path / "customer.yaml"
    cy.write_text(
        "form_letters:\n  signers:\n    Example Lawyer: {name: 'Example A. Lawyer', title: 'Attorney at Law', initials: EAL}\n"
        "    Pat Assistant: {name: Pat Assistant, title: Legal Assistant}\n",
        encoding="utf-8",
    )
    today = flf.load_signers(str(cy))
    assert today == flf.authored_signers(str(cy))  # no roster: exactly today's map
    doc = roster_doc()
    doc["attorneys"][0]["signs"]["letters"] = {
        "name": "Example A. Lawyer",
        "title": "Attorney at Law",
        "initials": "EAL",
    }
    doc["staff_signers"] = [
        {"name": "Pat  Assistant", "signs": {"letters": {"name": "Pat Assistant", "title": "Legal Assistant"}}}
    ]
    install(tmp_path, monkeypatch, doc)
    assert flf.load_signers(str(cy)) == today  # a roster saying the same: the same map, byte for byte
    doc["attorneys"][0]["signs"]["letters"]["initials"] = "EXL"
    install(tmp_path, monkeypatch, doc)
    assert flf.load_signers(str(cy))["example lawyer"]["initials"] == "EXL"  # the roster wins
    install(tmp_path, monkeypatch, "not json")
    with pytest.raises(flf.SignersUnusable):
        flf.load_signers(str(cy))


# ---- (a) "set up Dana's memos": the library setup job's attorneys -----------------------


def test_library_setup_reads_the_roster_and_no_staff_listing(tmp_path, monkeypatch):
    from jobs.library_setup import roster as R
    from test_library_setup import _client, _inputs

    before = R.attorneys(_inputs("alex@firm.example"), _client())
    assert [(a.case_system_name, a.folded_name) for a in before] == [("Alex Example", "alex example")]
    install(
        tmp_path,
        monkeypatch,
        {
            "schema_version": 1,
            "attorneys": [
                {"name": "Alex Example", "emails": ["alex@firm.example"]},
                {"name": "Casey  Third", "emails": []},
            ],
        },
    )
    client = _client()
    after = R.attorneys(_inputs("alex@firm.example"), client)
    assert [(a.case_system_name, a.folded_name) for a in after] == [
        ("Alex Example", "alex example"),
        ("Casey Third", "casey third"),
    ]
    assert after[0] == before[0] and client.calls == []  # the same identity; the case system is not asked
    found, said = R.pick("Casey", after)
    assert found is not None and found.folded_name == "casey third" and said == ""
    install(tmp_path, monkeypatch, "[]")
    with pytest.raises(R.RosterUnusable) as exc:
        R.attorneys(_inputs("alex@firm.example"), _client())
    assert exc.value.code == R.ROSTER_UNREADABLE


# ---- (b) + (c) the demand: identity, signer, format --------------------------------------


@pytest.fixture
def demand_env(monkeypatch, tmp_path):
    from jobs.drafting import firm_rules

    checker = RUNNER.parents[1] / "templates" / "drafting" / "drafting_gate_check.py"
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(checker))
    (tmp_path / "specs").mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))


def _demand_text(r) -> str:
    from test_demand_library_pick import _demand

    return "\n".join(p.text for p in docx.Document(io.BytesIO(_demand(r))).paragraphs)


def test_a_roster_that_says_what_the_demand_inputs_say_writes_the_same_demand(demand_env, tmp_path, monkeypatch):
    from test_demand_library_pick import _demand, _run

    old, v_old, _, _ = _run(tmp_path / "old")
    install(tmp_path, monkeypatch, roster_doc())
    new, v_new, _, _ = _run(tmp_path / "new")
    assert v_old.outcome == v_new.outcome == "delivered", (v_old.reason, v_new.reason)
    assert lt.members(_demand(new)) == lt.members(_demand(old))
    assert lt.record(new.data)["resolution"] == lt.record(old.data)["resolution"]


def test_the_rosters_demand_signature_wins(demand_env, tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, roster_doc(signs={"demand": {**DEMAND_SIG, "title": "Senior Trial Counsel"}}))
    from test_demand_library_pick import _run

    r, v, log, _ = _run(tmp_path / "job")
    assert v.outcome == "delivered", (v.reason, log[-5:])
    text = _demand_text(r)
    assert "Senior Trial Counsel" in text and "Attorney at Law" not in text


@pytest.mark.parametrize(
    "doc,outcome,words",
    [
        (
            {"schema_version": 1, "attorneys": [{"name": "Somebody Else", "emails": []}]},
            "held",
            "not an attorney on the firm's roster",
        ),
        (
            {
                "schema_version": 1,
                "attorneys": [{"name": "Somebody Else", "emails": []}],
                "staff_signers": [{"name": "Example Lawyer", "signs": {"letters": {"name": "E"}}}],
            },
            "held",
            "not an attorney on the firm's roster",
        ),
        (roster_doc(signs={}), "held", "no signature is authored"),
        ("{", "failed", "attorney roster could not be read"),
    ],
)
def test_a_demand_the_roster_cannot_sign_stops_before_anything_is_spent(
    demand_env, tmp_path, monkeypatch, doc, outcome, words
):
    from test_demand_library_pick import _run

    install(tmp_path, monkeypatch, doc)
    r, v, _, client = _run(tmp_path / "job")
    assert v.outcome == outcome and words in v.reason and v.reason.endswith("Nothing was spent."), v.reason
    assert client.calls == []


# ---- (b) + (d) the drafting job: identity and the caption's attorney block -----------------


def _drafting(tmp_path: Path, name: str):
    from test_drafting_attorneys import _run

    (tmp_path / name).mkdir()
    return _run(tmp_path / name, [{"email": "lead@firm.example", "block": LEAD_BLOCK}], "lead@firm.example")


@pytest.fixture
def drafting_env(monkeypatch):
    checker = RUNNER.parents[1] / "templates" / "drafting" / "drafting_gate_check.py"
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(checker))


def test_a_roster_that_says_what_the_drafting_inputs_say_sends_and_files_the_same(drafting_env, tmp_path, monkeypatch):
    old, v_old, c_old = _drafting(tmp_path, "old")
    install(tmp_path, monkeypatch, roster_doc())
    new, v_new, c_new = _drafting(tmp_path, "new")
    assert v_old.outcome == v_new.outcome == "delivered", (v_old.reason, v_new.reason)
    assert json.loads((new.data / "caption.json").read_text()) == json.loads((old.data / "caption.json").read_text())
    # Every model request, identical (as a set: the section audits run concurrently).
    assert sorted(json.dumps(c, sort_keys=True) for c in c_new.calls) == sorted(
        json.dumps(c, sort_keys=True) for c in c_old.calls
    )
    assert lt.record(new.data)["resolution"] == lt.record(old.data)["resolution"]


def test_the_rosters_caption_block_wins(drafting_env, tmp_path, monkeypatch):
    block = ["Lead Attorney, Esq. (SBN 123456)", "NEW FIRM NAME, LLP", "Email: lead@firm.example"]
    install(tmp_path, monkeypatch, roster_doc(caption_block=block))
    r, v, _ = _drafting(tmp_path, "job")
    assert v.outcome == "delivered", (v.stage, v.reason)
    assert json.loads((r.data / "caption.json").read_text())["attorney_block"]["lines"] == block


@pytest.mark.parametrize(
    "doc,outcome,code",
    [
        (
            {"schema_version": 1, "attorneys": [{"name": "Somebody Else", "emails": []}]},
            "held",
            "attorney_not_rostered",
        ),
        ("{", "failed", "roster_unreadable"),
    ],
)
def test_a_drafting_job_whose_attorney_the_roster_cannot_sign_for_stops(
    drafting_env, tmp_path, monkeypatch, doc, outcome, code
):
    install(tmp_path, monkeypatch, doc)
    r, v, client = _drafting(tmp_path, "job")
    assert v.outcome == outcome and v.reason.startswith(f"{code}: "), (v.outcome, v.reason)
    assert client.calls == []  # settled before anything is paid: no transcription, digest or compose
    rec = lt.record(r.data)
    assert rec["resolution"]["status"] == "refused" and rec["base"]["source"] == "not_started"


# ---- the draft-roster tool (run in a firm's ops workspace) --------------------------------


def _tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "draft_attorney_roster", RUNNER / "tools" / "draft_attorney_roster.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class StaffOnly:
    """A case system whose staff listing is the only thing it answers."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[tuple[str, dict]] = []

    def get(self, path, **params):
        self.calls.append((path, params))
        assert path == "/staff"
        return {"value": self.rows[params["Offset"] : params["Offset"] + params["Limit"]]}

    def post(self, *a, **k):
        raise AssertionError("the draft reads only")


def test_the_draft_is_built_from_the_staff_list_and_the_three_lists_and_validates(lane_lists, monkeypatch, capsys):
    tool = _tool()
    rows = [
        {"id": "s-1", "firstName": "Example", "lastName": "Lawyer", "email": "Lead@firm.example", "role": "Attorney"},
        {"id": "s-2", "firstName": "Pat", "lastName": "Assistant", "email": "pat@firm.example", "role": "Paralegal"},
        {"id": "s-3", "firstName": "Robin", "lastName": "Counsel", "email": "robin@firm.example", "title": "Attorney"},
        {"id": "s-4", "firstName": "Lee", "lastName": "Front", "email": "front@firm.example", "role": "Reception"},
    ]
    cy = lane_lists / "customer.yaml"
    cy.write_text(
        "form_letters:\n  signers:\n    Example Lawyer: {name: Example A. Lawyer, initials: EAL}\n"
        "    Pat Assistant: {name: Pat Assistant, title: Paralegal}\n    Gone Person: {name: G}\n",
        encoding="utf-8",
    )
    client = StaffOnly(rows)
    monkeypatch.setattr("smokeball_connector.client.build_client_from_env", lambda: client)
    assert tool.main([]) == 0
    out, err = capsys.readouterr()
    doc = json.loads(out)
    assert sdk_roster.problems(doc) == [] and "the draft validates" in err
    lawyer, robin = doc["attorneys"]
    assert lawyer == {
        "name": "Example Lawyer",
        "emails": ["lead@firm.example"],
        "bar_number": "123456",
        "caption_block": LEAD_BLOCK,
        "signs": {"demand": DEMAND_SIG, "letters": {"name": "Example A. Lawyer", "initials": "EAL"}},
    }
    assert robin == {"name": "Robin Counsel", "emails": ["robin@firm.example"]}  # every attorney, none left out
    assert doc["staff_signers"] == [
        {"name": "Pat Assistant", "signs": {"letters": {"name": "Pat Assistant", "title": "Paralegal"}}}
    ]
    assert "form letter signers entry 3: no active staff member has this name or id" in err
    assert "Lee" not in out  # staff who neither sign nor are attorneys are not drafted
    assert client.calls == [("/staff", {"Limit": tool.PAGE, "Offset": 0})]


def _staff_page() -> list[dict]:
    """A made-up firm's staff page shaped like a real one: 37 rows, 34 names,
    three names on two rows each, every pair a former record (former, not
    enabled, no role) beside the active one."""
    att = {"role": "Attorney", "former": False, "enabled": True}
    rows = [
        {"id": "a1", "firstName": "Example", "lastName": "Lawyer", "email": "lead@firm.example", **att},
        {"id": "a2", "firstName": "Second", "lastName": "Counsel", "email": "second@firm.example", **att},
        {"id": "a3", "firstName": "Third", "lastName": "Counsel", "email": "third@firm.example", **att},
        {"id": "a4", "firstName": "Fourth", "lastName": "Counsel", "email": "fourth@firm.example", **att},
        {"id": "a5", "firstName": "Fifth", "lastName": "Counsel", "email": "fifth@firm.example", **att},
        {"id": "a6", "firstName": "Sixth", "lastName": "Counsel", "email": "sixth@firm.example", **att},
        {
            "id": "p1",
            "firstName": "Pat",
            "lastName": "Assistant",
            "email": "pat@firm.example",
            "role": "Paralegal",
            "former": False,
            "enabled": True,
        },
    ]
    rows += [
        {
            "id": f"s{i}",
            "firstName": "Staff",
            "lastName": f"Member{i}",
            "email": f"s{i}@firm.example",
            "role": "Staff",
            "former": False,
            "enabled": True,
        }
        for i in range(27)
    ]
    for first, last in (("Second", "Counsel"), ("Pat", "Assistant"), ("Staff", "Member3")):
        rows.append(
            {"id": f"old-{first}", "firstName": first, "lastName": last, "email": "", "former": True, "enabled": False}
        )
    return rows


def test_a_former_record_beside_an_active_one_is_left_out_and_the_draft_validates(lane_lists, monkeypatch, capsys):
    """FALSIFIER (the replay on a real staff page, 2026-11-07): draft from every
    row and a former record with the active one's name drafts that person twice;
    the validator says "the same person appears twice" and SMD cannot commit it."""
    tool = _tool()
    rows = _staff_page()
    assert (len(rows), len({tool.full_name(r) for r in rows})) == (37, 34)
    monkeypatch.setenv("JOBS_DEMAND_INPUTS", str(lane_lists / "demand2"))
    from demand_testkit import make_inputs as demand_inputs

    demand_inputs(lane_lists / "demand2")
    import yaml

    cfg = lane_lists / "demand2" / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text())
    data["attorneys"]["Second Counsel"] = {
        "signer": "Second B. Counsel",
        "title": "Attorney for {client}",
        "initials": "",
    }
    cfg.write_text(yaml.safe_dump(data, sort_keys=False))
    (lane_lists / "customer.yaml").write_text(
        "form_letters:\n  signers:\n"
        + "".join(
            f"    {n}: {{name: {n}, initials: X}}\n"
            for n in ("Example Lawyer", "Second Counsel", "Third Counsel", "Fourth Counsel", "Pat Assistant")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("smokeball_connector.client.build_client_from_env", lambda: StaffOnly(rows))
    assert tool.main([]) == 0
    out, err = capsys.readouterr()
    doc = json.loads(out)
    assert sdk_roster.problems(doc) == [] and "the draft validates" in err
    atts = doc["attorneys"]
    assert len(atts) == 6 and all(a["emails"] for a in atts)
    assert sum("caption_block" in a for a in atts) == 1 and sum("bar_number" in a for a in atts) == 1
    assert sum("demand" in a.get("signs", {}) for a in atts) == 2
    assert sum("letters" in a.get("signs", {}) for a in atts) == 4
    assert [s["name"] for s in doc["staff_signers"]] == ["Pat Assistant"]
    review = [line for line in err.splitlines() if line.startswith("REVIEW: ")]
    assert sum("confirm they are not an attorney" in line for line in review) == 1
    former = [line for line in review if "former record" in line]
    assert len(former) == 3 and all(line.startswith("REVIEW: staff row ") for line in former)
    names = {tool.full_name(r) for r in rows} | {"Pat", "Second", "Counsel", "Assistant"}
    assert not any(n in line for line in review for n in names)  # positions, never names


def test_a_render_only_resume_with_nothing_accepted_and_no_roster_builds_no_seat(drafting_env, tmp_path, monkeypatch):
    """The roster is a file on disk: reading it never asks a run for its seat.
    A real ``DraftingRun`` over made-up inputs asks the resolver for its base
    the way a render-only resume does. FALSIFIER: read the roster through
    ``run.seat`` and the seat factory (on a seat, the case-system client) runs."""
    from drafting_testkit import make_inputs, make_job
    from jobs.drafting import firm_rules, library_pick
    from jobs.drafting.run import DraftingRun
    from jobs_testkit import PRICING

    (tmp_path / "specs").mkdir()
    monkeypatch.setenv(firm_rules.ENV_DIR, str(tmp_path / "specs"))
    pricing = tmp_path / "pricing.json"
    pricing.write_text(json.dumps(PRICING), encoding="utf-8")
    built: list[int] = []

    def factory():
        built.append(1)
        raise AssertionError("the seat was built")

    r = DraftingRun(
        make_job(tmp_path / "job"),
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
        pricing=str(pricing),
        seat_factory=factory,
        log=lambda m: None,
        readback_pause=0.0,
    )
    r.data.mkdir(parents=True, exist_ok=True)
    (r.data / "facts.json").write_text(json.dumps({"responsible_attorney": "Example Lawyer"}), encoding="utf-8")
    picked = library_pick.pick_base(r)
    assert picked.resolution.status == "none_accepted" and built == []


# ---- the probe the self-test runs (operator/templates/attorney-roster-agrees-probe.py) ----


def _probe(seat_root: Path) -> tuple[int, str]:
    import os
    import subprocess
    import sys

    probe = RUNNER.parents[1] / "templates" / "attorney-roster-agrees-probe.py"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JOBS_", "SMD_"))}
    env["SMD_SEAT_ROOT"] = str(seat_root)
    done = subprocess.run([sys.executable, str(probe)], env=env, capture_output=True, text=True, check=False)  # noqa: S603 - the repo-authored seat probe, run as the self-test runs it
    return done.returncode, done.stdout.strip()


def test_the_seat_probe_reads_the_seat_paths_under_its_root(tmp_path):
    """FALSIFIER: a probe that reads the live paths (or none) passes the disagreeing roster."""
    from demand_testkit import make_inputs as demand_inputs

    config = tmp_path / "var" / "lib" / "smd-config"
    config.mkdir(parents=True)
    assert _probe(tmp_path) == (0, "no attorney roster installed: nothing to compare, each lane reads its own list")
    demand_inputs(config / "demand")
    (config / "attorneys").mkdir()
    doc = roster_doc(signs={"demand": {**DEMAND_SIG, "initials": "XYZ"}})
    (config / "attorneys" / sdk_roster.FILE_NAME).write_text(json.dumps(doc), encoding="utf-8")
    code, line = _probe(tmp_path)
    assert code == 1 and line.startswith("1 disagreement(s)") and "demand attorneys entry 1" in line

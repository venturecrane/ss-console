"""The envelope, the firm config, the manifest diff and the seed converter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from litigation_testkit import LIBRARY, STAFF_A, envelope, firm_data, make_inputs
from medchron.litigation import baseline, firm as firm_mod, job as job_mod, manifest, vocab


def _job(**over):
    return job_mod.parse({**envelope(**over), "slug": "example", "month_cents_used": 0}, Path("/tmp/j"))


def test_a_good_envelope_parses_both_scopes():
    j = _job()
    assert j.scope_all and j.file_to_id == LIBRARY and j.folder_name == "Litigation Status"
    j2 = _job(scope={"attorney_staff_ids": [STAFF_A.upper()]})
    assert j2.attorney_staff_ids == (STAFF_A,) and not j2.scope_all
    s = _job(trigger="scheduled", message_ref="scheduled:2026-10-08", request_text="")
    assert s.trigger == "scheduled"


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"kind": "drafting"}, "kind"),
        ({"job_id": "not-a-ulid"}, "ULID"),
        ({"trigger": "cron"}, "trigger"),
        ({"requester": "nobody"}, "email"),
        ({"scope": {"attorney_staff_ids": []}}, "scope"),
        ({"scope": {"all": False}}, "scope"),
        ({"scope": {"attorney_staff_ids": ["Chris"]}}, "scope"),
        ({"trigger": "scheduled", "message_ref": "<x@y>"}, "scheduled"),
        ({"request_text": ""}, "request_text"),
        ({"file_to_matter_id": "nope"}, "filing matter"),
        ({"folder_name": "a/b"}, "folder_name"),
    ],
)
def test_a_bad_envelope_is_refused(over, needle):
    with pytest.raises(job_mod.LitigationJobError, match=needle):
        _job(**over)


def test_an_unstamped_job_is_refused():
    with pytest.raises(job_mod.LitigationJobError, match="month_cents_used"):
        job_mod.parse({**envelope(), "slug": "example"}, Path("/tmp/j"))


def test_the_firm_config_loads_and_the_validator_cli_answers(tmp_path, capsys):
    root = make_inputs(tmp_path / "in", attorneys={"Alpha Example": STAFF_A})
    f = firm_mod.load(root)
    assert f.model("audit") == "claude-sonnet-5" and f.get("email_months") == 12
    assert firm_mod.main([str(root)]) == 0 and "OK" in capsys.readouterr().out


@pytest.mark.parametrize(
    "over,needle",
    [
        ({"case_statuses": list(vocab.CASE_STATUSES)}, "missing"),
        ({"case_statuses": [*vocab.CASE_STATUSES, *vocab.DEFENDANT_STATUSES, "Open"]}, "not a product status"),
        ({"court_paper_patterns": ["(unclosed"]}, "invalid regex"),
        ({"surprise": 1}, "unknown key"),
        ({"monthly_budget_usd": 10.0}, "monthly_budget_usd"),
        ({"attorneys": {"Alpha": "not-a-uuid"}}, "attorneys.Alpha"),
        ({"models": {"read": "m"}}, "models"),
        ({"timezone": "Mars/Base"}, "timezone"),
    ],
)
def test_the_firm_config_refuses(tmp_path, over, needle):
    assert any(needle in p for p in firm_mod.validate(firm_data(**over)))


def test_a_missing_config_raises_and_the_cli_says_so(tmp_path, capsys):
    with pytest.raises(firm_mod.LitigationConfigError):
        firm_mod.load(tmp_path)
    assert firm_mod.main([str(tmp_path)]) == 1


def test_diff_and_the_seed_sentinel():
    now = {"a": "2026-01-01T00", "b": "2026-02-01T00", "c": "2026-03-01T00"}
    assert manifest.diff(now, {"a": "2026-01-01T00", "b": "2025-12-01T00", "d": "x"}) == {
        "new": ["c"],
        "changed": ["b"],
        "removed": ["d"],
    }
    assert manifest.diff(now, {"a": "", "b": "", "c": ""})["changed"] == []  # a seed row never reads as changed


def test_state_dir_resolution(monkeypatch, tmp_path):
    monkeypatch.delenv(manifest.STATE_ENV, raising=False)
    monkeypatch.setenv(manifest.DATA_ENV, str(tmp_path))
    assert manifest.state_dir() == tmp_path / "litigation" / "state"
    monkeypatch.setenv(manifest.STATE_ENV, str(tmp_path / "s"))
    assert manifest.state_dir() == tmp_path / "s"


FILES = [
    {"id": "4de824d4-0000-4000-8000-000000000001", "name": "POS Delta.pdf"},
    {"id": "0a0a0a0a-0000-4000-8000-000000000002", "name": "Complaint Gamma.pdf"},
    {"id": "0b0b0b0b-0000-4000-8000-000000000003", "name": "Answer Delta.pdf"},
]


def test_the_three_pass_shape_converts_and_cites_only_what_resolves():
    row = {
        "num": "100001",
        "case": "Gamma v. Delta",
        "court": "Exampletown",
        "case_number": "CV-0001",
        "complaint_filed": {"date": "2026-03-02", "source": "Complaint Gamma (filed 2026-03-02)"},
        "next_cmc": {"date": None, "source": "none set in file"},
        "defendants": [
            {
                "name": "Delta Example",
                "status": "Answered",
                "out_for_service": {"value": "no", "source": ""},
                "served": {"date": "2026-04-10", "method": "personal", "source": "POS (4de824d4, file 2026-04-11)"},
                "answered": {"date": "2026-04-30", "source": "Answer Delta, 2026-04-30"},
                "flags": [],
            },
        ],
        "matter_flags": [],
        "settled": {"value": "no", "source": "Complaint Gamma"},
        "case_status": "Active",
        "notes": "",
        "matter_id": "m1",
        "case_status_detail": "",
    }
    m = baseline.from_firm(row, FILES)
    assert m["defendants"][0]["served"]["source"]["file_id"].startswith("4de824d4")
    assert m["defendants"][0]["answered"]["source"]["name"] == "Answer Delta.pdf"
    assert m["case_status"]["value"] == vocab.ACTIVE
    assert m["case_status"]["source"]["file_id"] == m["complaint_filed"]["source"]["file_id"]
    assert m["fields_read"] == ["case", "defendants"] and not m["provenance"]["two_pass"]
    row["defendants"][0]["answered"]["source"] = "an email somewhere"
    assert baseline.from_firm(row, FILES)["fields_read"] == ["case"]  # an uncited date is read again


def test_the_two_pass_shape_converts_and_is_audited_first(tmp_path):
    rows = [
        {
            "num": "100001",
            "case": "Gamma v. Delta",
            "court": "X",
            "case_number": "CV-0001",
            "filed": "2026-03-02",
            "cmc": "11/20/2026 CMC",
            "defendant": "Delta Example",
            "status": "Served, appeared, no answer in file",
            "served": "2026-04-10 substituted service",
            "served_src": "POS Delta (file 2026-04-11)",
            "answered": "",
            "answered_src": "",
            "flags": "answered, no POS in file",
            "note": "",
        }
    ]
    m = baseline.from_two_pass(rows, FILES)
    d = m["defendants"][0]
    assert d["status"] == vocab.APPEARED_NO_ANSWER and d["served"]["method"] == "substituted service"
    assert m["next_court_date"]["date"] == "2026-11-20"
    assert m["provenance"]["two_pass"] and "case" not in m["fields_read"]  # no case status in this shape
    state = tmp_path / "state"
    baseline.write_state(state, {"m1": m}, {"m1": FILES}, "two_pass")
    assert manifest.load_manifest(state, "m1") == {f["id"]: "" for f in FILES}
    assert json.loads((state / "baseline.json").read_text())["matters"] == 1
    m["notes"] = "changed"
    baseline.write_state(state, {"m1": m}, {"m1": FILES}, "two_pass")
    assert manifest.load_prior(state, "m1")["notes"] == ""  # runner state is never overwritten

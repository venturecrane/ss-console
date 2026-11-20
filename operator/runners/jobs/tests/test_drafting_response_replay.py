"""The $0 discovery-response replay (jobs/drafting/response_replay.py).

A synthetic saved matter (invented values throughout) and stand-in stage
modules put in sys.modules under the names the harness expects, so the test
proves the comparison catches each failure the replay exists to catch: a
missed set, a wrong request count, a wrong sliced request, a checked-number
mismatch, and that a needed model call or an absent module stops cleanly.
"""

from __future__ import annotations

import hashlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from jobs.drafting import response_replay as R
from jobs.drafting.replay import body as B, recorded

SPECIAL = ["State each fact supporting your claim of loss.", "Identify each person who saw the event."]
FORM_SHA = hashlib.sha256(b"invented form bytes").hexdigest()


def _matter(tmp: Path) -> Path:
    job = tmp / "job"
    data = job / "data"
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "raw" / "form.pdf").write_bytes(b"invented form bytes")
    (data / "manifest.json").write_text(json.dumps({"documents": [{"id": "f1", "name": "Form set.pdf"}]}))
    (data / "extracted.jsonl").write_text(json.dumps({"id": "f1", "name": "Form set.pdf", "pages": 8}) + "\n")
    row = {"id": "f1", "ok": True, "sha256": FORM_SHA, "path": "/nowhere/on/this/host/form.pdf"}
    (data / "raw_manifest.jsonl").write_text(json.dumps(row) + "\n")
    return job


def _key(tmp: Path, **over: Any) -> Path:
    doc: dict[str, Any] = {
        "matter": "invented-001",
        "served_sets": [
            {"type": "special_interrogatories", "set_number": 1, "sha256": "ab" * 32, "request_count": 2},
            {"type": "form_interrogatories", "set_number": 1, "sha256": FORM_SHA, "request_count": 4},
        ],
        "form": {"checked": ["1.1", "6.4", "8.1"], "kinds": {"6.4": "answered_from_file"}, "names": {}},
        "special": [{"n": 1, "kind": "objection_plus_answer", "request_head": SPECIAL[0]}],
        "production": [],
    }
    doc.update(over)
    p = tmp / "keys" / "invented-001.json"
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps(doc))
    return p


def _served(**special: Any) -> dict[str, Any]:
    sp = {"set_type": "special_interrogatories", "set_number": 1, "request_count": 2, "requests": list(SPECIAL)}
    sp.update(special)
    form = {"set_type": "form_interrogatories", "set_number": 1, "request_count": 4, "sha256": FORM_SHA}
    return {"sets": [sp, form]}


def _install(monkeypatch: pytest.MonkeyPatch, served: dict[str, Any], checked: list[str], ask: bool = False) -> None:
    def find(data_dir: Path, out_dir: Path, *, doorway: Any) -> None:
        assert (data_dir / "manifest.json").is_file()
        if ask:
            doorway.call("served_find", model="m", messages=[{"role": "user", "content": "read page 1"}], max_tokens=50)
        (out_dir / "served.json").write_text(json.dumps(served))
        (out_dir / "found-report.json").write_text(json.dumps({"candidates": 2}))

    def scope(data_dir: Path, served: Any) -> dict[str, Any]:
        cats = {"medical": {"files": 3, "pages": 40, "scan_pages": 0, "chars": 900}, "other": {"chars": 100}}
        return {"categories": cats, "packs": {"form": {"chars": 1000, "cap": 100000, "left_out": []}}}

    def project(scope: Any) -> dict[str, Any]:
        return {"dollars": 1.5, "minutes": 9, "lines": [{"item": "form answers", "dollars": 1.5, "minutes": 9}]}

    for name, attrs in {
        "jobs.drafting.served_find": {"find": find},
        "jobs.drafting.response_scope": {"scope": scope},
        "jobs.drafting.response_cost": {"project": project},
        "jobs.drafting.forms": {},
        "jobs.drafting.forms.read_stage": {"pixel_read": lambda path: {"checked": checked, "unregistered": []}},
    }.items():
        mod = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(mod, k, v)
        monkeypatch.setitem(sys.modules, name, mod)


def _report(key: Path) -> dict[str, Any]:
    return json.loads(key.with_name(f"{key.stem}.replay.json").read_text())


def _mismatches(key: Path) -> list[str]:
    return [m for s in _report(key)["sections"] for m in s["mismatches"]]


def test_everything_matching_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--no-model"]) == 0
    rep = _report(key)
    assert rep["exit"] == 0 and rep["model_calls"] == 0
    names = [s["name"] for s in rep["sections"]]
    assert names[:4] == ["found vs key", "categories", "packs", "cost"]
    assert not (tmp_path / "job" / "served.json").exists(), "the replay never writes into the saved matter"


def test_a_missed_set_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served = _served()
    served["sets"] = served["sets"][:1]
    _install(monkeypatch, served, ["1.1", "6.4", "8.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "form_interrogatories set 1: missed (in the key, not found)" in _mismatches(key)


def test_an_extra_set_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served = _served()
    served["sets"].append({"set_type": "requests_for_production", "set_number": 1, "request_count": 3})
    _install(monkeypatch, served, [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert any("extra" in m for m in _mismatches(key))


def test_a_wrong_count_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(request_count=3), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "special_interrogatories set 1: request count 3 found, 2 in the key" in _mismatches(key)


def test_a_wrong_sliced_request_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(requests=["Describe every vehicle you own.", SPECIAL[1]]), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "special_interrogatories set 1 no. 1: sliced request differs" in _mismatches(key)


def test_whitespace_alone_does_not_make_a_request_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(requests=["State each fact\n  supporting your claim   of loss.", SPECIAL[1]]), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 0


def test_a_checked_number_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "9.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "form-read"]) == 1
    mm = _mismatches(key)
    assert "form 9.1: read checked, not in the key" in mm and "form 8.1: in the key, not read" in mm


def test_an_absent_module_is_named_and_exits_two(tmp_path: Path) -> None:
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 2
    (sec,) = _report(key)["sections"]
    assert sec["status"] == "missing_module" and "jobs.drafting.served_find.find" in sec["notes"][0]


def test_a_model_call_without_a_recording_stops_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"], ask=True)
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "cost"]) == 2
    rep = _report(key)
    assert [s["status"] for s in rep["sections"]] == ["needs_model"], "scope and cost stop behind find"
    (needed,) = rep["needed_calls"]
    assert needed["stage"] == "find"
    # A recording for exactly that request answers it, and the chain runs on.
    rec = {"sha": needed["sha"], "text": '{"set_type": "special_interrogatories"}', "in": 10, "out": 5}
    key.with_name(f"{key.stem}.recordings.jsonl").write_text(json.dumps(rec) + "\n")
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "cost"]) == 0


def test_the_request_sha_ignores_levers() -> None:
    a = {"system": "s", "messages": [{"role": "user", "content": "x"}], "max_tokens": 5, "model": "a"}
    b = dict(a, max_tokens=900, model="b")
    assert recorded.request_sha(a) == recorded.request_sha(b)
    assert recorded.request_sha(a) != recorded.request_sha(dict(a, system="t"))


def test_a_text_body_keeps_the_response_not_the_restated_request() -> None:
    text = (
        "SPECIAL INTERROGATORY NO. 1:\nState each fact.\n"
        "RESPONSE TO SPECIAL INTERROGATORY NO. 1:\nObjection. Vague.\n"
        "FORM INTERROGATORY NO. 6.4:\n(a) Sample Clinic (b) 01/02/2024\n"
    )
    got = B.split(text, lambda t: "objection_only" if t.startswith("Objection") else None)
    assert got[("special", "1")] == {"kind": "objection_only", "text": "Objection. Vague."}
    assert got[("form", "6.4")]["kind"] is None


def test_body_kinds_names_and_layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), [])
    names = {"6.4": ["Sample Clinic", "Example Imaging"]}
    key = _key(tmp_path, form={"checked": ["6.4"], "kinds": {"6.4": "answered_from_file"}, "names": names})
    body = tmp_path / "body.json"
    text = "(a) Sample Clinic (b) Example Imaging"
    body.write_text(json.dumps({"form": {"6.4": {"kind": "no_information", "text": text}}, "special": {}}))
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "form-read", "--body", str(body)]) == 1
    mm = _mismatches(key)
    assert "form 6.4: kind no_information, answered_from_file in the key" in mm
    assert "form 6.4: subparts (a, b) share one line" in mm and "form 6.4: 2 providers on one line" in mm
    assert "special 1: no response in the body" in mm

"""The $0 discovery-response replay (jobs/drafting/response_replay.py).

A synthetic saved matter (invented values throughout). The real scope, pack
and cost stages run on it; the finder (which would read with a model) and the
pixel read (which needs the official form's pixels) are stood in for at their
real signatures with ``monkeypatch.setattr`` on the real modules, so the test
proves the comparison catches each failure the replay exists to catch: a
missed set, a wrong request count, a wrong sliced request, a checked-number
mismatch, and that a needed model call or an absent module stops cleanly.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from drafting_testkit import firm_data
from jobs.drafting import response_replay as R, served_find
from jobs.drafting.forms import read_stage
from jobs.drafting.replay import body as B, recorded, stages as S
from jobs_testkit import PRICING

SPECIAL = ["State each fact supporting your claim of loss.", "Identify each person who saw the event."]
FORM_SHA = hashlib.sha256(b"invented form bytes").hexdigest()
SPECIAL_TEXT = "".join(f"SPECIAL INTERROGATORY NO. {i}:\n{t}\n\n" for i, t in enumerate(SPECIAL, 1))
CLIENT, OTHER = "Pat Example", "Sample Transit Co."


def _matter(tmp: Path) -> Path:
    job = tmp / "job"
    data = job / "data"
    (data / "raw").mkdir(parents=True, exist_ok=True)
    (data / "text").mkdir(parents=True, exist_ok=True)
    (data / "raw" / "form.pdf").write_bytes(b"invented form bytes")
    docs = [
        ("f1", "Form Interrogatories Set One.pdf", "Discovery", "[p.1]\nFORM INTERROGATORIES\n"),
        ("f2", "Special Interrogatories Set One.pdf", "Discovery", "[p.1]\n" + SPECIAL_TEXT),
        ("f3", "Sample Clinic records.pdf", "Medical Records", "[p.1]\nneck pain, treatment plan\n"),
    ]
    rows = []
    for i, name, folder, text in docs:
        p = data / "text" / f"{i}.txt"
        p.write_text(text)
        rows.append({"id": i, "name": name, "folder": folder, "ext": ".pdf", "pages": 1, "chars": len(text),
                     "transcribe": [], "text_path": str(p)})  # fmt: skip
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (data / "manifest.json").write_text(json.dumps([{"id": i, "name": n} for i, n, _f, _t in docs]))
    (data / "facts.json").write_text(json.dumps({"client_names": [CLIENT], "medicals": []}))
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


def _inputs(tmp: Path) -> list[str]:
    firm, pricing = tmp / "drafting-firm.yaml", tmp / "pricing.json"
    firm.write_text(yaml.safe_dump(firm_data()))
    pricing.write_text(json.dumps(PRICING))
    return ["--firm", str(firm), "--pricing", str(pricing)]


def _set(set_type: str, count: int, **over: Any) -> dict[str, Any]:
    s = {"set_type": set_type, "set_number": 1, "propounding_party": OTHER, "responding_party": CLIENT,
         "request_count": count, "drafted_by": "operator"}  # fmt: skip
    s.update(over)
    return s


def _served(text: str = SPECIAL_TEXT, count: int = 2) -> dict[str, Any]:
    sp = _set("special_interrogatories", count, request_text=text, source="f2")
    form = _set("form_interrogatories", 4, drafted_by="smd", source="f1")
    return {"sets": [sp, form]}


def _install(monkeypatch: pytest.MonkeyPatch, served: dict[str, Any], checked: list[str], ask: bool = False) -> None:
    def find(data: Path, job_dir: Path, request: str, reader: Any, *, clients: list[str], other_side: Any) -> None:
        assert (data / "manifest.json").is_file() and clients == [CLIENT]
        if ask:
            reader.ask("read page 1")
        (job_dir / "served.json").write_text(json.dumps(served))
        (job_dir / "found-report.json").write_text(json.dumps({"stop": None, "candidates": []}))

    monkeypatch.setattr(served_find, "find", find)
    monkeypatch.setattr(read_stage, "pixel_read", lambda path: {"checked": checked, "unregistered": []})


def _report(key: Path) -> dict[str, Any]:
    return json.loads(key.with_name(f"{key.stem}.replay.json").read_text())


def _mismatches(key: Path) -> list[str]:
    return [m for s in _report(key)["sections"] for m in s["mismatches"]]


def test_everything_matching_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--no-model", *_inputs(tmp_path)]) == 0
    rep = _report(key)
    assert rep["exit"] == 0 and rep["model_calls"] == 0
    names = [s["name"] for s in rep["sections"]]
    assert names[:4] == ["found vs key", "categories", "packs", "cost"]
    cats = {r["category"] for s in rep["sections"] if s["name"] == "categories" for r in s["rows"]}
    assert "medical_records" in cats, "the real scope categorised the saved files"
    assert not (tmp_path / "job" / "served.json").exists(), "the replay never writes into the saved matter"
    assert not (tmp_path / "job" / "data" / "scope.json").exists(), "the scope step ran on a copy"


def test_the_expected_table_names_real_functions() -> None:
    for stage in S.EXPECTED:
        assert callable(S.entry(stage)), stage


def test_cost_without_a_firm_needs_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), [])
    key = _key(tmp_path)
    monkeypatch.delenv("JOBS_PRICING_JSON", raising=False)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "cost"]) == 2
    assert [s["status"] for s in _report(key)["sections"]][-1] == "needs_input"


def test_a_missed_set_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served = _served()
    served["sets"] = served["sets"][:1]
    _install(monkeypatch, served, ["1.1", "6.4", "8.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "form_interrogatories set 1: missed (in the key, not found)" in _mismatches(key)


def test_an_extra_set_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    served = _served()
    served["sets"].append(_set("requests_for_production", 1, request_text="REQUEST FOR PRODUCTION NO. 1:\nAll.\n"))
    _install(monkeypatch, served, [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert any("extra" in m for m in _mismatches(key))


def test_a_wrong_count_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(count=3), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "special_interrogatories set 1: request count 3 found, 2 in the key" in _mismatches(key)


def test_a_wrong_sliced_request_is_a_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = SPECIAL_TEXT.replace(SPECIAL[0], "Describe every vehicle you own.")
    _install(monkeypatch, _served(text), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 1
    assert "special_interrogatories set 1 no. 1: sliced request differs" in _mismatches(key)


def test_whitespace_alone_does_not_make_a_request_differ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = SPECIAL_TEXT.replace(SPECIAL[0], "State each fact\n  supporting your claim   of loss.")
    _install(monkeypatch, _served(text), [])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 0


def test_a_checked_number_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "9.1"])
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "form-read"]) == 1
    mm = _mismatches(key)
    assert "form 9.1: read checked, not in the key" in mm and "form 8.1: in the key, not read" in mm


def test_an_absent_module_is_named_and_exits_two(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "jobs.drafting.served_find", None)
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 2
    (sec,) = _report(key)["sections"]
    assert sec["status"] == "missing_module" and "jobs.drafting.served_find.find" in sec["notes"][0]


def test_a_model_call_without_a_recording_stops_cleanly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"], ask=True)
    key = _key(tmp_path)
    args = [str(_matter(tmp_path)), "--key", str(key), "--stage", "cost", *_inputs(tmp_path)]
    assert R.main(args) == 2
    rep = _report(key)
    assert [s["status"] for s in rep["sections"]] == ["needs_model"], "scope and cost stop behind find"
    (needed,) = rep["needed_calls"]
    assert needed["stage"] == "find"
    # A recording for exactly that request answers it, and the chain runs on.
    rec = {"sha": needed["sha"], "text": '{"set_type": "special_interrogatories"}', "in": 10, "out": 5}
    key.with_name(f"{key.stem}.recordings.jsonl").write_text(json.dumps(rec) + "\n")
    assert R.main(args) == 0


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


# ---- the 2026-11-20 workspace replay's tool defects (invented values; the shapes it hit) ----------------


def test_the_form_file_is_found_by_its_sha_through_corpus_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R1: the response lane's pull writes corpus.json, not the chronology's raw_manifest.jsonl."""
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"])
    seen: list[Path] = []
    monkeypatch.setattr(read_stage, "pixel_read", lambda p: seen.append(p) or {"checked": ["1.1", "6.4", "8.1"]})
    job = _matter(tmp_path)
    (job / "data" / "raw_manifest.jsonl").unlink()
    pdf = job / "data" / "raw" / "form.pdf"
    (job / "data" / "corpus.json").write_text(json.dumps([{"id": "f1", "sha256": FORM_SHA, "path": str(pdf)}]))
    key = _key(tmp_path)
    assert R.main([str(job), "--key", str(key), "--stage", "form-read"]) == 0
    assert seen == [pdf]


def test_a_request_head_compares_trimmed_after_its_cut_and_without_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R2: a key copied from the paper's capitals, and a cut landing on a space, are not differences."""
    long = "x" * 79 + " and the rest of the request."
    text = SPECIAL_TEXT.replace(SPECIAL[1], long)
    _install(monkeypatch, _served(text), [])
    items = [{"n": 1, "kind": "", "request_head": SPECIAL[0].upper()}, {"n": 2, "kind": "", "request_head": "X" * 79}]
    key = _key(tmp_path, special=items)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 0, _mismatches(key)


def test_the_cost_projection_counts_the_pages_the_page_store_holds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3: the projection is the lane's own figure, which subtracts the pages already stored."""
    from jobs.drafting import response_cost

    _install(monkeypatch, _served(), [])
    got: dict[str, Any] = {}
    real_project = response_cost.project

    def project(*a: Any, **kw: Any) -> Any:
        got.update(kw)
        return real_project(*a, **kw)

    monkeypatch.setattr(response_cost, "stored_pages", lambda rows, model, log=print: 7)
    monkeypatch.setattr(response_cost, "project", project)
    key = _key(tmp_path)
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "cost", *_inputs(tmp_path)]) == 0
    assert got["stored_pages"] == 7
    cost = next(s for s in _report(key)["sections"] if s["name"] == "cost")
    assert any("7 scanned page(s) already in the seat's page store" in n for n in cost["notes"])


def _served_paper(title: str, label: str) -> str:
    return (
        f"[p.1]\nDEFENDANT SAMPLE TRANSIT CO.'S {title}, SET ONE\nPROPOUNDING PARTY: Defendant {OTHER}\n"
        f"RESPONDING PARTY: Plaintiff {CLIENT}\nSET NO.: ONE\n{label} NO. 1:\nAll example writings.\n"
    )


def test_find_lists_every_read_it_needs_and_uses_the_recordings_it_has(tmp_path: Path) -> None:
    """R4: the finder's code half runs in full and every read without a recording is listed at once."""
    job = _matter(tmp_path)
    data = job / "data"
    rows = [json.loads(x) for x in (data / "extracted.jsonl").read_text().splitlines()]
    for i, (title, label) in enumerate(
        [("SPECIAL INTERROGATORIES", "SPECIAL INTERROGATORY"), ("REQUESTS FOR PRODUCTION", "REQUEST FOR PRODUCTION")]
    ):
        p = data / "text" / f"s{i}.txt"
        p.write_text(_served_paper(title, label))
        rows.append({"id": f"s{i}", "name": f"s{i}.pdf", "folder": "Discovery", "ext": ".pdf", "text_path": str(p)})
    (data / "extracted.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    key = _key(tmp_path)
    args = [str(job), "--key", str(key), "--stage", "find", "--no-model"]
    assert R.main(args) == 2
    rep = _report(key)
    shas = [n["sha"] for n in rep["needed_calls"]]
    assert len(shas) == 2 and len(set(shas)) == 2, "both reads listed, not just the first"
    (sec,) = rep["sections"]
    assert sec["status"] == "needs_model" and all(s in sec["notes"][0] for s in shas)
    rec = {"sha": shas[0], "text": "{}", "in": 1, "out": 1}
    key.with_name(f"{key.stem}.recordings.jsonl").write_text(json.dumps(rec) + "\n")
    assert R.main(args) == 2
    assert [n["sha"] for n in _report(key)["needed_calls"]] == [shas[1]], "the recorded read was answered"


def test_the_workspace_key_shape_loads_with_its_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The workspace's hand-built keys: type aliases, set, requests, head."""
    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"])
    key = _key(
        tmp_path,
        served_sets=[
            {"type": "si", "set": 1, "sha256": "ab" * 32, "requests": 2},
            {"type": "frog", "set": 1, "sha256": FORM_SHA, "requests": 4},
        ],
        special=[{"n": 1, "kind": "objection_plus_answer", "head": SPECIAL[0]}],
    )
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 0, _mismatches(key)
    rows = next(s for s in _report(key)["sections"] if s["name"] == "found vs key")["rows"]
    assert [r["found"] for r in rows if "found" in r] == ["yes", "yes"]
    assert any(r.get("request_head_equal") for r in rows)


def test_the_workspace_responses_shape_and_set_words_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """K1: set numbers in words, upper-case type codes, and the answers under ``responses``."""
    from jobs.drafting.replay import key as K

    _install(monkeypatch, _served(), ["1.1", "6.4", "8.1"])
    key = tmp_path / "keys" / "invented-002.json"
    key.parent.mkdir()
    doc = {
        "matter": "invented-002",
        "served_sets": [
            {"type": "SI", "set": "One", "sha256": "ab" * 32, "requests": 2},
            {"type": "FI", "set": "One", "sha256": FORM_SHA, "requests": 4},
        ],
        "responses": {
            "FI": {"checked_numbers": ["1.1", "6.4", "8.1"], "per_number": {"6.4": {"kind": "answered_from_file"}}},
            "SI": {"per_request": {"1": {"kind": "objection_plus_answer", "request_first80": SPECIAL[0]}}},
            "RFP": {"per_request": [{"n": 1, "kind": "none", "request_first80": "All writings."}]},
        },
    }
    key.write_text(json.dumps(doc))
    k = K.load(key)
    assert [(s.type, s.set_number, s.request_count) for s in k.served_sets] == [
        ("special_interrogatories", 1, 2),
        ("form_interrogatories", 1, 4),
    ]
    assert k.checked == ["1.1", "6.4", "8.1"] and k.kinds == {"6.4": "answered_from_file"}
    assert [(i.n, i.kind, i.request_head) for i in k.items["special"]] == [(1, "objection_plus_answer", SPECIAL[0])]
    assert [i.n for i in k.items["production"]] == [1]
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "all", *_inputs(tmp_path)]) == 0, _mismatches(
        key
    )
    with pytest.raises(K.EmptyKey, match="set_number or set"):
        K.parse({**doc, "served_sets": [{"type": "SI", "set": "Eleventy"}]})


def test_a_key_with_no_served_set_fails_loudly_naming_the_missing_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install(monkeypatch, _served(), [])
    key = _key(tmp_path, served_sets=[{"type": "si", "sha256": "ab" * 32}])
    assert R.main([str(_matter(tmp_path)), "--key", str(key), "--stage", "find"]) == 2
    assert "served_sets[0] (no set_number or set)" in capsys.readouterr().err
    assert not key.with_name(f"{key.stem}.replay.json").exists()

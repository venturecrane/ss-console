"""The form set joins the drafting lane: the box read, the stops before spend,
the dispatch in ``finish``, and SMD's read of the first form set.
Every name, rule and value here is invented."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pytest

from jobs import drafting_lane
from jobs.drafting import finish as finish_mod, served as served_mod
from jobs.drafting.forms import catalog, checkbox, lane_hook, policy, read_stage
from jobs.drafting.outcome import REASON, DraftingFailed

from test_forms_answer_stage import POLICY

SOURCE = Path(catalog.__file__).resolve().parent / "source" / "disc001-rev-2024-01-01.pdf"
WANT = ["2.1", "2.2", "6.1", "6.4", "8.1"]


@pytest.fixture(scope="module")
def blank() -> dict[int, np.ndarray]:
    return dict(enumerate(checkbox.render_pages(SOURCE.read_bytes(), dpi=150)))


def _mark(pages: dict[int, np.ndarray], keys: Iterable[str]) -> dict[int, np.ndarray]:
    out = {p: a.copy() for p, a in pages.items()}
    geo = checkbox.geometry()
    for k in keys:
        b = geo[k]
        img = out[b.page]
        s = img.shape[1] / 612.0
        x0, y0, x1, y1 = (int(round(v * s)) for v in b.widget)
        img[y0:y1, x0:x1] = 0
    return out


def _texts(n: int = 8) -> list[str]:
    return ["PROOF OF SERVICE"] + [f"DISC-001 [Rev. January 1, 2024] Page {i} of 8" for i in range(1, n + 1)]


def _reply(nums: list[str], additions: str = "NONE") -> str:
    return "CHECKED:\n" + "\n".join(nums) + f"\nDEFINITION:\n4(a)(1)\nADDITIONS:\n{additions}\nUNREADABLE:\nNONE"


class Door:
    def __init__(self, *texts: str) -> None:
        self.texts, self.calls = list(texts), []

    def call(self, stage: str, **kw: Any) -> Any:
        self.calls.append(stage)
        t = self.texts.pop(0)

        class R:
            text = t

        return R()


def _job(tmp_path: Path, count: int = 5) -> Path:
    s = {"set_type": "form_interrogatories", "set_number": 1, "propounding_party": "Def Example",
         "responding_party": "Pat Example", "request_count": count, "drafted_by": "operator"}  # fmt: skip
    (tmp_path / "served.json").write_text(json.dumps({"sets": [s]}), encoding="utf-8")
    return tmp_path


def _read(tmp_path: Path, door: Door, images: Any, texts: list[str] | None = None) -> list[str]:
    return read_stage.read_served_form(
        None, tmp_path, 0, door, "model-x", texts=texts or _texts(), images=images, encode=lambda _i: b"png"
    )


def test_two_agreeing_reads_and_the_pixel_read_give_the_checked_list(tmp_path: Path, blank: Any) -> None:
    door = Door(_reply(WANT), _reply(WANT))
    got = _read(_job(tmp_path), door, _mark(blank, [*WANT, "4(a)(1)"]))
    assert got == WANT and door.calls == list(read_stage.STAGES)
    s = json.loads((tmp_path / "served.json").read_text())["sets"][0]
    assert (s["checked"], s["form"], s["form_revision"], s["definition_boxes"]) == (
        WANT, "DISC-001", "Rev. January 1, 2024", ["4(a)(1)"],
    )  # fmt: skip
    assert json.loads((tmp_path / read_stage.RECORD).read_text())["outcome"] == "agreed"
    assert _read(tmp_path, Door(), None) == WANT  # never read twice


def test_a_disagreement_stops_form_unconfirmed_and_records_all_three_reads(tmp_path: Path, blank: Any) -> None:
    door = Door(_reply(WANT), _reply(WANT[:-1]))
    with pytest.raises(DraftingFailed, match=r"^form_unconfirmed: .*8\.1") as e:
        _read(_job(tmp_path), door, _mark(blank, [*WANT, "4(a)(1)"]))
    assert REASON.match(str(e.value))
    rec = json.loads((tmp_path / read_stage.RECORD).read_text())
    assert len(rec["reads"]) == 2 and rec["reads"][1]["read"]["checked"] == WANT[:-1]
    assert rec["pixel"]["ratios"]["8.1"] > checkbox.FILLED_MIN and rec["outcome"] == "form_unconfirmed"
    assert "checked" not in json.loads((tmp_path / "served.json").read_text())["sets"][0]


def test_an_addition_or_a_count_mismatch_is_never_used(tmp_path: Path, blank: Any) -> None:
    img = _mark(blank, [*WANT, "4(a)(1)"])
    with pytest.raises(DraftingFailed, match="handwritten"):
        _read(_job(tmp_path), Door(_reply(WANT, "page 8: 51.1 written in"), _reply(WANT)), img)
    with pytest.raises(DraftingFailed, match="count is 6"):
        _read(_job(tmp_path, 6), Door(_reply(WANT), _reply(WANT)), img)


def test_seven_of_eight_form_pages_stops_form_incomplete_before_any_read(tmp_path: Path) -> None:
    door = Door()
    with pytest.raises(DraftingFailed, match=r"^form_incomplete: .*\[8\]"):
        _read(_job(tmp_path), door, {}, _texts(7))
    assert door.calls == []


# ---- the lane --------------------------------------------------------------------------


class Firm:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data

    def read(self, rel: str) -> str:
        return "Caption\n\n{{FORM RESPONSES}}\n"

    def model(self, stage: str) -> str:
        return "model-x"


class Job:
    def __init__(self, job_dir: Path, form_releases: int | None = 0) -> None:
        self.job_dir, self.form_releases = job_dir, form_releases


class Run:
    def __init__(
        self, tmp: Path, firm_data: dict[str, Any], variant: str = "form_interrogatories", checked: Any = WANT
    ):
        self.data = tmp / "data"
        self.data.mkdir(parents=True, exist_ok=True)
        self.job = Job(tmp)
        self.firm = Firm(firm_data)
        s = {"set_type": variant, "set_number": 1, "propounding_party": "Def Example",
             "responding_party": "Pat Example", "request_count": 5, "request_text": "", "drafted_by": "operator"}  # fmt: skip
        (self.data / served_mod.PLAN).write_text(json.dumps({"sets": [s]}), encoding="utf-8")
        u = finish_mod.Unit("set-1", "Responses to Form Interrogatories, Set One", variant, None, self.data / "set-1")
        u.data.mkdir(exist_ok=True)
        self.plan = {"key": "set-1", "variant": variant, "served": 0, **({"checked": checked} if checked else {})}
        self._units = [u]

    def _state(self) -> dict[str, Any]:
        return {"units": [self.plan]}

    def units(self) -> list[Any]:
        return self._units


def _rules(run: Run, ids: list[str]) -> None:
    rules = [{"rule_id": r, "text": f"rule {r}", "wording": [f"wording of {r}"], "doc_kinds": ["x"]} for r in ids]
    (run.data / "wording-rules.json").write_text(json.dumps({"rules": rules}), encoding="utf-8")


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(served_mod, "FORM_SETS_BY_OPERATOR", True, raising=False)


def test_the_switch_is_off_by_default(tmp_path: Path) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)})
    assert lane_hook.enabled() is False and not lane_hook.takes(run, run.units()[0])
    lane_hook.before_spend(run)  # nothing checked while off


def test_a_missing_policy_stops_before_any_spend(tmp_path: Path, on: None) -> None:
    run = Run(tmp_path, {})
    with pytest.raises(DraftingFailed, match=r"^policy_missing: .*nothing was spent") as e:
        lane_hook.before_spend(run)
    assert REASON.match(str(e.value))


def test_a_form_unit_without_checked_boxes_stops(tmp_path: Path, on: None) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)}, checked=None)
    with pytest.raises(DraftingFailed, match="^form_unconfirmed: "):
        lane_hook.before_spend(run)


def test_the_compilation_rule_is_not_applied_and_an_unplaced_rule_stops(tmp_path: Path, on: None) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)})
    _rules(run, ["r-plain", "r-compilation"])
    lane_hook.before_spend(run)
    pol = policy.load_policy(run.firm.data)
    u = run.units()[0]
    run._json = lambda _n: {"medicals": []}  # type: ignore[attr-defined]
    run._context = lambda: ["- client: Pat Example"]  # type: ignore[attr-defined]
    inp = lane_hook._inputs(run, u, lane_hook._served(run, u), pol)
    assert "r-plain" in inp.rules_block and "r-compilation" not in inp.rules_block
    _rules(run, ["r-plain", "r-compilation", "r-other"])
    with pytest.raises(DraftingFailed, match=r"^rule_unplaced: .*r-other"):
        lane_hook.before_spend(run)


class _Draft(finish_mod.Finish):
    def __init__(self, run: Run) -> None:
        self.run = run
        self.data, self.job, self.firm, self.cls = run.data, run.job, run.firm, "discovery_response"
        self._units = run.units()

    def _state(self) -> dict[str, Any]:
        return self.run._state()

    def _drafter(self, u: Any) -> Any:
        raise LookupError("compose path")


def test_a_non_form_unit_is_untouched(tmp_path: Path, on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)}, variant="special_interrogatories")
    d = _Draft(run)
    assert not lane_hook.takes(d, d.units()[0])
    with pytest.raises(LookupError, match="compose path"):
        d._draft(d.units()[0], "digest")


def test_a_form_unit_takes_the_answer_path(tmp_path: Path, on: None, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)})
    monkeypatch.setattr(lane_hook, "draft", lambda r, u: "answered by code")
    d = _Draft(run)
    assert d._draft(d.units()[0], "digest") == "answered by code"


def _manifest(tmp: Path) -> list[dict[str, Any]]:
    f = tmp / "out.docx"
    f.write_bytes(b"doc")
    import hashlib

    return [{"name": "out.docx", "role": "draft", "sha256": hashlib.sha256(b"doc").hexdigest(), "local_path": str(f)}]


def test_the_first_form_set_is_held_for_smd_and_the_second_is_not(tmp_path: Path, on: None) -> None:
    run = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)})
    m = _manifest(tmp_path)
    with pytest.raises(
        DraftingFailed, match=r"^held_for_smd: .*form interrogatory response set before it files \(0 of 1"
    ):
        lane_hook.first_set_gate(run, m)
    from jobs.drafting import review_first

    (run.job.job_dir / review_first.RELEASE_MARKER).write_text("x")
    assert lane_hook.first_set_gate(run, m) is True  # SMD released it: it files, and counts
    run2 = Run(tmp_path / "second", {policy.KEY: copy.deepcopy(POLICY)})
    run2.job.form_releases = 1
    assert lane_hook.first_set_gate(run2, m) is False


def test_the_release_record_counts_each_class_apart(tmp_path: Path) -> None:
    rows = [{"job_id": "j1"}, {"job_id": "j2", "class": "form_interrogatories"}]
    (tmp_path / drafting_lane.RELEASES).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert drafting_lane.releases(tmp_path) == ["j1"]
    assert drafting_lane.releases(tmp_path, drafting_lane.FORM_RELEASES) == ["j2"]


def test_draft_writes_the_paper_the_cite_table_and_no_compose(tmp_path: Path, on: None) -> None:
    from test_forms_answer_stage import CORPUS, Door as AnswerDoor, _v

    run: Any = Run(tmp_path, {policy.KEY: copy.deepcopy(POLICY)})
    u = run.units()[0]
    run.plan["checked"] = ["2.2", "2.9"]
    reply = {
        "answers": [{"number": "2.2", "kind": "answered", "items": [_v("June 1, 1980", 4, "born on June 1, 1980")]}]
    }
    done: list[str] = []
    run.doorway = AnswerDoor(reply)
    run.log = lambda _m: None
    run._json = lambda _n: {"medicals": []}
    run._context = lambda: ["- client: Pat Example"]
    run._done = lambda stage, **_k: done.append(stage)
    md = lane_hook.draft(run, u, CORPUS)
    assert md.startswith("Caption") and "RESPONSE TO FORM INTERROGATORY NO. 2.9:" in md and "Not applicable." in md
    assert len(run.doorway.calls) == 1 and done == ["set-1:form_answers"]
    assert "| 2.2 |  | records-a.pdf | 4 |" in (u.data / lane_hook.CITES).read_text()
    assert "records-a.pdf" not in md
    assert "Where each answer comes from" in lane_hook.with_cite_tables(run, "# Attorney notes\n")
    assert lane_hook.draft(run, u, CORPUS) == md  # resumed: read back, no call

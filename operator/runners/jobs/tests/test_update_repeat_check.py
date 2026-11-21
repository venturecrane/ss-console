"""The repeat check (``stages/update_repeat_check``), through the real driver,
the real stages and the update scene of ``test_update_e2e``: a scripted model
answers the check, and everything else is the lane's own code.

A second new document repeats the first one's two pages under another fax
header, so the rules set both of its pages aside as repeats (not pixel-equal:
the fax line differs). The reader decides: DIFFERENT reads them (composed and
charged as if no rule had matched), SAME keeps them aside (not charged),
a failure reads them. A pixel-equal copy is never sent; a resumed stage pays
for no pair it answered; the projection counts the pairs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

import test_update_e2e as E
from jobs import budget as budget_mod
from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron import pages_record as PR
from jobs.medchron.update import charged, repeat_check as RC
from jobs.stages import update_repeat_check as URC
from jobs.stages.base import StageRun
from jobs_testkit import FakeSeat, doc_row, make_pdf
from test_update_e2e import scene  # noqa: F401 - the update scene fixture

MARK = "REPEAT CHECK FOR A MEDICAL CHRONOLOGY UPDATE"


class _Checker(E._Model):
    """The update scene's model; the repeat check answered by `answer`
    ("SAME", "DIFFERENT", or "raise")."""

    def __init__(self, answer: str) -> None:
        super().__init__()
        self.answer = answer
        self.checks: list[dict[str, Any]] = []

    def create(self, **kw):
        if MARK in self._system(kw):
            self.checks.append(kw)
            if self.answer == "raise":  # a 400: the doorway raises it at once
                import anthropic
                import httpx

                req = httpx.Request("POST", "https://api.invalid/v1/messages")
                raise anthropic.BadRequestError("invalid_request", response=httpx.Response(400, request=req), body=None)
            if self.answer == "refuse":
                return E._msg([type("B", (), {"type": "text", "text": ""})()], "refusal")
            if self.answer == "garbled":
                return E._text("I think they match.")
            return E._text(json.dumps({"verdict": self.answer}))
        return super().create(**kw)


def _add_copy(job_dir: Path, seat: FakeSeat, data: bytes, name: str = "Imaging refax.pdf") -> None:
    seat.blobs["n4"] = data
    seat.docs.append(doc_row("n4", name, E.MEDICAL, len(data)))
    body = yaml.safe_load((job_dir / "job.yaml").read_text())
    body["selection"]["include_file_ids"].append("n4")
    (job_dir / "job.yaml").write_text(yaml.safe_dump(body, sort_keys=False))


def _refax() -> bytes:
    return make_pdf(["FAX 555-0177 06/09/2026 16:40 PAGE 7 OF 30\n" + p for p in E.NEW_PAGES])


def _run(scene, data_root: Path, firm: Path, pricing: Path, monkeypatch, answer: str, copy: bytes) -> tuple[Any, Any]:
    job_dir, seat = scene
    E._upload_pause(monkeypatch)
    _add_copy(job_dir, seat, copy)
    model = _Checker(answer)
    o = E._driver(job_dir, firm, pricing, seat, model).run()[0]
    return o, model


def _rows(data_root: Path, doc: str) -> list[dict[str, Any]]:
    return [r for r in PR.read(data_root / "example-matter" / "runs" / "alpha") if r["doc"] == doc]


def _ud(data_root: Path) -> Path:
    return data_root / "example-matter" / "runs" / "alpha" / "update"


def _images(kw: dict[str, Any]) -> int:
    return sum(1 for m in kw["messages"] for b in m["content"] if isinstance(b, dict) and b.get("type") == "image")


def test_a_copy_the_reader_rules_different_is_read_composed_and_charged(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    o, model = _run(scene, data_root, firm_config_path, pricing_path, monkeypatch, "DIFFERENT", _refax())
    # One call per set-aside page, each shown that page and the page covering it.
    assert len(model.checks) == 2 and all(_images(kw) == 2 for kw in model.checks)
    sel = json.loads((_ud(data_root) / "selection.json").read_text())
    ex = {r["source_name"]: r for r in sel["exhibits"]}
    # The copy is no longer a re-filed copy: it is an exhibit, both pages read.
    assert "Imaging refax.pdf" in ex and ex["Imaging refax.pdf"]["read"] == [1, 2]
    assert not [s for s in sel["subsets"] if s["source_name"] == "Imaging refax.pdf"]
    compose = (_ud(data_root) / "compose" / "n4.txt").read_text()
    assert "[p.1]" in compose and "[p.2]" in compose and "disc protrusion" in compose
    # The page record says read, so the pages are charged.
    assert [r["status"] for r in _rows(data_root, "n4")] == ["read", "read"]
    assert charged.read_pages(data_root / "example-matter", "alpha") == 4
    counts = RC.counts(_ud(data_root))
    assert counts["repeat_pairs_checked"] == 2 and counts["repeat_different"] == 2
    assert counts.get("repeat_same", 0) == 0


def test_a_copy_the_reader_rules_same_stays_aside_uncharged_and_a_resume_pays_nothing(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    o, model = _run(scene, data_root, firm_config_path, pricing_path, monkeypatch, "SAME", _refax())
    assert o.outcome == "delivered", o
    assert len(model.checks) == 2
    sel = json.loads((_ud(data_root) / "selection.json").read_text())
    assert "Imaging refax.pdf" not in {r["source_name"] for r in sel["exhibits"]}
    rows = _rows(data_root, "n4")
    assert [r["status"] for r in rows] == ["excluded", "excluded"] and {r["class"] for r in rows} == {"repeat"}
    assert charged.read_pages(data_root / "example-matter", "alpha") == 2  # n1's two pages only
    assert RC.counts(_ud(data_root))["repeat_same"] == 2
    # A resumed stage asks nothing it answered: zero calls, the same decision.
    again = _Checker("DIFFERENT")
    sr = _sr(scene[0], firm_config_path, data_root, again)
    assert URC.run(sr) == 0
    assert again.checks == [] and [r["status"] for r in _rows(data_root, "n4")] == ["excluded", "excluded"]


@pytest.mark.parametrize("answer, calls", [("raise", 2), ("refuse", 2), ("garbled", 4)])
def test_a_failed_check_reads_the_page_and_never_holds_the_job(
    scene,
    data_root: Path,
    firm_config_path: Path,
    pricing_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    calls: int,
) -> None:
    """A model error, a refusal, or a reply that does not parse after its one
    retry: the page is read, and the job goes on."""
    o, model = _run(scene, data_root, firm_config_path, pricing_path, monkeypatch, answer, _refax())
    assert len(model.checks) == calls
    assert o.stage != "update_repeat_check", o
    assert [r["status"] for r in _rows(data_root, "n4")] == ["read", "read"]
    assert RC.counts(_ud(data_root))["repeat_flipped_by_failure"] == 2


def test_a_pixel_equal_copy_is_never_sent(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The same pages drawn again: other bytes, the same pixels.
    o, model = _run(scene, data_root, firm_config_path, pricing_path, monkeypatch, "DIFFERENT", make_pdf(E.NEW_PAGES))
    assert o.outcome == "delivered", o
    assert model.checks == []
    assert all(r["status"] == "excluded" for r in _rows(data_root, "n4"))
    assert RC.counts(_ud(data_root)).get("repeat_pairs_checked", 0) == 0


def test_the_projection_counts_the_pairs_still_to_ask(
    scene, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(scene, data_root, firm_config_path, pricing_path, monkeypatch, "SAME", _refax())
    sd = data_root / "example-matter"
    assert URC.tokens(sd, "alpha")[:2] == (2, 0)  # both answered: nothing left to price
    (_ud(data_root) / RC.NAME).unlink()
    n, todo, tin, tout = URC.tokens(sd, "alpha")
    per = RC.PROMPT_TOKENS + 2 * RC.PICTURE_TOKENS
    assert (n, todo, tin, tout) == (2, 2, 2 * per, 2 * RC.OUT_TOKENS)
    cfg = config_mod.load(str(firm_config_path))
    pricing = budget_mod.Pricing.load(pricing_path)
    rate = pricing.rate_for(cfg.get("models", "tiers")["judgment"])
    want = (tin * rate.input_per_million_cents + tout * rate.output_per_million_cents) / 1e8
    assert URC.projected_usd(sd, "alpha", cfg, pricing) == pytest.approx(want) and want > 0


def test_a_repeat_with_no_covering_page_is_read_without_a_call() -> None:
    rows = [
        PR.row("d1", "s1", 1, PR.EXCLUDED, "repeat", PR.TEXT, "update_select", {"doc": "d0", "page": 3, "exact": False}),
        PR.row("d1", "s1", 2, PR.EXCLUDED, "refiled_extract", PR.FILE, "update_select", {"doc": "d0", "page": None}),
        PR.row("d1", "s1", 3, PR.EXCLUDED, "repeat", PR.PICTURE, "update_select", {"doc": "d0", "page": 4, "exact": True}),
        PR.row("d1", "s1", 4, PR.EXCLUDED, "blank", PR.BLANK, "update_select"),
        PR.row("d2", "s2", 1, PR.EXCLUDED, "already_delivered", PR.CONTAINED, "update_select", {"exhibit": 2, "page": 5, "last": 6, "exact": False}),
    ]  # fmt: skip
    pairs, lost = RC.pairs(rows, {2: "x2"}, {"d0": "s0"})
    assert [(p.doc, p.page, p.cover, p.cover_pages) for p in pairs] == [
        ("d1", 1, "d0", [3]),
        ("d2", 1, "exhibit:2", [5, 6]),
    ]
    assert [(u.doc, u.page) for u in lost] == [("d1", 2)]


def test_the_verdict_is_parsed_strictly() -> None:
    assert RC.parse('{"verdict": "SAME"}') == RC.SAME
    assert RC.parse('Here: {"verdict": "different"}') == RC.DIFFERENT
    assert RC.parse('{"verdict": "MAYBE"}') is None and RC.parse("SAME") is None and RC.parse("") is None


def _sr(job_dir: Path, firm: Path, data_root: Path, model: Any) -> StageRun:
    job = job_mod.load(job_dir)
    return StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=lambda *_: None,
        seat_factory=lambda: FakeSeat([], [], {}),
        client_factory=lambda: model,
    )

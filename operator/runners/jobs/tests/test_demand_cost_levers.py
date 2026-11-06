"""What a large demand costs, without changing what it says: a page the file
holds more than once is read once, a firm may send the per-page and per-chunk
stages through the provider's batch queue, and an audit pass writes the cache
of the digest once."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from demand_testkit import ScriptedClient, make_inputs, make_job, make_pdf, seat_with, standard_docs
from jobs_testkit import PRICING
from jobs.demand import batching, firm as firm_mod, repeats, summarize
from jobs.demand.run import DemandRun

CHECKER = Path(__file__).resolve().parents[3] / "templates" / "drafting" / "drafting_gate_check.py"
PAGE = "Exampletown Clinic. Patient Alpha Example seen for neck pain after a collision. " * 4


@pytest.fixture(autouse=True)
def _checker(monkeypatch):
    monkeypatch.setenv("SMD_DRAFTING_GATE_CHECK", str(CHECKER))


@pytest.fixture
def pricing(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


class QueueClient(ScriptedClient):
    """The scripted model, with a batch queue that answers every request the
    same way the live path would. ``held`` names batch ids still processing."""

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.batches: list[list[dict[str, Any]]] = []
        self.held: set[str] = set()
        self.messages.batches = SimpleNamespace(create=self._create_batch, retrieve=self._status, results=self._results)

    def _create_batch(self, requests: list[dict[str, Any]]) -> Any:
        self.batches.append(list(requests))
        return SimpleNamespace(id=f"b{len(self.batches)}")

    def _status(self, bid: str) -> Any:
        return SimpleNamespace(processing_status="in_progress" if bid in self.held else "ended")

    def _results(self, bid: str) -> Any:
        for r in self.batches[int(bid[1:]) - 1]:
            yield SimpleNamespace(
                custom_id=r["custom_id"], result=SimpleNamespace(type="succeeded", message=self._msg(r["params"]))
            )


def _levers(root: Path, **levers: Any) -> Path:
    make_inputs(root)
    cfg = root / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["levers"].update(levers)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return root


def _job(tmp_path: Path, pricing: Path, seat: Any, client: Any, **levers: Any) -> DemandRun:
    inputs = tmp_path / "inputs"
    if not inputs.is_dir():
        _levers(inputs, **levers)
    jd = tmp_path / "job"
    return DemandRun(
        jd if jd.is_dir() else make_job(jd),
        inputs_dir=str(inputs),
        pricing=str(pricing),
        seat_factory=lambda: seat,
        client=client,
        log=lambda _line: None,
        readback_pause=0.0,
        vendor_factory=lambda: None,
    )


def _ledger(tmp_path: Path) -> list[dict[str, Any]]:
    p = tmp_path / "job" / "data" / "usage-ledger.jsonl"
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- a page the file holds more than once -------------------------------------------
def test_a_repeated_page_becomes_a_pointer_to_where_it_was_first_read():
    fold = repeats.Repeats()
    first = fold.fold("Chart A.pdf", f"[p.1]\n{PAGE}\n[p.2]\nA second page with its own words. {PAGE[:150]} More.\n")
    again = fold.fold("Chart B.pdf", f"[p.1]\nA cover sheet.\n[p.2]\n{PAGE.upper()}\n")
    assert PAGE in first and "repeats" not in first  # the first reading is whole
    assert PAGE.upper() not in again
    assert "[p.2]\n(This page repeats FILE: Chart A.pdf, p. 1, word for word; it is read there.)" in again
    assert "A cover sheet." in again  # a short page is never folded
    assert fold.rows == [{"file": "Chart B.pdf", "page": 2, "first_file": "Chart A.pdf", "first_page": 1}]
    assert fold.chars == len(PAGE.upper()) + 1


def test_a_look_alike_is_not_a_repeat_and_two_files_may_share_a_name():
    fold = repeats.Repeats()
    fold.fold("subpoena.pdf", f"[p.1]\n{PAGE}\n")
    other = fold.fold("subpoena.pdf", f"[p.1]\n{PAGE}\n[p.2]\n{PAGE.replace('neck', 'back')}\n")
    assert other.count("This page repeats FILE: subpoena.pdf, p. 1") == 1  # the same-named second file's page 1
    assert PAGE.replace("neck", "back") in other  # one word apart: read
    whole = fold.fold("Email body", PAGE)
    assert whole.startswith("(This document repeats FILE: subpoena.pdf, p. 1")


def test_every_distinct_page_is_still_in_the_summarizers_input_exactly_once(tmp_path):
    texts = {
        "a": f"[p.1]\n{PAGE}\n[p.2]\n{PAGE.replace('neck', 'knee')}\n",
        "b": f"[p.1]\n{PAGE.replace('neck', 'knee')}\n[p.2]\n{PAGE}\n[p.3]\n{PAGE.replace('neck', 'hip')}\n",
    }
    files = []
    for name, text in texts.items():
        p = tmp_path / f"{name}.txt"
        p.write_text(text, encoding="utf-8")
        files.append({"name": f"{name}.pdf", "folder": "/Medical", "text_path": str(p)})
    plain = "".join(summarize.build_chunks(files, 120_000))
    fold = repeats.Repeats()
    folded = "".join(summarize.build_chunks(files, 120_000, fold))
    for part in ("neck", "knee", "hip"):
        page = PAGE.replace("neck", part)
        assert plain.count(page) == (1 if part == "hip" else 2) and folded.count(page) == 1
    assert len(fold.rows) == 2 and len(folded) < len(plain)
    assert "".join(summarize.build_chunks(files, 120_000)) == plain  # off unless asked


def _scans() -> bytes:
    """Page 1 has words; pages 2 and 3 are the same scanned image; page 4 is another."""
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Exampletown ER itemized statement page one with readable words.", fontsize=11)
    for shade in (200, 200, 90):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(shade)
        doc.new_page().insert_image(pymupdf.Rect(72, 72, 300, 300), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def _with_scans() -> list[tuple[str, str, bytes, str]]:
    return [d if d[0] != "d2" else ("d2", "ER bill 1-15-26.pdf", _scans(), "f-med") for d in standard_docs()]


def test_two_pages_with_the_same_image_are_transcribed_once_and_both_get_the_reading(tmp_path, pricing):
    client = ScriptedClient()
    r = _job(tmp_path, pricing, seat_with(_with_scans()), client)
    v = r.run()
    assert v.outcome == "delivered", v.reason
    assert client.stages().count("VISION") == 2  # three scanned pages, two images
    data = tmp_path / "job" / "data"
    read = [json.loads(line) for line in next((data / "partial").glob("*.jsonl")).read_text().splitlines()]
    assert sorted(x["page"] for x in read) == [2, 3, 4]
    rec = json.loads((data / repeats.RECORD).read_text(encoding="utf-8"))["scanned"]
    assert rec["pages"] == 1 and rec["repeats"][0]["page"] == 3 and rec["repeats"][0]["first_page"] == 2


def test_a_firm_that_asks_has_repeated_text_pages_summarized_once(tmp_path, pricing):
    twice = [*standard_docs(), ("d9", "ER record copy.pdf", standard_docs()[0][2], "f-med")]
    lines = [f"On visit {i} the patient was seen at the clinic for neck pain after the collision." for i in range(1, 7)]
    page = "\n".join(lines)
    # A second production of the same chart: another file (the pull already drops a byte-identical one), the same page.
    again = make_pdf(["Records produced in response to a later request.", page])
    docs = [*twice, ("d10", "Chart.pdf", make_pdf([page]), "f-med"), ("d11", "Chart again.pdf", again, "f-med")]
    r = _job(tmp_path, pricing, seat_with(docs), ScriptedClient(), fold_repeated_pages=True)
    v = r.run()
    assert v.outcome == "delivered", v.reason
    data = tmp_path / "job" / "data"
    rec = json.loads((data / repeats.RECORD).read_text(encoding="utf-8"))["text"]
    assert [(x["file"], x["page"], x["first_file"]) for x in rec["repeats"]] == [("Chart again", 2, "Chart")]
    chunk = (data / "digest" / "chunk-00.txt").read_text(encoding="utf-8")
    assert chunk.count(lines[5]) == 1 and "This page repeats FILE: Chart, p. 1" in chunk
    assert lines[5] in (data / "text").joinpath("d11.txt").read_text(encoding="utf-8")  # the text file is whole


# ---- the batch queue -----------------------------------------------------------------
def test_the_batch_levers_are_validated_and_off_when_absent(tmp_path):
    assert "batch_stages" not in firm_mod.load(make_inputs(tmp_path / "plain")).data["levers"]
    ok = _levers(tmp_path / "ok", batch_stages=["vision", "digest", "condense"], batch_over_usd=50.0)
    assert firm_mod.load(ok).data["levers"]["batch_over_usd"] == 50.0
    with pytest.raises(firm_mod.DemandConfigError, match="not a batchable stage"):
        firm_mod.load(_levers(tmp_path / "bad", batch_stages=["compose"]))
    with pytest.raises(firm_mod.DemandConfigError, match="names no stage"):
        firm_mod.load(_levers(tmp_path / "line", batch_over_usd=50.0))


def test_the_queue_is_used_only_at_or_over_the_firms_line():
    levers = {"batch_stages": ["vision", "digest"], "batch_over_usd": 50.0}
    assert batching.stages(levers, 49.99) == frozenset()
    assert batching.stages(levers, 50.0) == {"vision", "digest"}
    assert batching.stages({"batch_stages": ["digest"]}, 0.5) == {"digest"}
    assert batching.stages({}, 500.0) == frozenset()
    budget = {"usd_per_scanned_page": 0.012, "usd_per_million_chars": 3.0, "usd_per_million_condense_chars": 3.3}
    assert batching.projected_usd(budget, 0.5, "vision", 1000, 0) == pytest.approx(6.0)
    assert batching.projected_usd(budget, 0.5, "digest", 0, 2_000_000) == pytest.approx(3.0)


def test_a_queued_job_delivers_the_same_files_at_the_batch_price(tmp_path, pricing):
    live_dir, queued_dir = tmp_path / "live", tmp_path / "queued"
    live = _job(live_dir, pricing, seat_with(_with_scans()), ScriptedClient())
    assert live.run().outcome == "delivered"
    client = QueueClient()
    queued = _job(queued_dir, pricing, seat_with(_with_scans()), client, batch_stages=["vision", "digest", "condense"])
    v = queued.run()
    assert v.outcome == "delivered", v.reason
    by_stage = {s: [r for r in _ledger(queued_dir) if r["stage"] == s] for s in ("vision", "digest", "compose")}
    assert by_stage["vision"] and all(r["batch"] for r in by_stage["vision"] + by_stage["digest"])
    assert not any(r["batch"] for r in by_stage["compose"])  # the drafting stages stay live
    assert len(client.batches) == 2  # one for the scanned pages, one for the digest round
    assert v.dollars < live.run().dollars
    for name in ("digest.md", "draft-final.md"):
        assert (queued_dir / "job" / "data" / name).read_text() == (live_dir / "job" / "data" / name).read_text()


def test_under_the_firms_line_the_job_runs_live(tmp_path, pricing):
    client = QueueClient()
    r = _job(
        tmp_path, pricing, seat_with(_with_scans()), client, batch_stages=["vision", "digest"], batch_over_usd=9999.0
    )
    assert r.run().outcome == "delivered"
    assert client.batches == [] and not any(row["batch"] for row in _ledger(tmp_path))


def test_a_batch_still_in_the_queue_stops_the_job_and_a_resume_collects_it_unpaid(tmp_path, pricing):
    seat, client = seat_with(_with_scans()), QueueClient()
    client.held = {"b1"}
    r = _job(tmp_path, pricing, seat, client, batch_stages=["vision"])
    r.doorway.max_wait_s = 0.0
    v = r.run()
    assert v.outcome == "failed" and "still in the batch queue" in v.reason and seat.sent == []
    assert _ledger(tmp_path) == [] if (tmp_path / "job" / "data" / "usage-ledger.jsonl").is_file() else True
    client.held = set()
    again = _job(tmp_path, pricing, seat, client)
    v2 = again.run()
    assert v2.outcome == "delivered", v2.reason
    assert len(client.batches) == 1  # the same batch, collected: nothing was submitted twice
    assert sum(1 for row in _ledger(tmp_path) if row["stage"] == "vision") == 2


def test_the_cap_binds_before_a_batch_is_submitted(tmp_path, pricing):
    inputs = _levers(tmp_path / "inputs", batch_stages=["vision"])
    cfg = inputs / "demand-firm.yaml"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    data["budget"].update(usd_per_scanned_page=30.0, per_job_cap_usd=20.0, monthly_budget_usd=5000.0)
    cfg.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    client = QueueClient()
    r = _job(tmp_path, pricing, seat_with(_with_scans()), client)
    r._estimate = lambda: None  # this test is about the batch's own check, not the pre-paid estimate
    v = r.run()
    assert v.outcome == "failed" and v.stage == "per_job_cap_usd" and client.batches == []


def test_condense_through_the_queue_keeps_the_groups_in_order(tmp_path):
    asked: list[str] = []

    class Door:
        batch_stages = frozenset({"condense"})

        def batch_call(self, stage, items, on_result, **kw):
            assert stage == "condense" and kw["batch_dir"] == tmp_path
            for it in reversed(items):  # the queue answers in any order
                asked.append(it.custom_id)
                on_result(it, SimpleNamespace(text="short " + it.messages[0]["content"][-6:]), None)

    digests = ["a" * 200_000 + "-ONE--", "b" * 200_000 + "-TWO--"]
    out = summarize.condense(digests, 1000, Door(), "m", "P {{WINDOW}}", 2, lambda _l: None, batch_dir=tmp_path)
    assert out == "short -ONE--\n\nshort -TWO--" and len(asked) == 2


# ---- the audit's cache ---------------------------------------------------------------
def test_the_first_audit_section_runs_alone_so_the_rest_read_its_cache(tmp_path, pricing):
    class Watching(ScriptedClient):
        def __init__(self) -> None:
            super().__init__()
            self.lock, self.active, self.seen = threading.Lock(), 0, []

        def _msg(self, params):
            audit = "AUDIT-PROMPT" in json.dumps(params.get("system"))
            if audit:
                with self.lock:
                    self.active += 1
                    self.seen.append(self.active)
                time.sleep(0.05)
            try:
                return super()._msg(params)
            finally:
                if audit:
                    with self.lock:
                        self.active -= 1

    client = Watching()
    r = _job(tmp_path, pricing, seat_with(standard_docs()), client)
    assert r.run().outcome == "delivered"
    assert (
        len(client.seen) >= 3 and client.seen[0] == 1 and client.seen[1] == 1
    )  # the second starts after the first ends
    assert max(client.seen) > 1  # the rest still run side by side

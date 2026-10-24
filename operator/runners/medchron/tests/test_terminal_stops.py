"""Endings that needed a person when they did not have to (independent review
at 4c88dce8, 2026-10-24). Policy: an infrastructure cause retries, bounded and
delayed (requeue.py); a real defect or a policy stop never retries; a done paid
stage is never paid for again on a retry.

1. review: a model call that returned no verdict exited 1, terminal, under a
   reason promising "a rerun reviews again". Now the infrastructure exit; a
   failed re-render keeps its own terminal code.
2. download: targets the vendor or network left unpulled exited 1, terminal
   (09-11, 09-14). Now the infrastructure exit, and the resumed pass pulls only
   what is missing.
3. "the runner exited 4 without a verdict" (09-16): a refusal whose report was
   lost. Still terminal, now saying what the runner decided.
4. exhibits: a cited file in the unresolved lane refused (09-16). Now settled
   by the citing entries' provider, or exhibited as Unattributed Records and
   named in Limitations.
5. upload: a send with an unknown outcome and a pre-flag delivery record shared
   the read-back-short exit, so each was re-run 4 x 900 s. Now their own codes.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron import dag, requeue, verdict
from medchron.stages import download as download_stage, exhibits as exhibits_stage, group as group_stage
from medchron.stages import limitations, review, review_policy, upload
from medchron_testkit import FakeSeat, make_pdf
from test_finish_stages import _sr as _finish_sr, firm  # noqa: F401 - `firm` is a pytest fixture used by name
from test_network_retry import _daemon, _submit
from test_review_proof import CLEAN, _package, _review
from test_stages import _sr as _stage_sr, seat, corpus  # noqa: F401 - `seat`/`corpus` are fixtures used by name
from test_upload_daemon import _upload_sr


class _Doorway:
    """The review's doorway, answering every call with one text and stop."""

    def __init__(self, text: str, stop: str) -> None:
        self.text, self.stop, self.calls = text, stop, 0

    def call(self, stage, **kw):
        self.calls += 1
        return SimpleNamespace(text=self.text, stop_reason=self.stop, usage=None)


def _failed(stage: str, code: int) -> dict:
    return {"outcome": "failed", "stage": stage, "exit_code": code, "reason": dag.BY_NAME[stage].exit_map[code][1]}


# ---- 1. review ---------------------------------------------------------------------
def test_a_review_with_no_verdict_is_infrastructure_and_the_daemon_retries_it(job_dir, firm_config_path, data_root):
    sr, log = _package(job_dir, firm_config_path, data_root)
    code = _review(sr, _Doorway("| NOTE | 5 x | y | z |", "end_turn"))
    assert code == review.EXIT_INFRA == requeue.INFRA_EXIT, log
    assert dag.BY_NAME["review"].exit_map[code][0] == dag.FAILED
    assert requeue.cause([_failed("review", code)], "failed", {}, "review") == "model_api"
    # The resume reviews again and passes: nothing earlier is re-run by the stage.
    assert _review(sr, _Doorway(CLEAN, "end_turn")) == 0


def test_a_refused_review_and_a_failed_rerender_stay_terminal_with_their_own_reasons(
    job_dir, firm_config_path, data_root, monkeypatch
):
    sr, _log = _package(job_dir, firm_config_path, data_root)
    code = _review(sr, _Doorway("I cannot help with that.", "refusal"))
    assert code == review.EXIT_INCOMPLETE
    assert requeue.cause([_failed("review", code)], "failed", {}, "review") is None
    monkeypatch.setattr(review_policy, "package", lambda sr, rd, found, rows, **kw: (found, None))
    code = _review(sr, _Doorway(CLEAN, "end_turn"))
    assert code == review.EXIT_RERENDER
    outcome, reason = dag.BY_NAME["review"].exit_map[code]
    assert outcome == dag.FAILED and "re-render" in reason and "rerun" not in reason
    assert requeue.cause([_failed("review", code)], "failed", {}, "review") is None


# ---- 2. download -------------------------------------------------------------------
def test_targets_left_unpulled_are_infrastructure_and_the_resume_pulls_only_the_rest(
    job_dir,
    firm_config_path,
    data_root,
    seat,
):
    from medchron import decisions
    from medchron_testkit import seed_seat_files

    seat.fail_mint.add("f2")
    seed_seat_files(data_root, seat)
    sr = _stage_sr(job_dir, firm_config_path, data_root, seat)
    decisions.selection(sr.job, sr.cfg, sr.slug_dir, dry_run=False)
    code = download_stage.run(sr)
    assert code == download_stage.EXIT_INFRA == requeue.INFRA_EXIT
    assert requeue.cause([_failed("download", code)], "failed", {}, "download") == "vendor"
    assert "vendor" in requeue.note("download", "vendor")
    seat.fail_mint.clear()
    seat.mints.clear()
    assert download_stage.run(sr) == 0
    assert seat.mints == [["f2"]], "the resume mints only the file the first pass missed"


def test_a_matter_with_nothing_to_chronicle_is_still_terminal_and_says_so() -> None:
    outcome, reason = dag.BY_NAME["download"].exit_map[download_stage.EXIT_NOTHING]
    assert outcome == dag.FAILED and "nothing to chronicle" in reason
    assert dag.BY_NAME["download"].exit_map[download_stage.EXIT_NAMED][0] == dag.HELD
    assert requeue.cause([_failed("download", 1)], "failed", {}, "download") is None


# ---- 3. a positive exit without a verdict ------------------------------------------
def test_exit_4_without_a_verdict_says_it_refused_and_is_not_retried(tmp_path) -> None:
    d, broker = _daemon(tmp_path, "import sys\nprint('a library banner')\nsys.exit(4)\n")
    _submit(d, broker, "01A")
    assert d.tick() == "failed"
    reason = broker.records[-1][2]["reason"]
    assert reason.startswith("the runner exited 4 without a verdict: it refused") and "defect" in reason
    assert requeue.cause([], "failed", {"reason": reason}, None) is None
    assert requeue.cause([], "failed", {"reason": verdict.lost(-15)}, None) == "killed"
    assert verdict.lost(-9) == "the runner exited -9 without a verdict"


# ---- 4. a cited file in the unresolved lane -----------------------------------------
def _sentinel_matter(sr, head: str) -> Path:
    d = sr.slug_dir
    raw = d / "raw"
    raw.mkdir(parents=True)
    (raw / "a.pdf").write_bytes(make_pdf(["", ""]))
    (raw / "s.pdf").write_bytes(make_pdf([""]))
    rows = [{"id": i, "ok": True, "path": str(raw / f"{i}.pdf")} for i in ("a", "s")]
    (d / "raw_manifest.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (d / "units").mkdir(parents=True)
    files = [
        {"id": "a", "name": "clinic note", "ext": ".pdf", "folder": "/MEDICAL"},
        {"id": "s", "name": "scan0001", "ext": ".pdf", "folder": "/(root)"},
    ]
    (d / "units" / "alpha.json").write_text(json.dumps(files))
    rd = d / "runs" / "alpha"
    rd.mkdir(parents=True)
    # The map cited the scan by a shortened name: assemble resolved it by
    # prefix, the group stage's exact-name key could not, so no row names it.
    (rd / "map-01.md").write_text(
        "## ENTRIES\n01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (FILE: clinic note.pdf, p. 1)\n\n"
        "01/05/2026\nExample Imaging | Diagnostics\n\nMRI. (FILE: scan00, p. 1)\n\n"
        "## INDEX\n2026-01-02 | Example Clinic | -- | clinic note.pdf\n\n## FILES-SEEN\n"
    )
    (rd / "exhibit_map.json").write_text(json.dumps({"clinic note.pdf": 1, "scan0001.pdf": 2}))
    (rd / "entries_scoped.md").write_text(
        "01/02/2026\nExample Clinic | Medical Diagnoses\n\nStrain. (Exhibit 1 - p. 2)\n\n"
        f"01/05/2026\n{head}\n\nMRI of the cervical spine. (Exhibit 2 - p. 1)\n"
    )
    return rd


def test_a_cited_file_in_the_unresolved_lane_takes_its_citing_entrys_provider(job_dir, firm, data_root) -> None:
    log: list[str] = []
    sr = _finish_sr(job_dir, firm, data_root, log=log)
    rd = _sentinel_matter(sr, "Example Imaging | Diagnostics")
    assert group_stage.run(sr) == 0
    groups = {g["provider"]: g for g in json.loads((sr.slug_dir / "groups" / "alpha.json").read_text())}
    assert groups[group_stage.Canon(sr.cfg).unresolved]["file_ids"] == ["s"], "the premise: it is in the lane"
    assert exhibits_stage.run(sr) == 0, log
    pm = json.loads((sr.slug_dir / "out" / "alpha" / "page_map.json").read_text())
    assert [e["provider"] for e in pm] == ["Example Clinic", "Example Imaging"]
    assert pm[1]["title"].startswith("Exhibit 2 - Example Imaging - 01-05-2026") and not pm[1].get("unattributed")
    assert "(Exhibit 2 - p. 1)" in (rd / "entries_final.md").read_text()


def test_a_cited_file_no_rule_can_attribute_is_exhibited_and_named_in_limitations(job_dir, firm, data_root) -> None:
    log: list[str] = []
    sr = _finish_sr(job_dir, firm, data_root, log=log)
    _sentinel_matter(sr, "Provider not identified | Diagnostics")
    assert group_stage.run(sr) == 0
    assert exhibits_stage.run(sr) == 0, log
    pm = json.loads((sr.slug_dir / "out" / "alpha" / "page_map.json").read_text())
    ex = next(e for e in pm if e.get("unattributed"))
    assert ex["title"].startswith(f"Exhibit 2 - {exhibits_stage.UNATTRIBUTED} - ")
    assert "not identified" not in ex["title"].lower(), "never the review's placeholder wording"
    text = "\n".join(limitations.section(sr.slug_dir, "alpha", lambda _m: None))
    assert 'Exhibit 2: "scan0001.pdf"' in text and "Unattributed Records" in text


def test_the_exhibits_stop_reason_no_longer_names_the_unresolved_lane() -> None:
    assert "unresolved" not in dag.BY_NAME["exhibits"].exit_map[1][1]


# ---- 5. upload: only the short read-back is a wait -----------------------------------
def _upload_reason(code: int) -> str:
    return dag.BY_NAME["upload"].exit_map[code][1]


def test_a_failed_send_holds_on_its_own_code_and_is_not_re_run(job_dir, firm_config_path, data_root) -> None:
    seat = FakeSeat([], [], {})
    seat.crash_after = 1
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, [])
    code = upload.run(sr, pause=0, tries=2)
    assert code == upload.EXIT_SEND_UNKNOWN
    assert dag.BY_NAME["upload"].exit_map[code][0] == dag.HELD
    held = [{"outcome": "held", "stage": "upload"}]
    assert requeue.cause(held, "held", {"reason": _upload_reason(code)}, "upload") is None
    assert requeue.cause(held, "held", {"reason": _upload_reason(upload.EXIT_SHORT)}, "upload") == "upload_slow"


def test_a_pre_flag_delivery_record_holds_on_its_own_code_and_is_not_re_run(
    job_dir, firm_config_path, data_root
) -> None:
    seat = FakeSeat([], [], {})
    sr = _upload_sr(job_dir, firm_config_path, data_root, seat, [])
    rows = [{"name": "x.pdf", "sha256": "y", "bytes": 6, "confirmed": False}]
    (sr.slug_dir / "runs" / "alpha" / "delivery.json").write_text(
        json.dumps({"folder": "F", "folder_id": "folder-legacy", "files": rows})
    )
    code = upload.run(sr, pause=0, tries=2)
    assert code == upload.EXIT_PRE_FLAG and seat.sent == []
    held = [{"outcome": "held", "stage": "upload"}]
    assert requeue.cause(held, "held", {"reason": _upload_reason(code)}, "upload") is None
    assert _upload_reason(code).startswith("upload:"), "the upload_held hold class keys on the prefix"

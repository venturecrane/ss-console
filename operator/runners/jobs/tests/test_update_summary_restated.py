"""The added prior history never restates a new dated entry's own cited
sentence (2026-11-20: the update's summary step repeated one word for word,
assemble carried both, and proof 7 failed the job after every paid stage).
Through the real stage, its real doorway and a scripted client; every value
is invented."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace as NS

import update_testkit as K
from jobs.medchron import config as config_mod, job as job_mod
from jobs.medchron.update import proofs as PF
from jobs.stages import update_collect as UC
from jobs.stages.base import StageRun

PRE = (
    "11/03/2025\nExample Imaging | Medical Diagnoses\n\nMedical Diagnoses\n"
    "Radiographs of the neck showed straightening of the curve. (Exhibit 2 - p. 1)"
)
SAME = "Radiographs of the neck showed straightening of the curve. (Exhibit 2 - p. 1)"
OTHER = "Before the incident, the neck curve was straightened on imaging. (Exhibit 2 - p. 1)"


class Usage:
    input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens = 100, 20, 0, 0


class Scripted:
    def __init__(self, text: str) -> None:
        self.text = text
        self.messages = NS(create=self._create, stream=None, batches=None)

    def _create(self, **_params):
        return NS(content=[NS(type="text", text=self.text)], stop_reason="end_turn", usage=Usage())


def _sr(job_dir: Path, firm: Path, data_root: Path, reply: str, log: list[str]) -> StageRun:
    job = job_mod.load(job_dir)
    sr = StageRun(
        job=job,
        cfg=config_mod.load(str(firm)),
        unit=job.units[0],
        slug_dir=data_root / "example-matter",
        decided={},
        log=log.append,
        seat_factory=lambda: None,  # type: ignore[arg-type, return-value]
        client_factory=lambda: Scripted(reply),
    )
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    ud.mkdir(parents=True)
    (ud / "base.md").write_text(K.base_md())
    (ud / "update_plan.json").write_text(json.dumps({"exhibits": []}))
    (ud / "new_exhibits.final.json").write_text(json.dumps([{"exhibit": 2, "id": "n1", "title": "Exhibit 2 - X"}]))
    (ud / "new_entries.md").write_text(PRE + "\n")
    (sr.slug_dir / "manifest.json").write_text(json.dumps([{"id": "n1", "created": "2026-10-01T15:04:00Z"}]))
    return sr


def _proof7(lines: list[str]) -> bool:
    md = K.base_md()
    x = PF.Inputs([], lines, md, md, [], [], [], [], [], set())
    return "7" not in PF.run(x).fails


def test_a_restated_sentence_is_removed_and_nothing_else(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, f"Prior Medical History\n{SAME}\n{OTHER}", log)
    assert UC.run_summary(sr) == 0
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    pmh = (ud / "pmh.md").read_text()
    assert pmh == f"Additional prior history from records filed through 10-01-26\n{OTHER}\n"
    assert json.loads((ud / "summary_report.json").read_text()) == {"restated_removed": 1}
    entry_lines = PRE.splitlines()
    assert _proof7(entry_lines + pmh.splitlines())
    assert not _proof7(entry_lines + [SAME, OTHER])  # without the removal, proof 7 fails


def test_a_block_with_nothing_in_common_is_byte_identical(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    text = f"{OTHER} Imaging was of the neck. (Exhibit 2 - p. 1)"
    sr = _sr(job_dir, firm_config_path, data_root, f"Prior Medical History\n{text}", log)
    assert UC.run_summary(sr) == 0
    ud = sr.slug_dir / "runs" / "alpha" / "update"
    assert (ud / "pmh.md").read_text() == f"Additional prior history from records filed through 10-01-26\n{text}\n"
    assert json.loads((ud / "summary_report.json").read_text()) == {"restated_removed": 0}


def test_a_block_that_only_restates_adds_no_prior_history(
    job_dir: Path, firm_config_path: Path, data_root: Path
) -> None:
    log: list[str] = []
    sr = _sr(job_dir, firm_config_path, data_root, f"Prior Medical History\n{SAME}", log)
    assert UC.run_summary(sr) == 0
    rd = sr.slug_dir / "runs" / "alpha"
    assert not (rd / "update" / "pmh.md").exists()
    assert "Additional prior history" not in (rd / "final-chronology.md").read_text()


def test_only_an_exact_sentence_matches() -> None:
    near = "Radiographs of the neck showed straightening of the curves. (Exhibit 2 - p. 1)"
    assert PF.drop_restated(near, [PRE]) == (near, 0)
    two = f"{SAME} {OTHER}"
    assert PF.drop_restated(two, [PRE]) == (OTHER, 1)


#: A citation before the sentence's period (the shape a summary writes too).
INNER = "01/02/2026\nExample Clinic | Medical Diagnoses\n\nMedical Diagnoses\n" + (
    "Neck pain was reported (Exhibit 2 - p. 4). A radiograph was normal (Exhibit 2 - p. 5)."
)


def _proofs_pass(lines: list[str], *codes: str) -> bool:
    md = K.base_md()
    fails = PF.run(PF.Inputs([], lines, md, md, [], [], [], [], [], set())).fails
    return not set(codes) & set(fails)


def test_a_citation_before_the_period_leaves_no_stray_period() -> None:
    text = "Neck pain was reported (Exhibit 2 - p. 4). Migraines were noted (Exhibit 2 - p. 6)."
    got, n = PF.drop_restated(text, [INNER])
    assert (got, n) == ("Migraines were noted (Exhibit 2 - p. 6).", 1)
    assert _proofs_pass(INNER.splitlines() + [got], "6", "7")
    # removed in the middle: the period before the next sentence stays where it was
    mid = (
        "Migraines were noted (Exhibit 2 - p. 6). Neck pain was reported (Exhibit 2 - p. 4). Falls (Exhibit 2 - p. 7)."
    )
    assert PF.drop_restated(mid, [INNER])[0] == "Migraines were noted (Exhibit 2 - p. 6). Falls (Exhibit 2 - p. 7)."


def test_a_line_whose_every_sentence_is_removed_leaves_nothing() -> None:
    text = "Neck pain was reported (Exhibit 2 - p. 4). A radiograph was normal (Exhibit 2 - p. 5)."
    assert PF.drop_restated(text, [INNER]) == ("", 2)
    two = text + "\nMigraines were noted. (Exhibit 2 - p. 6)"
    assert PF.drop_restated(two, [INNER]) == ("Migraines were noted. (Exhibit 2 - p. 6)", 2)

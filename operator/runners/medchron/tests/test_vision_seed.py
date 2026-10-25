"""An update reuses the delivered run's page readings (``stages/vision_seed``)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from medchron.stages import vision_seed

MATTER = "m-1"


def _job_dir(
    jobs: Path, name: str, *, state: str = "delivered", matter: str = MATTER, finished: str = "2026-10-25T07:30:00Z"
) -> Path:
    jd = jobs / name
    (jd / "data" / "firm" / "partial").mkdir(parents=True)
    (jd / "daemon.json").write_text(json.dumps({"state": state, "finished_at": finished}))
    (jd / "job.yaml").write_text(f"slug: firm\nmatter:\n  id: {matter}\n")
    return jd


def _manifest(sd: Path, rows: dict[str, str]) -> None:
    (sd / "raw_manifest.jsonl").write_text(
        "".join(json.dumps({"id": k, "ok": True, "sha256": v, "path": f"/x/{k}.pdf"}) + "\n" for k, v in rows.items())
    )


def _partial(sd: Path, doc: str, pages: dict[int, str]) -> None:
    (sd / "partial" / f"{doc}.jsonl").write_text(
        "".join(json.dumps({"page": p, "text": t}) + "\n" for p, t in pages.items())
    )


class _Job(SimpleNamespace):
    @property
    def prior_delivered(self) -> list[str]:
        """What the daemon (root) writes into job.yaml at staging."""
        return vision_seed.prior_delivered(Path(self.install_root) / "jobs", MATTER, "UPD")


def _update(tmp_path: Path, *, update: object = True) -> SimpleNamespace:
    jobs = tmp_path / "run" / "jobs"
    jd = _job_dir(jobs, "UPD", state="claimed")
    log: list[str] = []
    job = _Job(update=object() if update else None, install_root=str(tmp_path / "run"), matter_id=MATTER)
    return SimpleNamespace(job=job, slug_dir=jd / "data" / "firm", log=log.append, logs=log, jobs=jobs)


def _read(sd: Path, doc: str) -> dict[int, str]:
    p = sd / "partial" / f"{doc}.jsonl"
    return {json.loads(ln)["page"]: json.loads(ln)["text"] for ln in p.read_text().splitlines()} if p.is_file() else {}


def test_an_update_reuses_the_delivered_runs_pages_for_the_same_file(tmp_path):
    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "BASE") / "data" / "firm"
    _manifest(base, {"d1": "aaa"})
    _partial(base, "d1", {1: "page one", 2: "page two"})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    assert vision_seed.seed(sr, [{"id": "d1"}]) == 2
    assert _read(sr.slug_dir, "d1") == {1: "page one", 2: "page two"}
    assert any("reused" in m for m in sr.logs)


def test_failed_and_refused_pages_are_not_copied(tmp_path):
    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "BASE") / "data" / "firm"
    _manifest(base, {"d1": "aaa"})
    _partial(base, "d1", {1: "page one", 2: "[page transcription failed]", 3: "[page transcription refused]"})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    assert vision_seed.seed(sr, [{"id": "d1"}]) == 1
    assert _read(sr.slug_dir, "d1") == {1: "page one"}


def test_a_different_file_under_the_same_id_is_paid_for(tmp_path):
    """Falsifier: same id, different bytes (the firm replaced the file)."""
    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "BASE") / "data" / "firm"
    _manifest(base, {"d1": "aaa"})
    _partial(base, "d1", {1: "old reading"})
    _manifest(sr.slug_dir, {"d1": "bbb"})
    assert vision_seed.seed(sr, [{"id": "d1"}]) == 0 and _read(sr.slug_dir, "d1") == {}


def test_only_a_delivered_job_on_the_same_matter_is_a_source(tmp_path):
    sr = _update(tmp_path)
    for name, kw in (("HELD", {"state": "held"}), ("OTHER", {"matter": "m-2"})):
        sd = _job_dir(sr.jobs, name, **kw) / "data" / "firm"
        _manifest(sd, {"d1": "aaa"})
        _partial(sd, "d1", {1: "x"})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    assert vision_seed.seed(sr, [{"id": "d1"}]) == 0


def test_the_newest_delivered_job_wins(tmp_path):
    sr = _update(tmp_path)
    old = _job_dir(sr.jobs, "OLD", finished="2026-10-20T00:00:00Z") / "data" / "firm"
    new = _job_dir(sr.jobs, "NEW", finished="2026-10-25T00:00:00Z") / "data" / "firm"
    for sd, text in ((old, "older"), (new, "newer")):
        _manifest(sd, {"d1": "aaa"})
        _partial(sd, "d1", {1: text})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    vision_seed.seed(sr, [{"id": "d1"}])
    assert _read(sr.slug_dir, "d1") == {1: "newer"}


def test_a_resume_keeps_its_own_checkpoint_and_a_run_is_never_seeded(tmp_path):
    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "BASE") / "data" / "firm"
    _manifest(base, {"d1": "aaa", "d2": "ccc"})
    _partial(base, "d1", {1: "base"})
    _partial(base, "d2", {1: "base"})
    _manifest(sr.slug_dir, {"d1": "aaa", "d2": "ccc"})
    _partial(sr.slug_dir, "d1", {1: "mine"})
    assert vision_seed.seed(sr, [{"id": "d1"}, {"id": "d2"}]) == 1
    assert _read(sr.slug_dir, "d1") == {1: "mine"}
    run = _update(tmp_path / "r", update=False)
    assert vision_seed.seed(run, [{"id": "d1"}]) == 0


def test_the_child_reads_named_jobs_by_path_in_a_dir_it_cannot_list(tmp_path):
    """2026-10-25: the first proof run listed /run/smd-medchron/jobs from the
    child's uid (0710 root:medchron) and died on PermissionError. The daemon
    names the jobs; the child only enters them. Falsifier: listing the same
    unlistable dir finds nothing, and does not raise."""
    import os

    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "01M5BASE00000000000000000A") / "data" / "firm"
    _manifest(base, {"d1": "aaa"})
    _partial(base, "d1", {1: "page one"})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    named = vision_seed.prior_delivered(sr.jobs, MATTER, "UPD")
    assert named == ["01M5BASE00000000000000000A"]
    job = SimpleNamespace(update=object(), install_root=sr.job.install_root, prior_delivered=named)
    mode = sr.jobs.stat().st_mode
    os.chmod(sr.jobs, 0o311)
    try:
        assert vision_seed.prior_delivered(sr.jobs, MATTER, "UPD") == []
        assert vision_seed.delivered_same_matter(job, "firm") == [base]
        assert vision_seed.seed(SimpleNamespace(job=job, slug_dir=sr.slug_dir, log=sr.log), [{"id": "d1"}]) == 1
    finally:
        os.chmod(sr.jobs, mode)


def test_no_hash_on_either_side_means_no_reuse(tmp_path):
    sr = _update(tmp_path)
    base = _job_dir(sr.jobs, "BASE") / "data" / "firm"
    _partial(base, "d1", {1: "base"})
    _manifest(sr.slug_dir, {"d1": "aaa"})
    assert vision_seed.seed(sr, [{"id": "d1"}]) == 0

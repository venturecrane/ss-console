"""The lane's store of delivered form answers is purged by job or by matter
(``python -m jobs.drafting.forms.saved purge``). Every value here is invented."""

from __future__ import annotations

import json
from pathlib import Path

from jobs.drafting.forms import saved


def _job(root: Path, job_id: str, matter: str) -> Path:
    jd = root / "jobs" / job_id
    (jd / "data" / "set-1").mkdir(parents=True)
    (jd / "job.json").write_text(json.dumps({"job_id": job_id, "matter_id": matter}))
    rec = {"label": "Form set", "answers": [{"number": "1.1", "kind": "answered", "items": []}]}
    (jd / "data" / "set-1" / saved.UNIT_FILE).write_text(json.dumps(rec))
    return jd / "data"


def test_purge_by_matter_and_by_job(tmp_path: Path, capsys) -> None:
    run = tmp_path / "run"
    for job_id, matter in (("J1", "M-100"), ("J2", "M-100"), ("J3", "M-200")):
        assert saved.keep(run, job_id, _job(tmp_path, job_id, matter))
    store = run / saved.STORE
    assert json.loads((store / "J1.json").read_text())["matter_id"] == "M-100"

    assert saved.main(["purge", "--matter", "M-100", "--run-dir", str(run)]) == 0
    assert sorted(p.name for p in store.iterdir()) == ["J3.json"]
    assert "removed 2 record(s)" in capsys.readouterr().out

    assert saved.purge(run, job="J3") == ["J3.json"]
    assert not list(store.iterdir())
    assert saved.purge(run, matter="M-404") == []

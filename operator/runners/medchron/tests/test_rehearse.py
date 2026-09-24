"""`medchron rehearse`: every $0 gate against a COPY of the workdir, nothing
spent, nothing real touched.

Built after a client's chronology died three times on three gates, each
decidable from artifacts already on disk. Every test here pins one of the
promises: the real tree is byte-identical afterwards, the seat and the model
are never opened, and the gates BEHIND the paid stage are answered anyway.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from medchron import __main__ as cli, dag, job as job_mod, rehearsal
from medchron.state import RunState
from medchron_testkit import job_yaml, seed_folders
from test_decisions_driver import REAL_MAP, _NoNetwork, _controls, _driver

pytestmark = pytest.mark.usefixtures("fake_pipeline")


def _no_seat():
    raise AssertionError("the seat was opened during a rehearsal")


def _snapshot(root: Path) -> dict[str, str]:
    """sha256 of every file under root, so 'unchanged' is a byte claim."""
    out: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            out[str(p.relative_to(root))] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def _delivered(tmp_path: Path, data_root: Path, firm: Path, pricing: Path) -> tuple[Path, _NoNetwork]:
    """The whole DAG, in-process, to `delivered`: the fixture that leaves every
    $0 stage's real artifact on disk (mirrors test_the_whole_dag_runs...)."""
    job_dir, install_root = tmp_path / "job", tmp_path / "install"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, install_root=install_root), encoding="utf-8")
    client = _NoNetwork()
    outs = _driver(job_dir, firm, pricing, client=client).run()
    assert outs[0].outcome == "delivered", outs[0]
    return job_dir, client


def _truncate(sd: Path, keep_through: str, drop: set[str] = frozenset()) -> None:
    """Rewrite the REAL state so only stages up to `keep_through` are done."""
    st = RunState.load_or_new(sd / "runs" / "alpha" / "state.json", slug="example-matter", unit="alpha")
    cut = dag.ORDER.index(keep_through)
    for name, rec in st.stages.items():
        if dag.ORDER.index(name) > cut or name in drop:
            rec.status = "pending"
    st.outcome = None
    st.outcome_reason = None
    st.save()


def _rehearse(job_dir: Path, firm: Path, pricing: Path, **kw):
    """Controls are seeded by the CALLER before any snapshot: on a bare job
    install_root is data_root, and seeding there is the test writing, not the
    rehearsal."""
    kw.setdefault("seat_factory", _no_seat)
    kw.setdefault("client", _NoNetwork())
    return rehearsal.run(job_dir, firm_config=str(firm), pricing=str(pricing), log=lambda *_: None, **kw)


def _stopped_at(out) -> str | None:
    return out.stage


# ---- the contract -----------------------------------------------------------
def test_a_bare_job_stops_at_the_first_external_stage_and_touches_nothing_real(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    _controls(job_mod.load(job_dir).install_root)
    before = _snapshot(data_root)
    client = _NoNetwork()
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    o = outs[0]
    assert o.outcome == "rehearsed" and _stopped_at(o) == "list_matter"
    assert any(n.startswith("rehearse list_matter: STOP external") for n in o.notes), o.notes
    assert _snapshot(data_root) == before, "the real tree must be byte-identical"
    assert client.calls == []
    assert (copy / "job.yaml").is_file() and (copy / "data" / "example-matter").is_dir()
    assert any(n.startswith("rehearse summary: workdir ") for n in o.notes)


def test_free_stages_run_in_the_copy_and_the_first_paid_stage_stops_with_a_projection(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    _truncate(sd, "extract_after_fold", drop={"decide_selection", "decide_fold"})
    (sd / "include.json").unlink()  # the cleared decision must re-author it -- in the copy
    (sd / "runs" / "alpha" / "final-chronology.md").unlink()  # a real dead tree has none this early
    before = _snapshot(sd)
    calls = len(client.calls)
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    o = outs[0]
    assert _stopped_at(o) == "vision" and o.outcome == "rehearsed"
    assert "rehearse decide_selection: ok" in o.notes and "rehearse decide_fold: ok" in o.notes
    assert any(n.startswith("rehearse vision: STOP paid, not done (~") for n in o.notes), o.notes
    assert (copy / "data" / "example-matter" / "include.json").is_file(), "authored in the copy"
    assert not (sd / "include.json").exists(), "and NOT in the real tree"
    assert _snapshot(sd) == before, "real state.json and every real artifact byte-identical"
    assert len(client.calls) == calls
    # no chronology yet, so the audit has no count: it must read as unprojected,
    # never as "audit 0.00" (a zero here would be the costliest late stage
    # printed as free)
    unproj = [n for n in o.notes if n.startswith("rehearse summary: unprojected")]
    assert unproj and "audit" in unproj[0], o.notes
    assert not any("audit 0.00" in n for n in o.notes), o.notes


def test_merge_reports_its_routing_without_the_model(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    rd = sd / "runs" / "alpha"
    # a second composed chunk: same date and provider, one number changed, so
    # assemble makes a cluster the router must send to the model (two-sided
    # number conflict) and the probe has something to count
    (rd / "map-02.md").write_text(REAL_MAP.replace("6 of 10", "8 of 10"), encoding="utf-8")
    assert (rd / "map-02.md").read_text() != REAL_MAP
    _truncate(sd, "repair_truncated")
    calls = len(client.calls)
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    o = outs[0]
    assert "rehearse assemble: ok" in o.notes
    assert _stopped_at(o) == "merge"
    routing = [n for n in o.notes if n.startswith("rehearse merge: ") and "routed to the model" in n]
    assert routing and "1 routed to the model" in routing[0], o.notes
    # the histogram reports whatever the router said (here the fixture firm's
    # heading menu does not list this heading, so it is a parse route); the
    # probe's job is to count kinds, not to editorialise
    assert any(n.startswith("rehearse merge:   ") and "x " in n for n in o.notes), o.notes
    assert len(client.calls) == calls, "the probe never calls the model"
    assert not (copy / "data" / "example-matter" / "runs" / "alpha" / "merged.md").exists() or (
        (copy / "data" / "example-matter" / "runs" / "alpha" / "merged.md").read_text() == ""
    ), "merge's paid half wrote nothing"


def test_the_gates_behind_the_paid_stage_are_answered_anyway(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """The point of the whole thing. On every real dead tree the walk stops at
    the paid stage that killed the run, and coverage and audit sit behind it.
    The standalone probes answer both from what exists."""
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    _truncate(sd, "assemble")
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    o = outs[0]
    assert _stopped_at(o) == "merge"
    cov = [n for n in o.notes if n.startswith("rehearse coverage_gate: ")]
    assert cov and "unit file(s); with nothing cited:" in cov[0], o.notes
    aud = [n for n in o.notes if n.startswith("rehearse audit: ")]
    assert aud and "claims" in aud[0] and "USD/claim" in aud[0], o.notes


def test_an_undone_external_stage_stops_the_walk_and_never_opens_the_seat(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    _truncate(sd, "manifest")
    before = _snapshot(sd)
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client, seat_factory=_no_seat)
    o = outs[0]
    assert _stopped_at(o) == "upload"
    assert any(n == "rehearse upload: STOP external, not done (would write to the firm's matter)" for n in o.notes)
    assert _snapshot(sd) == before


def test_the_audit_is_projected_from_the_built_chronology(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    _truncate(sd, "billing_docx")
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    o = outs[0]
    assert _stopped_at(o) == "audit"
    assert any(n.startswith("rehearse audit: STOP paid, not done (~") for n in o.notes), o.notes
    aud = [n for n in o.notes if n.startswith("rehearse audit: ") and "from the built chronology" in n]
    assert aud, o.notes
    assert any(n.startswith("rehearse summary: projected ") and "audit " in n for n in o.notes)
    # a resume projects only what is still unverified: verdicts on disk for THIS
    # body cost nothing again (live 2026-09-16: a resume with 1,998 paid
    # verdicts was held at the cap on a projection that re-counted them all)
    import re

    from medchron.audit import claims as CL
    from medchron.audit.page_text import exhibit_paths

    def projected(notes: list[str]) -> float:
        m = next(re.search(r"STOP paid, not done \(~([0-9.]+) USD\)", n) for n in notes if "rehearse audit: STOP" in n)
        return float(m.group(1))

    # the delivered fixture's own audit verdicts are on disk, so the first rehearsal
    # above already projected 0; remove them to see the full projection
    results = sd / "out" / "alpha" / "audit-results.jsonl"
    assert projected(o.notes) == 0.0 and results.is_file()
    results.unlink()
    _copy1, outs1 = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    full = projected(outs1[0].notes)
    body = CL.body_of((sd / "runs" / "alpha" / "final-chronology.md").read_text(encoding="utf-8"))
    claims = CL.extract_claims(body, set(exhibit_paths(sd / "out" / "alpha")))
    assert claims and full > 0
    rows = [{"key": c["key"], "kind": "real", "verdict": "SUPPORTED", "doc_sha": CL.doc_sha_of(body)} for c in claims]
    results.write_text("".join(json.dumps(r) + "\n" for r in rows))
    _copy2, outs2 = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    assert projected(outs2[0].notes) == 0.0, "every claim already verified for this body: nothing to re-bill"
    # verdicts for a DIFFERENT body do not count
    results.write_text("".join(json.dumps({**r, "doc_sha": "another-body"}) + "\n" for r in rows))
    _copy3, outs3 = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    assert projected(outs3[0].notes) == full


def test_redo_reopens_a_free_stage_in_the_copy_only_and_refuses_paid_or_external(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    _truncate(sd, "assemble")
    before = _snapshot(sd)
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client, redo=("build_units",))
    o = outs[0]
    assert "rehearse build_units: ok" in o.notes, "reopened and re-run in the copy"
    assert "rehearse identity: skipped (done)" in o.notes, "everything else stays done"
    assert _snapshot(sd) == before, "the real state is never reopened"
    for bad in ("vision", "upload", "nonsense"):
        with pytest.raises(Exception, match="--redo"):
            _rehearse(job_dir, firm_config_path, pricing_path, client=client, redo=(bad,))


def test_rehearse_refuses_while_the_seat_is_running_a_job(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    _controls(job_mod.load(job_dir).install_root)
    hb = job_mod.load(job_dir).install_root / "heartbeat.json"
    hb.parent.mkdir(parents=True, exist_ok=True)
    hb.write_text(json.dumps({"running": "01LIVEJOB"}), encoding="utf-8")
    with pytest.raises(rehearsal.RehearsalError, match="01LIVEJOB"):
        _rehearse(job_dir, firm_config_path, pricing_path)
    hb.write_text(json.dumps({"running": None}), encoding="utf-8")
    _copy, outs = _rehearse(job_dir, firm_config_path, pricing_path)
    assert outs[0].outcome == "rehearsed"


def test_the_copy_links_the_input_folders_and_refuses_a_volume_that_cannot_hold_it(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-09-16: copying msg_pdfs/ and msg_raw/ (most of a 1.2 GB job)
    filled the seat's 10 GB volume to zero, which stalls the daemon too. The
    inputs are linked, and a volume that cannot hold twice the copy refuses
    before a byte is written."""
    job_dir, _client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    sd = data_root / "example-matter"
    for name in ("msg_pdfs", "msg_raw"):
        (sd / name).mkdir(exist_ok=True)
        (sd / name / "big.bin").write_bytes(b"\0" * 4096)
    before = _snapshot(sd)
    copy, outs = _rehearse(job_dir, firm_config_path, pricing_path)
    assert outs[0].outcome == "rehearsed"
    cs = copy / "data" / "example-matter"
    assert all((cs / n).is_symlink() for n in ("raw", "msg_pdfs", "msg_raw")), sorted(p.name for p in cs.iterdir())
    assert (cs / "text").is_dir() and not (cs / "text").is_symlink(), "text/ is written by extract; copied"
    assert _snapshot(sd) == before
    # the refusal: free space below twice what the copy would write
    need = rehearsal._copy_bytes(sd)
    assert need > 0
    monkeypatch.setattr(rehearsal.shutil, "disk_usage", lambda _p: type("U", (), {"free": need * 2 - 1})())
    n_before = len(list((job_dir / "rehearsal").iterdir()))
    with pytest.raises(rehearsal.RehearsalError, match="free space first"):
        _rehearse(job_dir, firm_config_path, pricing_path)
    assert len(list((job_dir / "rehearsal").iterdir())) == n_before, "nothing written under rehearsal/"


def test_rehearse_and_from_are_exclusive(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    _controls(job_mod.load(job_dir).install_root)
    with pytest.raises(Exception, match="--from"):
        _rehearse(job_dir, firm_config_path, pricing_path, start="vision")


def test_rehearse_cli_json_stdout_is_only_the_verdict(
    job_dir: Path, data_root: Path, firm_config_path: Path, pricing_path: Path, capsys: pytest.CaptureFixture
) -> None:
    seed_folders(data_root, ["MEDICAL"])
    _controls(job_mod.load(job_dir).install_root)
    rc = cli.main(
        ["rehearse", str(job_dir), "--firm-config", str(firm_config_path), "--pricing", str(pricing_path), "--json"]
    )
    captured = capsys.readouterr()
    assert rc == 0
    verdict = json.loads(captured.out)
    assert verdict[0]["outcome"] == "rehearsed" and verdict[0]["stage"] == "list_matter"
    assert "[rehearse] workdir" in captured.err


def test_a_done_slug_stage_is_done_for_every_unit_of_a_joint_matter(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """The live bug the rehearsal design exposed (driver.py run_unit): the
    done-skip for a slug-scope stage never fed `slug_done`, so a joint matter's
    SECOND unit found `list_matter` not done in its own state.json and re-ran
    it -- and re-paid `vision` -- on every resume. Here unit beta has no state
    at all; with the fix its walk skips every slug stage alpha already did and
    stops at the first unit-scope paid stage. Without it, beta stops at
    `list_matter`, which is exactly the re-run.

    Falsifier: remove the `slug_done.add` from `_note_skip` and beta's stage
    reads `list_matter`.
    """
    job_dir, client = _delivered(tmp_path, data_root, firm_config_path, pricing_path)
    (job_dir / "job.yaml").write_text(
        job_yaml(data_root, joint=True, install_root=tmp_path / "install"), encoding="utf-8"
    )
    _copy, outs = _rehearse(job_dir, firm_config_path, pricing_path, client=client)
    by_unit = {o.unit: o for o in outs}
    assert by_unit["alpha"].stage is None, "alpha is done through and through"
    assert by_unit["beta"].stage == "map", by_unit["beta"].notes
    assert not any(n.startswith("rehearse list_matter: STOP") for n in by_unit["beta"].notes)


# ---- the same probes, in a REAL run, before the money (ss#2911) --------------


def test_a_paid_stage_answers_its_own_free_probe_before_it_spends(
    tmp_path: Path, data_root: Path, firm_config_path: Path, pricing_path: Path
) -> None:
    """Two stages carry a `rehearse` probe and both are paid. Until this change
    they ran only inside `medchron rehearse` -- a hand command that, probed on
    the live seat 2026-09-24, had never been run on any of six job dirs. A gate
    nobody invokes is not a gate, so a real run now answers each paid stage's
    own probe immediately before that stage spends.

    `merge` reports what the code merged and what goes to the model, which is
    the shape of the live 62.01 USD failure where the model could not merge a
    routed cluster. `audit` reports the claim count times the rate -- what its
    own docstring calls "the cap answer, at $0".

    Falsifier: drop the `probe_before_spend` call from `driver._execute` and a
    real run carries no `rehearse ` note at all.
    """
    job_dir, install_root = tmp_path / "job", tmp_path / "install"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, install_root=install_root), encoding="utf-8")
    outs = _driver(job_dir, firm_config_path, pricing_path, client=_NoNetwork()).run()
    assert outs[0].outcome == "delivered", outs[0]  # the run really reached both paid probes
    notes = outs[0].notes
    merge_lines = [n for n in notes if n.startswith("rehearse merge: ")]
    audit_lines = [n for n in notes if n.startswith("rehearse audit: ")]
    assert merge_lines and "clusters" in merge_lines[0], notes
    assert audit_lines and "claims" in audit_lines[0], notes


def test_a_paid_stage_without_a_probe_adds_no_noise(data_root: Path) -> None:
    """Seven of the nine paid stages carry no probe. The guard returns before
    it touches the driver at all, which is why passing None here is safe -- and
    why removing the guard makes this test raise AttributeError rather than
    quietly printing empty lines on every run."""
    notes: list[str] = []
    rehearsal.probe_before_spend(None, dag.BY_NAME["vision"], None, notes)
    assert dag.BY_NAME["vision"].paid and dag.BY_NAME["vision"].rehearse is None
    assert notes == []


def test_a_probe_that_raises_is_a_line_not_a_crash() -> None:
    """An observer that can end the run it observes is worse than no observer."""

    def boom(_sr):
        raise RuntimeError("clusters.md is a directory")

    stage = dag.BY_NAME["merge"]
    original = stage.rehearse
    object.__setattr__(stage, "rehearse", boom)
    try:
        lines = rehearsal.probe_lines(object(), stage)
    finally:
        object.__setattr__(stage, "rehearse", original)
    assert lines == ["rehearse merge: probe could not run: RuntimeError: clusters.md is a directory"]

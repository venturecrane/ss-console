"""The lane-proof gate (lane_proof.py, proof_run.py, lane_proof_cli.py): a job
of a kind not proven on this seat waits and is noted once; a proof run is the
lane's real pipeline with nothing written and nothing recorded; ``accept`` of a
passed proof releases the waiting job, which then runs exactly as before.

Every test here runs the REAL gate (the ``real_lane_gate`` fixture opts out of
conftest's every-lane-proven default)."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pytest
import yaml

from demand_testkit import job_doc, make_inputs
from medchron import lane_proof, lane_proof_cli, proof_mode, proof_run
from medchron.demand_lane import DemandBroker, DemandLane

pytestmark = pytest.mark.usefixtures("real_lane_gate")

VFY = "vfy_01M4TZKFAKAC8KYD9Q7HB0S4AB"

# A runner that does what a real one does at its write stage: in a proof run it
# returns dry_run and files nothing; in a live run it files (a marker file the
# test can see) and delivers. It also records the env it was given.
RUNNER = """
import json, os, sys, pathlib
jd = pathlib.Path(sys.argv[1])
(jd / 'env.json').write_text(json.dumps({'proof': os.environ.get('MEDCHRON_PROOF_RUN'),
                                        'job': os.environ.get('MEDCHRON_DAEMON_JOB_ID')}))
out = jd / 'data' / 'out'
out.mkdir(parents=True, exist_ok=True)
(out / 'Demand.docx').write_text('draft')
if os.environ.get('MEDCHRON_PROOF_RUN') == '1':
    v = [{"unit": "demand", "outcome": "dry_run", "stage": "file", "dollars": 4.25, "files": []}]
else:
    (jd.parent.parent / 'FILED').write_text(jd.name)
    v = [{"unit": "demand", "outcome": "delivered", "stage": "file", "dollars": 4.25,
          "folder_id": "f1", "files": [{"name": "Demand.docx", "size": 5, "role": "demand"}],
          "proof": {"readback": "confirmed", "review": "passed", "review_cents": 0, "review_model": "m",
                    "files": [{"name": "Demand.docx", "file_id": "u1", "size": 5, "sha256": "a" * 64}]}}]
(jd / 'verdict.json').write_text(json.dumps(v))
print(json.dumps(v))
"""
# A runner that IGNORES the proof flag and files anyway: the outcome is not
# dry_run, so the proof must fail (and the connector refusal is tested below).
RECKLESS = RUNNER.replace("os.environ.get('MEDCHRON_PROOF_RUN') == '1'", "False")


class Client:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.records: list[tuple[str, str, dict]] = []
        self.proof: list[dict] = []

    def _request(self, req: dict) -> dict:
        a = req["action"]
        if a == "demand_job_status":
            return {"ok": True, "job": self.rows.get(req["job_id"])}
        if a == "demand_job_record":
            self.records.append((req["job_id"], req["state"], req["fields"]))
            self.rows[req["job_id"]]["state"] = req["state"]
            return {"ok": True}
        if a == "demand_allowance":
            return {"ok": True, "used": 0, "remaining": 5, "cents_used": 0}
        if a in ("delivery_proof_record", "delivery_proof_alert"):  # proven delivery (proof/watch.py)
            self.proof.append(req)
            return {"ok": True}
        raise AssertionError(a)


@pytest.fixture
def lane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[DemandLane, Client]:
    run_dir = tmp_path / "run"
    (run_dir / "demand-queue").mkdir(parents=True)
    client = Client()
    script = tmp_path / "runner.py"
    script.write_text(RUNNER)
    d = DemandLane(
        run_dir=run_dir,
        broker=DemandBroker(client),
        runner_cmd=[os.environ.get("PYTHON", "python3"), str(script)],
        customer_slug="example",
        sticky_db=str(tmp_path / "sticky.db"),
        cgroup_root=tmp_path / "cg",
        child_uid=None,
        clock=lambda: 1_000_000.0,
        queue_dir=run_dir / "demand-queue",
        inputs_dir=str(make_inputs(tmp_path / "inputs")),
    )
    return d, client


def _submit(d: DemandLane, client: Client, job_id: str) -> str:
    client.rows[job_id] = {"state": "submitted"}
    env = {k: v for k, v in job_doc(job_id).items() if k not in ("slug", "month_cents_used")}
    (d.queue / f"{job_id}.json").write_text(json.dumps(env))
    return job_id


def _authored(run_dir: Path, lanes: dict, revoked: list | None = None) -> None:
    (run_dir / lane_proof.AUTHORED_NAME).write_text(yaml.safe_dump({"lanes": lanes, "revoked": revoked or []}))


def _cli(run_dir: Path, action: str, target: str = "", **kw: str) -> int:
    ns = argparse.Namespace(
        action=action, target=target, run_dir=str(run_dir), wait=0.0, evidence="", why="", by="", **{}
    )
    for k, v in kw.items():
        setattr(ns, k, v)
    return lane_proof_cli.run(ns)


A, B = "01DEMAND0000000000000000AA", "01DEMAND0000000000000000BB"


# -- keys and the proven set ---------------------------------------------------------


def test_lane_keys_carry_the_drafting_class_and_an_unreadable_one_is_never_proven():
    assert lane_proof.lane_key("demand", {}) == "demand-letter-drafter"
    assert lane_proof.lane_key("drafting", {"document_class": "discovery_set"}) == "document-drafter.discovery_set"
    assert lane_proof.lane_key("drafting", {"document_class": "x; rm"}) == "document-drafter.unknown"
    assert lane_proof.lane_key("somethingnew", {}).startswith("unknown-lane.")


def test_runtime_and_authored_layers_and_revocation(tmp_path: Path):
    _authored(tmp_path, {"demand-letter-drafter": {"evidence": "7 delivered"}, "litigation-status": {}})
    p = lane_proof.load(tmp_path)
    assert p.is_proven("demand-letter-drafter") and p.is_proven("litigation-status")
    assert not p.is_proven("document-drafter.discovery_set")
    lane_proof.append_event(tmp_path, {"event": "revoked", "key": "litigation-status"})
    lane_proof.append_event(tmp_path, {"event": "proven", "key": "document-drafter.discovery_set"})
    p = lane_proof.load(tmp_path)
    assert not p.is_proven("litigation-status")  # a later runtime revoke beats the authored seed
    assert p.is_proven("document-drafter.discovery_set")
    _authored(tmp_path, {}, revoked=[{"key": "document-drafter.discovery_set", "why": "defect"}])
    assert not lane_proof.load(tmp_path).is_proven("document-drafter.discovery_set")  # authored revoke wins


def test_the_rule_matches_the_contract_cases_the_overlay_also_reads(tmp_path: Path):
    """operator/contracts/lane-proofs-cases.json is mirrored byte for byte in the
    overlay (tests/contract/lane_proofs_cases.json): the daemon that HOLDS and
    the request cards that PAGE must agree on what is proven."""
    cases = json.loads((Path(__file__).resolve().parents[3] / "contracts" / "lane-proofs-cases.json").read_text())
    for case in cases["cases"]:
        d = tmp_path / case["name"].replace(" ", "_").replace(":", "").replace(",", "")
        d.mkdir()
        if case["authored"] is not None:
            (d / lane_proof.AUTHORED_NAME).write_text(yaml.safe_dump(case["authored"]))
        (d / lane_proof.RUNTIME_NAME).write_text(json.dumps({"events": case["events"]}))
        p = lane_proof.load(d)
        assert {k: p.is_proven(k) for k in case["expect"]} == case["expect"], case["name"]


def test_every_seat_ships_a_valid_lane_proofs_file():
    customers = Path(__file__).resolve().parents[3] / "customers"
    seats = [d for d in customers.iterdir() if (d / "customer.yaml").is_file()]
    assert seats
    for d in seats:
        f = d / "lane-proofs.yaml"
        assert f.is_file(), f"{d.name} has no lane-proofs.yaml: every job there would wait with no seed"
        assert lane_proof.validate(f) == [], d.name


def test_the_validator_catches_a_typo_and_a_lane_without_evidence(tmp_path: Path):
    f = tmp_path / "lane-proofs.yaml"
    f.write_text(yaml.safe_dump({"schema": 1, "lanes": {"Demand Letter": {"evidence": "x"}}, "revoked": []}))
    assert any("not a lane key" in p for p in lane_proof.validate(f))
    f.write_text(yaml.safe_dump({"schema": 1, "lanes": {"demand-letter-drafter": {}}, "revoked": []}))
    assert any("evidence" in p for p in lane_proof.validate(f))
    f.write_text(yaml.safe_dump({"schema": 1, "lane": {}, "revoked": []}))
    assert lane_proof.validate(f)


def test_an_unreadable_seed_proves_nothing_and_says_so(tmp_path: Path):
    (tmp_path / lane_proof.AUTHORED_NAME).write_text("lanes: [not, a, mapping]\n")
    p = lane_proof.load(tmp_path)
    assert p.proven == {} and any("unreadable" in x for x in p.problems)
    (tmp_path / lane_proof.RUNTIME_NAME).write_text("{")
    assert lane_proof.load(tmp_path).problems


# -- the hold at claim ------------------------------------------------------------------


def test_an_unproven_job_waits_noted_once_and_does_not_block_a_proven_one(lane, tmp_path):
    d, client = lane
    _authored(d.run_dir, {})
    _submit(d, client, A)
    assert d.tick() is None
    assert client.records == [(A, "submitted", {"reason": "lane_unproven: demand-letter-drafter"})]
    assert (d.queue / f"{A}.json").is_file() and not (d.jobs / A).exists()
    d.tick()
    d.tick()
    assert len(client.records) == 1  # noted ONCE, not every poll
    assert lane_proof.holds(d.run_dir)[0]["key"] == "demand-letter-drafter"
    assert not (d.run_dir / "FILED").exists()


def test_a_held_job_does_not_starve_a_proven_lane_behind_it(lane):
    d, client = lane
    _authored(d.run_dir, {})
    _submit(d, client, A)
    d.tick()
    # The same queue, a second job; prove the lane only for the claim decision
    # by checking claim order: an unproven head must not stop the scan.
    _submit(d, client, B)
    lane_proof.append_event(d.run_dir, {"event": "proven", "key": "demand-letter-drafter"})
    assert d.claim_next() == A  # proven now: released and claimed in order
    assert client.records[-1] == (A, "submitted", {"reason": ""})  # the note is cleared BEFORE the run


def test_release_waits_until_the_note_is_cleared(lane, monkeypatch):
    d, client = lane
    _authored(d.run_dir, {})
    _submit(d, client, A)
    d.tick()
    lane_proof.append_event(d.run_dir, {"event": "proven", "key": "demand-letter-drafter"})

    def broken(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(d.broker, "record", broken)
    assert d.claim_next() is None  # not claimed with a stale lane_unproven reason on the row


# -- the proof run --------------------------------------------------------------------


def test_a_proof_run_writes_nothing_records_nothing_and_passes(lane, tmp_path):
    d, client = lane
    _authored(d.run_dir, {})
    _submit(d, client, A)
    d.tick()
    before = list(client.records)
    proof_run.request(d.queue, A)
    assert d.tick() is None  # the tick ran the proof and claimed nothing
    assert client.records == before  # the firm's job row never moved
    assert not (d.run_dir / "FILED").exists()
    res = proof_run.results(d.run_dir)[-1]
    assert res["status"] == "passed" and res["key"] == "demand-letter-drafter" and res["cents"] == 425
    twin = Path(res["path"]) / A
    assert json.loads((twin / "env.json").read_text())["proof"] == "1"
    assert any(a["path"].endswith("Demand.docx") for a in res["artifacts"])
    assert not (d.jobs / A).exists()  # nothing in the real job dirs, so no deliver wake can fire
    assert (d.queue / f"{A}.json").is_file()  # the firm's job still waits
    assert not list(d.queue.glob(".prove-*"))


def test_a_runner_that_ignores_proof_mode_fails_its_proof(lane, tmp_path):
    d, client = lane
    Path(d.runner_cmd[1]).write_text(RECKLESS)
    _authored(d.run_dir, {})
    _submit(d, client, A)
    d.tick()
    proof_run.request(d.queue, A)
    d.tick()
    assert proof_run.results(d.run_dir)[-1]["status"] == "failed"
    assert _cli(d.run_dir, "accept", A, evidence=VFY, by="smd") == 1
    assert not lane_proof.load(d.run_dir).is_proven("demand-letter-drafter")


def test_accept_needs_a_passed_proof_and_a_verify_id_then_the_job_runs_live(lane, tmp_path):
    d, client = lane
    _authored(d.run_dir, {})
    _submit(d, client, A)
    d.tick()
    assert _cli(d.run_dir, "accept", A, evidence=VFY, by="smd") == 2  # no proof run yet
    proof_run.request(d.queue, A)
    d.tick()
    assert _cli(d.run_dir, "accept", A, evidence="looked fine", by="smd") == 2
    assert _cli(d.run_dir, "accept", A, evidence=VFY, by="smd") == 0
    assert d.tick() == "delivered"
    states = [s for j, s, _f in client.records if j == A]
    assert states == ["submitted", "submitted", "running", "delivered"]  # note, clear, then the ordinary run
    assert (d.run_dir / "FILED").read_text() == A
    assert lane_proof.holds(d.run_dir) == []
    # The proof run paged nothing; the live delivery recorded its proof once.
    assert [r["action"] for r in client.proof] == ["delivery_proof_record"]


def test_a_proof_of_a_job_with_no_envelope_anywhere_is_refused(lane):
    d, _client = lane
    proof_run.request(d.queue, A)
    d.tick()
    assert proof_run.results(d.run_dir)[-1]["status"] == "refused"


def test_a_finished_real_request_can_be_proved_again_without_touching_it(lane):
    """The proactive proof: a delivered job's own envelope, run again in the
    twin tree. Its job dir, its ledger row and the firm's matter are untouched."""
    d, client = lane
    _authored(d.run_dir, {"demand-letter-drafter": {"evidence": "x"}})
    _submit(d, client, B)
    assert d.tick() == "delivered"  # an ordinary live job, proven lane
    before = (list(client.records), (d.jobs / B / "daemon.json").read_text(), (d.run_dir / "FILED").read_text())
    lane_proof.append_event(d.run_dir, {"event": "revoked", "key": "demand-letter-drafter"})
    assert _cli(d.run_dir, "dry-run", B) == 0  # found in its job dir, not the queue
    d.tick()
    res = proof_run.results(d.run_dir)[-1]
    assert res["status"] == "passed" and res["job_id"] == B
    after = (list(client.records), (d.jobs / B / "daemon.json").read_text(), (d.run_dir / "FILED").read_text())
    assert after == before
    assert _cli(d.run_dir, "accept", B, evidence=VFY, by="smd") == 0
    assert lane_proof.load(d.run_dir).is_proven("demand-letter-drafter")


def test_judge_needs_every_verdict_to_be_dry_run():
    assert proof_run.judge([[{"outcome": "dry_run"}, {"outcome": "dry_run"}]])[0]
    assert proof_run.judge([{"verdict": "dry_run"}])[0]
    assert not proof_run.judge([[{"outcome": "dry_run"}, {"outcome": "held"}]])[0]
    assert not proof_run.judge([])[0]
    assert not proof_run.judge([{"verdict": "delivered"}])[0]


def test_the_twin_never_writes_through_the_real_broker(lane):
    d, client = lane
    t = proof_run.twin(d, d.run_dir / "x")
    t.broker.record(A, "delivered", {"cents": 1})
    assert client.records == [] and t.broker.records[0]["state"] == "delivered"
    assert t.jobs == d.run_dir / "x" and d.jobs != t.jobs
    assert t.child_env[proof_mode.PROOF_ENV] == "1" and proof_mode.PROOF_ENV not in d.child_env


# -- below the runner: the connector refuses a write in a proof run ---------------------


def test_the_connector_refuses_every_write_in_a_proof_run(monkeypatch):
    from smokeball_connector.write_record import ProofRunWriteRefused, refuse_proof_run_write

    refuse_proof_run_write("POST", "/matters/x/documents")  # not a proof run: allowed here
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    refuse_proof_run_write("GET", "/matters/x")  # reads pass
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        with pytest.raises(ProofRunWriteRefused):
            refuse_proof_run_write(method, "/matters/x")


def test_the_client_refuses_before_any_http_is_sent(monkeypatch):
    from smokeball_connector import client as client_mod
    from smokeball_connector.write_record import ProofRunWriteRefused

    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    c = client_mod.SmokeballClient.__new__(client_mod.SmokeballClient)
    c._account_id = ""
    c.api_host = "https://example.invalid"

    class NoHttp:
        def request(self, *a, **k):
            raise AssertionError("an HTTP request was sent during a proof run")

        put = request

    c._http = NoHttp()
    with pytest.raises(ProofRunWriteRefused):
        c.request("PATCH", "/matters/0b1c2d3e-0000-4000-8000-000000000000/layouts/x", json={"values": []})
    with pytest.raises(ProofRunWriteRefused):
        c._put_presigned("https://s3.example.invalid/x", b"bytes")


# -- each runner stops at its own write stage in a proof run ----------------------------


def _staged(data: Path) -> None:
    from medchron.demand import deliver

    out = deliver.out_dir(data)
    (out / "upload_manifest.json").write_text(json.dumps([{"name": "Demand.docx", "bytes": 5, "role": "demand"}]))


@pytest.mark.parametrize("module", ["demand", "drafting"])
def test_demand_and_drafting_stop_before_filing(module, tmp_path, monkeypatch):
    import importlib

    run_mod = importlib.import_module(f"medchron.{module}.run")
    cls = run_mod.DemandRun if module == "demand" else run_mod.DraftingRun
    r = cls.__new__(cls)
    monkeypatch.setattr(cls, "seat", None, raising=False)
    r.data, r.readback_pause, r.log = tmp_path, 0, print
    r.job = argparse.Namespace(file_to_id="m1")
    _staged(tmp_path)
    if hasattr(run_mod, "review_stage"):  # the demand's pre-delivery review is its own suite's concern
        monkeypatch.setattr(run_mod.review_stage, "run_stage", lambda _run: None)

    def no_filing(*a, **k):
        raise AssertionError("filed during a proof run")

    monkeypatch.setattr(run_mod.deliver, "file_to_matter", no_filing)
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    v = r._file()
    assert v.outcome == "dry_run" and v.stage == "file" and v.files[0]["name"] == "Demand.docx"
    monkeypatch.delenv(proof_mode.PROOF_ENV)
    with pytest.raises(AssertionError, match="filed during a proof run"):
        r._file()  # the falsifier: without the flag the same object files


@pytest.mark.parametrize("proof", [True, False])
def test_the_chronology_stops_at_upload_with_its_package_staged(
    proof, tmp_path, data_root, firm_config_path, pricing_path, monkeypatch
):
    from medchron_testkit import job_yaml
    from test_decisions_driver import _NoNetwork, _driver

    monkeypatch.delenv("MEDCHRON_PIPELINE_DIR", raising=False)
    if proof:
        monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    job_dir, install_root = tmp_path / "job", tmp_path / "install"
    job_dir.mkdir()
    (job_dir / "job.yaml").write_text(job_yaml(data_root, install_root=install_root), encoding="utf-8")
    o = _driver(job_dir, firm_config_path, pricing_path, client=_NoNetwork()).run()[0]
    if proof:
        assert (o.outcome, o.stage) == ("dry_run", "upload"), o
        assert (data_root / "example-matter" / "runs" / "alpha" / "final-chronology.md").is_file()
        assert o.folder_id is None
    else:  # the falsifier: the same run without the flag goes through upload
        assert o.outcome == "delivered", o


def test_the_litigation_status_stops_before_filing_and_commits_no_state(tmp_path, pricing_path, monkeypatch):
    from litigation_testkit import M1, ScriptedLit
    from medchron.litigation import manifest
    from test_litigation_run import _run

    (tmp_path / "pricing.json").write_text(pricing_path.read_text())
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    r, seat = _run(tmp_path, ScriptedLit())
    v = r.run()
    assert (v.verdict, v.stage) == ("dry_run", "file"), v.reason
    assert v.files[0]["role"] == "workbook" and v.cents > 0
    assert not seat.created  # no folder, no file on the firm's library matter
    assert manifest.load_prior(tmp_path / "state", M1) is None  # the cursor did not advance


@pytest.mark.parametrize("dry_verdict,expect", [("delivered", "dry_run"), ("failed", "failed")])
def test_the_negotiation_watch_proves_through_its_dry_run(dry_verdict, expect, tmp_path, monkeypatch):
    from medchron import __main__ as cli
    from medchron.negotiation import dryrun, run as neg_run

    seen = {}

    class FakeDry:
        def __init__(self, job_dir, *, state_dir, **kw):
            seen["state"] = state_dir

        def run(self):
            return {"verdict": dry_verdict, "candidates": 3, "cents": 12}

    def live(*a, **k):
        raise AssertionError("the live negotiation run ran in a proof run")

    monkeypatch.setattr(dryrun, "NegotiationDryRun", FakeDry)
    monkeypatch.setattr(neg_run.NegotiationRun, "__init__", live)
    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    (tmp_path / "job").mkdir()
    cli._cmd_negotiate(argparse.Namespace(job_dir=str(tmp_path / "job"), state_dir=None, pricing=None))
    v = json.loads((tmp_path / "job" / "verdict.json").read_text())
    assert v["verdict"] == expect and v["cents"] == 12
    assert seen["state"] == tmp_path / "job" / "state"  # a throwaway cursor inside the twin


def test_a_caption_correction_is_not_written_in_a_proof_run(monkeypatch, tmp_path):
    from medchron.drafting import caption_fix

    class NoWrites:
        def request(self, *a, **k):
            raise AssertionError("wrote the matter record during a proof run")

    monkeypatch.setenv(proof_mode.PROOF_ENV, "1")
    diff = {
        "field": "case_number",
        "record_value": "A",
        "document_value": "B",
        "source": "doc",
        "why": "differs",
        "fix": {"kind": "case_number"},
    }
    corrections, left, alarms = caption_fix.apply(NoWrites(), "m1", [diff], tmp_path)
    assert corrections and not left and not alarms
    assert '"proof": true' in (tmp_path / "caption-writes.jsonl").read_text()

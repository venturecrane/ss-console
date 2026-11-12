"""The job-lane outputs are in sync with operator/contracts/job-lanes.yaml.

operator/bin/gen-job-lanes.py writes one Python module per install tree, the
console's TypeScript module and a shell file from the one declaration. This is
the CI guard that a lane added or changed in the yaml reaches every output, and
that nobody edits an output by hand.

Run::

    cd operator && python3 -m pytest bin/tests/test_job_lanes_generation.py -v
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from bin.lib import job_lanes_gen as gen

ROOT = gen.repo_root()


def _cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_job_lanes_under_test", ROOT / gen.GENERATOR)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_outputs_are_in_sync() -> None:
    assert _cli().main(["gen-job-lanes.py", "--check"]) == 0


def test_every_python_output_is_the_same_module() -> None:
    texts = {(ROOT / rel).read_text(encoding="utf-8") for rel in gen.PYTHON_RELS}
    assert len(texts) == 1


def test_a_hand_edit_to_an_output_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for rel in (gen.CONTRACT_REL, gen.SHELL_REL, gen.TS_REL, *gen.PYTHON_RELS):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    monkeypatch.setattr(gen, "repo_root", lambda: tmp_path)
    cli = _cli()
    assert cli.main(["gen-job-lanes.py", "--check"]) == 0
    broker = tmp_path / "operator/workspace_broker/job_lanes.py"
    broker.write_text(broker.read_text(encoding="utf-8").replace('"records",\n)', '"records",\n    "extra",\n)'))
    assert cli.main(["gen-job-lanes.py", "--check"]) == 1
    assert cli.main(["gen-job-lanes.py"]) == 0
    assert cli.main(["gen-job-lanes.py", "--check"]) == 0


def test_the_generated_python_reads_back_the_declaration() -> None:
    decl = gen.load(ROOT / gen.CONTRACT_REL)
    spec = importlib.util.spec_from_file_location("job_lanes_under_test", ROOT / gen.PYTHON_RELS[0])
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.JOB_LANES == tuple(lane.name for lane in decl.lanes)
    for name, _comment, value in gen.constants(decl):
        assert getattr(mod, name) == value, name
    # Not vacuous: the declaration holds today's eight lanes and their columns.
    assert set(mod.JOB_LANES) == {
        "medchron",
        "demand",
        "drafting",
        "litigation",
        "casefile",
        "negotiation",
        "note",
        "records",
    }
    assert set(mod.QUEUED_LANES) == set(mod.JOB_LANES) - {"note"}


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_shell_output_sources_and_answers() -> None:
    script = f'. "{ROOT / gen.SHELL_REL}"; smd_lane_skill casefile; echo "$SMD_FIRM_INPUT_DIR_LANES"; smd_lane_skill nope || echo none'
    out = subprocess.run(  # noqa: S603 - a fixed bash argv sourcing the committed generated file
        ["bash", "-c", script], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    assert out == ["case-file-manager", "demand drafting litigation casefile records", "none"]


def _lanes_yaml(body: str) -> str:
    return f"lanes:\n  a:\n    skill: s-a\n  b:\n{body}firm_input_extras: []\n"


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("    skill: s-b\n    bogus: 1\n", "unknown fields"),
        ("    queued: true\n    skill: s-b\n", "queue_dir, jobs_dir and child_pid"),
        ("    skill: s-b\n    queue_dir: q\n", "queue_dir, jobs_dir and child_pid"),
        ("    skill: s-b\n    queued: false\n", "is a flag"),
        ("    skill: s-b\n    firm_inputs: dir\n", "come together"),
        ("    skill: s-b\n    firm_inputs: tree\n    firm_module: jobs.b.firm\n", "file or dir"),
        ("    skill: s-b\n    broker_firm_copy: true\n", "needs firm_inputs"),
        ("    skill: s-b\n    scheduled: true\n", "needs a wake_word"),
        ("    queued: true\n", "needs a skill"),
    ],
)
def test_a_bad_declaration_is_refused(tmp_path: Path, body: str, why: str) -> None:
    bad = tmp_path / "job-lanes.yaml"
    bad.write_text(_lanes_yaml(body), encoding="utf-8")
    with pytest.raises(ValueError, match=why):
        gen.load(bad)

"""Ask a release's own validator whether a seat's staged firm inputs still fit.

The lane-input preflight (preflight.py, check 3) exists for one risk: a seat
boots an image whose lane validator refuses the firm inputs already staged in
its vault, and the lane comes up without its configuration. Until 2026-11-05
the gate asked a cheaper question, whether the schema MODULE's bytes changed,
and twice in two days the answer was yes for an edit that changed nothing a firm
may write (ADR 0103's package rename; #3774's one import line). Each time every
seat was refused and a person had to declare the change harmless.

This asks the real question. For a lane whose module digest differs, it reads
the seat's staged marker yaml from the vault (``vaults/<slug>/<lane>/<lane>-firm.yaml``,
or ``vaults/<slug>/medchron-firm.yaml``), takes the runner package out of git at
the RELEASE's commit (``git archive``, never the checkout's working tree, which
may be newer), and runs that tree's ``validate(data)`` on it in a child of this
interpreter. No problems: the inputs fit and the seat may take the release. Any
problem: refused, by count only (a validator's words can quote a firm's own
values, and a rollout's detail leaves the seat's vault for D1 and the ledger).

Three answers, never a guess: True, False, or None when the question could not
be asked (no marker in the vault, a commit this clone has never seen, a lane with
no validator here, the child failing for its own reason). None falls back to the
refusal the gate always gave, so a broken instrument cannot pass a seat.

What it does not judge: the files a marker PINS (demand and drafting name
exemplars by path and sha256). Those are client documents that live only in the
vault (Law 15) and are checked against their pins when the lane boots. A schema
change that alters what a pin means is a person's call, as before.
"""

from __future__ import annotations

import re
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from . import ids
from .runner import ToolingError

#: The runner package in the repo, and the directory that must be on sys.path to import it.
RUNNER_PACKAGE = "operator/runners/jobs/jobs"
RUNNER_ROOT = "operator/runners/jobs"

#: lane -> (the marker's key under vaults/<slug>/, the module whose validate() judges it).
LANE_VALIDATORS: dict[str, tuple[str, str]] = {
    "medchron": ("medchron-firm.yaml", "jobs.medchron.config"),
    "demand": ("demand/demand-firm.yaml", "jobs.demand.firm"),
    "drafting": ("drafting/drafting-firm.yaml", "jobs.drafting.firm"),
    "litigation": ("litigation/litigation-firm.yaml", "jobs.litigation.firm"),
    "casefile": ("casefile/casefile-firm.yaml", "jobs.casefile.firm"),
    "records": ("records/records-firm.yaml", "jobs.records.firm"),
}

# Prints one line, the number of problems. Nothing the validator SAYS is printed.
_CHILD = (
    "import importlib, sys, yaml\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "module = importlib.import_module(sys.argv[2])\n"
    "with open(sys.argv[3], encoding='utf-8') as fh:\n"
    "    data = yaml.safe_load(fh)\n"
    "print('problems=' + str(len(module.validate(data))))\n"
)
_VERDICT_RE = re.compile(r"^problems=(\d+)$")


class LaneValidator:
    """``validates(seat, lane, target_sha)``: True, False, or None when it could not be asked."""

    def __init__(self, runner: Any, repo: Path, r2: Any, python: str = sys.executable) -> None:
        self.runner, self.repo, self.r2, self.python = runner, repo, r2, python

    def validates(self, seat: str, lane: str, target_sha: str | None) -> bool | None:
        spec = LANE_VALIDATORS.get(lane)
        if spec is None or not target_sha or not ids.SHA_RE.match(target_sha):
            return None
        marker_key, module = spec
        try:
            marker = self.r2.get_bytes(f"vaults/{ids.slug(seat)}/{marker_key}")
        except ToolingError:
            return None
        if marker is None:
            return None
        try:
            return self._ask(marker, module, target_sha)
        except ToolingError:
            # git or the interpreter could not be started: not asked, never a pass.
            return None

    def _ask(self, marker: bytes, module: str, target_sha: str) -> bool | None:
        with tempfile.TemporaryDirectory(prefix="lane-validate-") as tmp:
            root = Path(tmp)
            archive = root / "runner.tar"
            got = self.runner.run(
                [
                    "git",
                    "-C",
                    str(self.repo),
                    "archive",
                    "--format=tar",
                    "-o",
                    str(archive),
                    target_sha,
                    RUNNER_PACKAGE,
                ],
                timeout=120,
            )
            if got.rc != 0 or not archive.is_file():
                return None
            src = root / "src"
            try:
                with tarfile.open(archive) as tar:
                    tar.extractall(src, filter="data")
            except (tarfile.TarError, OSError, TypeError):
                # TypeError: a Python older than 3.11.4 has no ``filter=``; not asked, never a pass.
                return None
            staged = root / "marker.yaml"
            staged.write_bytes(marker)
            ran = self.runner.run(
                [self.python, "-I", "-c", _CHILD, str(src / RUNNER_ROOT), module, str(staged)], timeout=120
            )
            if ran.rc != 0:
                return None
            lines = ran.stdout.strip().splitlines()
            verdict = _VERDICT_RE.match(lines[-1]) if lines else None
            return None if verdict is None else verdict.group(1) == "0"

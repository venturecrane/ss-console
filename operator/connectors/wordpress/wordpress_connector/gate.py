"""The California attorney-advertising gate, run before every post write.

The checker is ``advertising_gate_check.py``, shipped root-owned in the image at
``/app/operator/templates/drafting/`` with the sibling modules it loads by path
(ADR 0098); the overlay's outbound gate reads the same file. It is run as a SUBPROCESS
(the ``record_check`` pattern the Smokeball connector uses), never imported, so
this connector's venv never has to carry the checker's dependencies and the
checker the seat runs is exactly the file the image shipped.

Interface (owned by the checker): ``--item <file> --channel blog
--registers <yaml> --json``. Exit 0 pass, 1 block, 2 usage or IO. JSON
``{"verdict": "pass"|"block", "findings": [{"rule", "severity", "span",
"message"}]}`` with severity ``BLOCK`` or ``FLAG``. FLAG findings alone still
pass (exit 0).

FAIL CLOSED. Exit 1, exit 2, any other exit, a timeout, an absent checker, or
output that does not parse: the write is refused. A gate that silently did not
run reads exactly like a gate that passed.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_GATE = "/app/operator/templates/drafting/advertising_gate_check.py"
DEFAULT_REGISTERS = "/var/lib/smd-config/marketing/marketing-firm.yaml"
GATE_TIMEOUT_SECONDS = 60
# Every write this connector makes is a WordPress post, which is a blog post.
# The channel is fixed here, never taken from the model: the checker's lighter
# channels (note, social) drop the blog BLOCK rules, so a caller-chosen channel
# would let a blog post past rules written for blog posts.
CHANNEL = "blog"


@dataclass
class GateResult:
    passed: bool
    reason: str = ""
    findings: list[dict[str, Any]] = field(default_factory=list)

    @property
    def flags(self) -> list[dict[str, Any]]:
        return [f for f in self.findings if str(f.get("severity", "")).upper() == "FLAG"]


def _gate_path() -> str:
    for name in ("SMD_ADVERTISING_GATE", "SMD_ADVERTISING_GATE_PATH"):
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return DEFAULT_GATE


def _registers_path() -> str:
    return os.environ.get("SMD_MARKETING_REGISTERS", "").strip() or DEFAULT_REGISTERS


def _python() -> str:
    # This connector's own venv interpreter (the record_check pattern), which
    # carries PyYAML for the registers file; without it the checker returns a
    # REGISTERS block on every post. Overridable for tests.
    return os.environ.get("SMD_ADVERTISING_GATE_PYTHON", "").strip() or sys.executable


def _clean_findings(raw: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    for f in raw:
        if isinstance(f, dict):
            out.append(
                {
                    "rule": str(f.get("rule", "")),
                    "severity": str(f.get("severity", "")),
                    "span": str(f.get("span", ""))[:400],
                    "message": str(f.get("message", ""))[:600],
                }
            )
    return out


def run_gate(text: str) -> GateResult:
    """Run the checker over ``text`` as a blog post. Never raises: every failure is a refusal."""
    gate = _gate_path()
    if not Path(gate).is_file():
        return GateResult(False, f"advertising gate checker is absent at {gate}; refusing the write (fail closed)")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False) as fh:
        fh.write(text)
        item = fh.name
    try:
        try:
            proc = subprocess.run(  # noqa: S603 - fixed argv; the checker path is the image's own file or a test override, never model input
                [
                    _python(),
                    gate,
                    "--item",
                    item,
                    "--channel",
                    CHANNEL,
                    "--registers",
                    _registers_path(),
                    "--json",
                ],
                capture_output=True,
                text=True,
                timeout=GATE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return GateResult(False, f"advertising gate could not run ({type(exc).__name__}); refusing the write")
    finally:
        try:
            os.unlink(item)
        except OSError:
            pass

    parsed: Any = None
    try:
        parsed = json.loads(proc.stdout) if proc.stdout.strip() else None
    except ValueError:
        parsed = None
    findings = _clean_findings(parsed.get("findings") if isinstance(parsed, dict) else None)

    if proc.returncode == 0:
        if not isinstance(parsed, dict) or parsed.get("verdict") != "pass":
            return GateResult(False, "advertising gate returned no readable pass verdict; refusing the write", findings)
        return GateResult(True, "", findings)
    if proc.returncode == 1:
        return GateResult(False, "advertising gate BLOCKED this draft; fix every BLOCK finding and retry", findings)
    tail = (proc.stderr or "").strip()[-400:]
    return GateResult(
        False,
        f"advertising gate failed to run (exit {proc.returncode}): {tail}; refusing the write (fail closed)",
        findings,
    )

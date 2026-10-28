"""The harness app's `fly logs`, captured for a run and cut to one Machine."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")


class LogStream:
    """`fly logs` streamed to a file for the whole run.

    `fly logs --no-tail` returns only the last ~100 buffered lines; on the first
    calibration run that cut off everything before the gateway's exit, including
    its cause. The harness app holds only harness Machines, so the app stream is
    this run's log. Started BEFORE `fly machine run`, so the first boot line is kept.
    """

    def __init__(self, app: str, path: Path) -> None:
        self.path = path
        self._fh = path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(  # noqa: S603 - fixed argv, never a shell
            ["fly", "logs", "-a", app], stdout=self._fh, stderr=subprocess.STDOUT, text=True
        )

    def close(self) -> str:
        time.sleep(5)  # let the last lines arrive
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._fh.close()
        return ANSI.sub("", self.path.read_text(encoding="utf-8", errors="replace"))


def machine_lines(log: str, machine: str) -> str:
    """Only this Machine's lines: the app stream replays buffered lines of earlier
    harness Machines, and the first calibration run judged those as this boot's."""
    tag = f"[{machine}]"
    return "\n".join(ln for ln in log.splitlines() if tag in ln)

"""The one subprocess seam.

Every external call `fleet` makes (flyctl, wrangler, aws, crane, the boot smoke
script) goes through :class:`CommandRunner`, so the tests replace exactly one
object and the classification of "the tool broke" lives in one place.

THE THREE OUTCOMES A ROLLOUT DISTINGUISHES. A seat check can PASS, FAIL (the
seat is genuinely unhealthy, so the release is rolled back), or end in a
TOOLING ERROR (ssh hung, the Fly agent errored, wrangler could not reach D1).
Only a FAIL may roll a seat back: rolling back on a tooling error would act on
a guess, and on 2026-10-18 a single hung ssh would have reverted a healthy seat.
A command that overruns its timeout, or cannot be started at all, raises
:class:`ToolingError` here; callers decide what a non-zero exit means.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Mapping, Protocol, Sequence


class ToolingError(RuntimeError):
    """A tool failed to give an answer (timeout, not installed, transport error)."""


class Refusal(RuntimeError):
    """A check said no. Nothing was touched; the reason is the message."""


@dataclass(frozen=True)
class Result:
    rc: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def text(self) -> str:
        return f"{self.stdout}\n{self.stderr}"


class Runner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: float = 120,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Result: ...


class CommandRunner:
    """Run a command with a wall-clock budget; never via a shell."""

    def run(
        self,
        argv: Sequence[str],
        *,
        input_text: str | None = None,
        timeout: float = 120,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        full_env = {**os.environ, **(env or {})}
        try:
            proc = subprocess.run(  # noqa: S603 - argv list, never a shell
                list(argv),
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env=full_env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolingError(f"{argv[0]} gave no answer in {timeout:.0f}s ({' '.join(argv[:3])} ...)") from exc
        except OSError as exc:
            raise ToolingError(f"could not run {argv[0]}: {exc}") from exc
        return Result(proc.returncode, proc.stdout or "", proc.stderr or "")


# Output that means the TRANSPORT failed, not the thing being asked about. Fly's
# agent and ssh layer, and wrangler's API layer, print these; a check whose
# command failed with one of them returned no verdict.
TRANSPORT_MARKERS: tuple[str, ...] = (
    "context deadline exceeded",
    "connection refused",
    "connection reset",
    "i/o timeout",
    "error connecting to",
    "failed to connect",
    "tunnel unavailable",
    "agent error",
    "agent not running",
    "failed to start agent",
    "ssh: handshake failed",
    "unexpected eof",
    # Go's net/http names a connection the server dropped mid-response as a bare
    # EOF after the URL: `Get "https://api.machines.dev/...": EOF`.
    '": eof',
    "temporary failure in name resolution",
    "no such host",
    "502 bad gateway",
    "503 service unavailable",
    "authentication error",
    "fetch failed",
    "econnreset",
)


def looks_like_transport_failure(result: Result) -> bool:
    low = result.text.lower()
    return any(marker in low for marker in TRANSPORT_MARKERS)


def require_ok(result: Result, what: str) -> Result:
    """Raise :class:`ToolingError` (naming ``what``) unless the command exited 0."""
    if not result.ok:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise ToolingError(f"{what} failed (rc={result.rc}): {' / '.join(tail)}")
    return result

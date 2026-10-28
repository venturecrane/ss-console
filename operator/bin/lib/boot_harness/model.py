"""The verdict and expectation types the boot harness judges share."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BOOT_BUDGET_S = 900.0


@dataclass(frozen=True)
class Verdict:
    check: str
    ok: bool
    detail: str

    def line(self) -> str:
        return f"{'PASS' if self.ok else 'FAIL'}  {self.check}: {self.detail}"


@dataclass(frozen=True)
class Expect:
    hermes_sha: str | None
    subplugins: tuple[str, ...]
    boot_budget_s: float = DEFAULT_BOOT_BUDGET_S
    brotlicffi: str | None = None
    require_virtual_env: bool = False
    require_managed_jobs: bool = False

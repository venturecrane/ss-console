#!/usr/bin/env python3
"""Audit the seat image's Python against known vulnerabilities.

Two sets are audited, because the image installs two kinds of Python:

1. Every hash-locked file under ``operator/requirements/`` (the exact bytes the
   Dockerfile installs into the broker, connector, runner and overlay venvs).
2. The Hermes gateway venv's EFFECTIVE set (``/opt/hermes/.venv``): Hermes'
   own ``uv.lock`` at the Dockerfile's ``HERMES_UPSTREAM_SHA``, exported with
   the extras the Dockerfile's ``uv sync --frozen`` installs, with the security
   overrides in ``operator/requirements/hermes-security.txt`` applied. Until
   2026-10-27 (code review DP-1) only set 1 was read, so a critical advisory in
   a Hermes-locked package (certifi's compromised emSign intermediates,
   PyJWT's key-confusion bypasses) passed this gate on every run.

Any advisory not in the time-boxed allowlist at
``.github/pip-audit-allowlist.json`` fails. The allowlist has the same shape
and the same rules as the npm one (``.github/audit-allowlist.json``): an entry
names a tracking issue and expires on a date, after which the gate re-fails so
the fix has to land. An empty ``allow`` map means no exceptions are in force.

pip-audit reports OSV / PyPA advisories without a severity field this gate can
trust, so the bar is "any known vulnerability", not "high or critical".

Exit codes: 0 clean (or every finding allowlisted and unexpired); 1 findings;
2 pip-audit, uv, or the Hermes lock fetch could not run (never silently green:
a scanner that cannot run is a HOLD, the send-reconciler lesson).

Run locally (needs network, pip-audit and uv on PATH)::

    uvx --from pip-audit==2.10.1 --with uv python3 operator/bin/pip_audit_gate.py
    (or: pip install pip-audit==2.10.1 uv && python3 operator/bin/pip_audit_gate.py)
"""

from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO / "operator" / "requirements"
ALLOWLIST = REPO / ".github" / "pip-audit-allowlist.json"
DOCKERFILE = REPO / "operator" / "templates" / "Dockerfile"
HERMES_OVERRIDES = REQUIREMENTS / "hermes-security.txt"
HERMES_RAW = "https://raw.githubusercontent.com/NousResearch/hermes-agent/{sha}/{path}"
HERMES_LABEL = "hermes-venv"

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_allowlist() -> dict[str, str]:
    if not ALLOWLIST.is_file():
        return {}
    data = json.loads(ALLOWLIST.read_text(encoding="utf-8"))
    allow = data.get("allow") or {}
    expires = data.get("expires")
    if allow and expires:
        if dt.date.fromisoformat(expires) <= dt.date.today():
            print(f"pip-audit allowlist expired on {expires}; ignoring {len(allow)} entries")
            return {}
    return {str(k): str(v) for k, v in allow.items()}


def hermes_pin(dockerfile: Path = DOCKERFILE) -> tuple[str, list[str]]:
    """(HERMES_UPSTREAM_SHA, the extras of the image's ``uv sync --frozen``), read from the Dockerfile."""
    text = dockerfile.read_text(encoding="utf-8")
    sha = re.search(r"^ARG HERMES_UPSTREAM_SHA=([0-9a-f]{40})$", text, re.M)
    sync = re.search(r"^RUN uv sync --frozen\b.*$", text, re.M)
    if sha is None or sync is None:
        raise ValueError(f"{dockerfile}: no ARG HERMES_UPSTREAM_SHA or no `uv sync --frozen` line")
    extras = re.findall(r"--extra\s+([A-Za-z0-9_-]+)", sync.group(0))
    return sha.group(1), extras


def pins(text: str) -> dict[str, str]:
    """``name -> version`` for every ``name==version`` line (hash continuations ignored)."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _PIN.match(line)
        if m:
            out[_norm(m.group(1))] = m.group(2)
    return out


def apply_overrides(export: str, overrides: dict[str, str]) -> str:
    """The venv's effective set: Hermes' exported pins with each override's version substituted.

    An override for a package Hermes' set does not install is an error: it
    means the override file has gone stale against HERMES_UPSTREAM_SHA, and
    the image would be installing a package Hermes never asked for.
    """
    hermes = pins(export)
    stale = sorted(set(overrides) - set(hermes))
    if stale:
        raise ValueError(f"hermes-security.txt overrides {stale}, which Hermes' lock does not install")
    out: list[str] = []
    for line in export.splitlines():
        m = _PIN.match(line)
        if m and _norm(m.group(1)) in overrides:
            # Keep the line's environment marker; swap only the version.
            line = f"{m.group(1)}=={overrides[_norm(m.group(1))]}{line[m.end() :]}"
        out.append(line)
    return "\n".join(out) + "\n"


def fetch_hermes_export(sha: str, extras: list[str], work: Path) -> str:
    """``uv export`` of Hermes' lock at ``sha`` with ``extras``, exactly as compile.sh exports it."""
    uv, curl = shutil.which("uv"), shutil.which("curl")
    if uv is None or curl is None:
        print("uv and curl are needed to fetch and export Hermes' uv.lock", file=sys.stderr)
        raise SystemExit(2)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError(f"not a 40-hex sha: {sha!r}")
    for path in ("pyproject.toml", "uv.lock"):
        # The same fetch compile.sh makes, with the same tool.
        url = HERMES_RAW.format(sha=sha, path=path)
        got = subprocess.run(  # noqa: S603 - list argv from shutil.which, no shell; the URL is a fixed host plus a 40-hex sha
            [curl, "-fsSL", "--max-time", "120", "-o", str(work / path), url],
            capture_output=True,
            text=True,
            check=False,
        )
        if got.returncode != 0:
            print(f"could not fetch {url}: {got.stderr.strip()}", file=sys.stderr)
            raise SystemExit(2)
    argv = [uv, "export", "--frozen", "--no-hashes", "--no-emit-project", "--no-header", "--no-annotate"]
    for extra in extras:
        argv += ["--extra", extra]
    proc = subprocess.run(argv, cwd=work, capture_output=True, text=True, check=False)  # noqa: S603 - list argv from shutil.which, no shell, flags fixed
    if proc.returncode != 0 or not pins(proc.stdout):
        print(f"uv export of Hermes' lock at {sha} failed:", file=sys.stderr)
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        raise SystemExit(2)
    return proc.stdout


def audit(requirements: Path, *, label: str | None = None, hashed: bool = True) -> list[dict]:
    exe = shutil.which("pip-audit")
    if exe is None:
        print("pip-audit is not installed", file=sys.stderr)
        raise SystemExit(2)
    argv = [
        exe,
        "--strict",
        # The files are fully pinned, so pip-audit needs no resolver and no
        # temporary venv: --disable-pip reads the pins as written, which is
        # also the only mode that cannot drift from what the image installs.
        "--disable-pip",
        "--no-deps",
        "--format",
        "json",
        "--progress-spinner",
        "off",
        "-r",
        str(requirements),
    ]
    if hashed:
        argv.insert(4, "--require-hashes")
    proc = subprocess.run(  # noqa: S603 - list argv from shutil.which, no shell; the only variable is the requirements path
        argv,
        capture_output=True,
        text=True,
        check=False,
    )
    # pip-audit exits 1 when it found vulnerabilities; that is a result, not a
    # failure to run. Anything else with no JSON is the scanner breaking.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"pip-audit could not scan {label or requirements.name}:", file=sys.stderr)
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        raise SystemExit(2) from None
    findings: list[dict] = []
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []) or []:
            findings.append(
                {
                    "file": label or requirements.name,
                    "package": dep.get("name"),
                    "version": dep.get("version"),
                    "id": vuln.get("id"),
                    "aliases": vuln.get("aliases") or [],
                    "fix_versions": vuln.get("fix_versions") or [],
                }
            )
    return findings


def audit_hermes_venv(work: Path) -> list[dict]:
    """Audit Hermes' locked set at the image's pin, with hermes-security.txt applied."""
    sha, extras = hermes_pin()
    export = fetch_hermes_export(sha, extras, work)
    overrides = pins(HERMES_OVERRIDES.read_text(encoding="utf-8")) if HERMES_OVERRIDES.is_file() else {}
    effective = work / "hermes-effective.txt"
    effective.write_text(apply_overrides(export, overrides), encoding="utf-8")
    print(
        f"pip-audit: Hermes venv = uv.lock @ {sha[:12]} (extras {', '.join(extras)}), "
        f"{len(pins(export))} packages, {len(overrides)} override(s) from hermes-security.txt"
    )
    return audit(effective, label=f"{HERMES_LABEL}@{sha[:12]}", hashed=False)


def main() -> int:
    files = sorted(REQUIREMENTS.glob("*.txt"))
    if not files:
        print(f"no requirement files under {REQUIREMENTS}", file=sys.stderr)
        return 2
    allow = load_allowlist()
    findings: list[dict] = []
    for req in files:
        findings += audit(req)
    with tempfile.TemporaryDirectory() as tmp:
        findings += audit_hermes_venv(Path(tmp))
    blocking: list[dict] = []
    tolerated: list[dict] = []
    for finding in findings:
        ids = {finding["id"], *finding["aliases"]}
        if ids & set(allow):
            tolerated.append(finding)
        else:
            blocking.append(finding)
    print(f"pip-audit: scanned {len(files)} requirement file(s) and the Hermes venv")
    for f in tolerated:
        print(f"  tolerated {f['id']} in {f['package']}=={f['version']} ({f['file']}) until the allowlist expires")
    if blocking:
        print(f"  {len(blocking)} blocking advisory(ies):")
        for f in blocking:
            fix = ", ".join(f["fix_versions"]) or "no fix published"
            print(f"    {f['id']}  {f['package']}=={f['version']}  ({f['file']})  fix: {fix}")
        print("  Bump the pin with operator/requirements/compile.sh (a Hermes-locked package:")
        print("  add it to hermes-security.in, or promote HERMES_UPSTREAM_SHA), or add a")
        print("  time-boxed, tracked, triaged entry to .github/pip-audit-allowlist.json.")
        return 1
    print("  no untolerated vulnerabilities in the pinned set or the Hermes venv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

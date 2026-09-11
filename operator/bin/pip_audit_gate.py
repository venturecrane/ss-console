#!/usr/bin/env python3
"""Audit the seat image's pinned Python against known vulnerabilities.

Runs ``pip-audit`` over every hash-locked file under ``operator/requirements/``
(the exact bytes the Dockerfile installs) and fails on any advisory that is
not in the time-boxed allowlist at ``.github/pip-audit-allowlist.json``. The
allowlist has the same shape and the same rules as the npm one
(``.github/audit-allowlist.json``): an entry is legitimate only when the
advisory is triaged non-reachable on a seat AND its fix is genuinely
unavailable; it names a tracking issue and expires on a date, after which the
gate re-fails so the fix has to land. An empty ``allow`` map means no
exceptions are in force.

pip-audit reports OSV / PyPA advisories without a severity field this gate can
trust, so the bar is "any known vulnerability", not "high or critical".

Exit codes: 0 clean (or every finding allowlisted and unexpired); 1 findings;
2 pip-audit itself could not run (never silently green: a scanner that cannot
run is a HOLD, the send-reconciler lesson).

Run locally::

    uvx --from pip-audit==2.10.1 python3 operator/bin/pip_audit_gate.py
    (or: pip install pip-audit==2.10.1 && python3 operator/bin/pip_audit_gate.py)
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REQUIREMENTS = REPO / "operator" / "requirements"
ALLOWLIST = REPO / ".github" / "pip-audit-allowlist.json"


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


def audit(requirements: Path) -> list[dict]:
    exe = shutil.which("pip-audit")
    if exe is None:
        print("pip-audit is not installed", file=sys.stderr)
        raise SystemExit(2)
    proc = subprocess.run(  # noqa: S603 - list argv from shutil.which, no shell; the only variable is the requirements path
        [
            exe,
            "--strict",
            # The files are fully pinned and hashed, so pip-audit needs no
            # resolver and no temporary venv: --disable-pip reads the pins as
            # written, which is also the only mode that cannot drift from what
            # the Dockerfile installs.
            "--disable-pip",
            "--no-deps",
            "--require-hashes",
            "--format",
            "json",
            "--progress-spinner",
            "off",
            "-r",
            str(requirements),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # pip-audit exits 1 when it found vulnerabilities; that is a result, not a
    # failure to run. Anything else with no JSON is the scanner breaking.
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"pip-audit could not scan {requirements.name}:", file=sys.stderr)
        print(proc.stderr.strip() or proc.stdout.strip(), file=sys.stderr)
        raise SystemExit(2) from None
    findings: list[dict] = []
    for dep in report.get("dependencies", []):
        for vuln in dep.get("vulns", []) or []:
            findings.append(
                {
                    "file": requirements.name,
                    "package": dep.get("name"),
                    "version": dep.get("version"),
                    "id": vuln.get("id"),
                    "aliases": vuln.get("aliases") or [],
                    "fix_versions": vuln.get("fix_versions") or [],
                }
            )
    return findings


def main() -> int:
    files = sorted(REQUIREMENTS.glob("*.txt"))
    if not files:
        print(f"no requirement files under {REQUIREMENTS}", file=sys.stderr)
        return 2
    allow = load_allowlist()
    blocking: list[dict] = []
    tolerated: list[dict] = []
    for req in files:
        for finding in audit(req):
            ids = {finding["id"], *finding["aliases"]}
            if ids & set(allow):
                tolerated.append(finding)
            else:
                blocking.append(finding)
    print(f"pip-audit: scanned {len(files)} requirement file(s)")
    for f in tolerated:
        print(f"  tolerated {f['id']} in {f['package']}=={f['version']} ({f['file']}) until the allowlist expires")
    if blocking:
        print(f"  {len(blocking)} blocking advisory(ies):")
        for f in blocking:
            fix = ", ".join(f["fix_versions"]) or "no fix published"
            print(f"    {f['id']}  {f['package']}=={f['version']}  ({f['file']})  fix: {fix}")
        print("  Bump the pin with operator/requirements/compile.sh, or add a time-boxed,")
        print("  tracked, triaged entry to .github/pip-audit-allowlist.json.")
        return 1
    print("  no known vulnerabilities in the pinned set")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

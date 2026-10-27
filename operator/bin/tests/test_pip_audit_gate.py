"""pip_audit_gate.py audits the Hermes gateway venv, not just our own files (DP-1, 2026-10-27).

Until this, the gate globbed ``operator/requirements/*.txt`` and nothing else,
so the venv every seat's gateway runs in (Hermes' own uv.lock) was never read:
certifi with compromised emSign intermediates and PyJWT with key-confusion
bypasses passed it on every run. These tests hold the two halves:

* the gate's verdict includes the Hermes venv's effective set, and an advisory
  there that the allowlist does not name fails it (the regression);
* the effective set is Hermes' export with hermes-security.txt substituted,
  read from the same Dockerfile pin the image builds from.

The offline tests stub the network and the scanner. ``test_live_*`` runs the
real thing (fetch, ``uv export``, pip-audit) when ``PIP_AUDIT_LIVE=1`` and both
tools are on PATH; the security workflow's pip-audit job is its CI form.

Run::

    cd operator && python3 -m pytest bin/tests/test_pip_audit_gate.py -q
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1]
REQUIREMENTS = BIN.parent / "requirements"


def _load_gate():
    spec = importlib.util.spec_from_file_location("pip_audit_gate", BIN / "pip_audit_gate.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pip_audit_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_gate()

EXPORT = """\
certifi==2026.5.20
pyjwt==2.13.0
tzdata==2025.3 ; sys_platform == 'win32'
urllib3==2.7.0
"""


def _finding(pkg: str, version: str, vid: str, where: str) -> dict:
    return {"file": where, "package": pkg, "version": version, "id": vid, "aliases": [], "fix_versions": []}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """Stub the fetch and the scanner: our files are clean; the Hermes set carries one advisory."""
    hermes_findings: list[dict] = []

    def fake_audit(requirements: Path, *, label: str | None = None, hashed: bool = True) -> list[dict]:
        if label and label.startswith(gate.HERMES_LABEL):
            assert not hashed, "Hermes' export carries no hashes; auditing it hashed would refuse"
            return list(hermes_findings)
        return []

    monkeypatch.setattr(gate, "audit", fake_audit)
    monkeypatch.setattr(gate, "fetch_hermes_export", lambda sha, extras, work: EXPORT)
    return hermes_findings


def test_an_unallowlisted_advisory_in_the_hermes_venv_fails_the_gate(offline, monkeypatch) -> None:
    # FALSIFIER: the pre-DP-1 gate never read Hermes' lock, so this returned 0.
    offline.append(_finding("pyjwt", "2.13.0", "PYSEC-NEW-1", f"{gate.HERMES_LABEL}@345cd2b057a4"))
    monkeypatch.setattr(gate, "load_allowlist", lambda: {"PYSEC-OTHER": "triaged"})
    assert gate.main() == 1


def test_an_allowlisted_advisory_in_the_hermes_venv_is_tolerated(offline, monkeypatch) -> None:
    offline.append(_finding("pyjwt", "2.13.0", "PYSEC-NEW-1", f"{gate.HERMES_LABEL}@345cd2b057a4"))
    monkeypatch.setattr(gate, "load_allowlist", lambda: {"PYSEC-NEW-1": "triaged"})
    assert gate.main() == 0


def test_a_clean_hermes_venv_passes(offline, monkeypatch) -> None:
    monkeypatch.setattr(gate, "load_allowlist", dict)
    assert gate.main() == 0


def test_the_audited_set_is_hermes_export_with_the_overrides_applied(offline, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def capture(requirements: Path, *, label: str | None = None, hashed: bool = True) -> list[dict]:
        if label and label.startswith(gate.HERMES_LABEL):
            seen["text"] = requirements.read_text(encoding="utf-8")
        return []

    monkeypatch.setattr(gate, "audit", capture)
    monkeypatch.setattr(gate, "load_allowlist", dict)
    assert gate.main() == 0
    effective = gate.pins(seen["text"])
    overrides = gate.pins((REQUIREMENTS / "hermes-security.txt").read_text(encoding="utf-8"))
    assert overrides, "hermes-security.txt pins nothing"
    for name, version in overrides.items():
        assert effective[name] == version, f"{name} audited at {effective[name]}, image installs {version}"
    assert effective["pyjwt"] == "2.13.0", "a package with no override is audited at Hermes' locked version"


def test_apply_overrides_swaps_only_the_version_and_keeps_markers() -> None:
    out = gate.apply_overrides(EXPORT, {"certifi": "2026.10.23", "tzdata": "2026.1"})
    assert "certifi==2026.10.23\n" in out
    assert "tzdata==2026.1 ; sys_platform == 'win32'\n" in out
    assert "pyjwt==2.13.0\n" in out and "urllib3==2.7.0\n" in out


def test_an_override_for_a_package_hermes_does_not_install_is_an_error() -> None:
    with pytest.raises(ValueError, match="does not install"):
        gate.apply_overrides(EXPORT, {"requests": "9.9.9"})


def test_the_hermes_pin_and_extras_are_the_dockerfiles() -> None:
    sha, extras = gate.hermes_pin()
    assert len(sha) == 40 and all(c in "0123456789abcdef" for c in sha)
    assert sorted(extras) == ["all", "anthropic", "messaging"]


def test_the_override_file_moves_only_near_zero_risk_packages() -> None:
    # Admission rule (hermes-security.in header): PyJWT and every fix that
    # changes an API Hermes calls is a Hermes promotion, not an override.
    overrides = gate.pins((REQUIREMENTS / "hermes-security.txt").read_text(encoding="utf-8"))
    assert set(overrides) == {"certifi", "urllib3"}


_LIVE = os.environ.get("PIP_AUDIT_LIVE") == "1" and shutil.which("pip-audit") and shutil.which("uv")


@pytest.mark.skipif(not _LIVE, reason="needs PIP_AUDIT_LIVE=1, network, pip-audit and uv")
def test_live_hermes_lock_at_the_pin_carries_the_advisories_the_overrides_close(tmp_path: Path) -> None:
    sha, extras = gate.hermes_pin()
    export = gate.fetch_hermes_export(sha, extras, tmp_path)
    bare = tmp_path / "bare.txt"
    bare.write_text(export, encoding="utf-8")
    before = {f["package"] for f in gate.audit(bare, label="bare", hashed=False)}
    assert {"certifi", "pyjwt", "urllib3"} <= before, before
    overrides = gate.pins((REQUIREMENTS / "hermes-security.txt").read_text(encoding="utf-8"))
    effective = tmp_path / "effective.txt"
    effective.write_text(gate.apply_overrides(export, overrides), encoding="utf-8")
    after = {f["package"] for f in gate.audit(effective, label="effective", hashed=False)}
    assert not ({"certifi", "urllib3"} & after), after
    assert "pyjwt" in after, "PyJWT is not overridden; its advisories must stay visible"

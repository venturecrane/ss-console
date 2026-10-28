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
import json
import os
import shutil
import subprocess
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
brotlicffi==1.2.0.2
certifi==2026.10.19
pyjwt==2.13.0
tzdata==2025.3 ; sys_platform == 'win32'
urllib3==2.8.0
"""
# The real export is ~130 lines; the gate refuses to call fewer than
# HERMES_MIN_AUDITED applicable pins a clean audit, so the offline set is padded.
FILLER = "".join(f"filler-pkg-{i}==1.0.{i}\n" for i in range(gate.HERMES_MIN_AUDITED))


def _finding(pkg: str, version: str, vid: str, where: str) -> dict:
    return {"file": where, "package": pkg, "version": version, "id": vid, "aliases": [], "fix_versions": []}


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    """Stub the fetch and the scanner: our files are clean; the Hermes set carries one advisory."""
    hermes_findings: list[dict] = []

    def fake_audit(
        requirements: Path, *, label: str | None = None, hashed: bool = True, min_audited: int = 0
    ) -> list[dict]:
        if label and label.startswith(gate.HERMES_LABEL):
            assert not hashed, "Hermes' export carries no hashes; auditing it hashed would refuse"
            return list(hermes_findings)
        return []

    monkeypatch.setattr(gate, "audit", fake_audit)
    monkeypatch.setattr(gate, "fetch_hermes_export", lambda sha, extras, work: EXPORT + FILLER)
    return hermes_findings


def test_an_unallowlisted_advisory_in_the_hermes_venv_fails_the_gate(offline, monkeypatch) -> None:
    # FALSIFIER: the pre-DP-1 gate never read Hermes' lock, so this returned 0.
    offline.append(_finding("pyjwt", "2.13.0", "PYSEC-NEW-1", f"{gate.HERMES_LABEL}@bf0203a794a0"))
    monkeypatch.setattr(gate, "load_allowlist", lambda: {"PYSEC-OTHER": "triaged"})
    assert gate.main() == 1


def test_an_allowlisted_advisory_in_the_hermes_venv_is_tolerated(offline, monkeypatch) -> None:
    offline.append(_finding("pyjwt", "2.13.0", "PYSEC-NEW-1", f"{gate.HERMES_LABEL}@bf0203a794a0"))
    monkeypatch.setattr(gate, "load_allowlist", lambda: {"PYSEC-NEW-1": "triaged"})
    assert gate.main() == 0


def test_a_clean_hermes_venv_passes(offline, monkeypatch) -> None:
    monkeypatch.setattr(gate, "load_allowlist", dict)
    assert gate.main() == 0


def test_the_audited_set_is_hermes_export_with_the_overrides_applied(offline, monkeypatch) -> None:
    seen: dict[str, str] = {}

    def capture(
        requirements: Path, *, label: str | None = None, hashed: bool = True, min_audited: int = 0
    ) -> list[dict]:
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
    out = gate.apply_overrides(EXPORT, {"brotlicffi": "1.2.1.0", "tzdata": "2026.1"})
    assert "brotlicffi==1.2.1.0\n" in out
    assert "tzdata==2026.1 ; sys_platform == 'win32'\n" in out
    assert "pyjwt==2.13.0\n" in out and "urllib3==2.8.0\n" in out


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
    assert set(overrides) == {"brotlicffi"}


# --- The floor: a marker-skipped audit is not a clean audit (ss-console#3564) ---
#
# Hermes v0.22 supports only Python 3.14. pip-audit reads a requirement only when
# its environment marker is true on the interpreter pip-audit runs on, so an
# export whose lines carry ``python_version >= '3.14'``, audited from 3.13, is
# read as almost nothing and reported clean (observed 2026-10-27 with
# pip-audit 2.10.1 on v0.22.5's export so marked: 117 audited on 3.14, 7 on
# 3.13, exit 0).

MARKED = "brotlicffi==1.2.0.2 ; python_version >= '3.14'\n" + "".join(
    f"pkg-{i}==1.0.{i} ; python_version >= '3.14'\n" for i in range(gate.HERMES_MIN_AUDITED + 20)
)
PY313 = {"python_version": "3.13", "python_full_version": "3.13.5"}
PY314 = {"python_version": "3.14", "python_full_version": "3.14.10"}


def test_a_314_marked_export_evaluated_on_313_has_nothing_live() -> None:
    assert gate.live_pin_count(MARKED, PY314) == gate.HERMES_MIN_AUDITED + 21
    assert gate.live_pin_count(MARKED, PY313) == 0
    assert gate.live_pin_count(EXPORT + FILLER, PY313) >= gate.HERMES_MIN_AUDITED


def test_a_marker_skipped_hermes_export_trips_the_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    # FALSIFIER: without the floor this export, audited from 3.13, audits nothing
    # and the gate passes green with every Hermes advisory unseen.
    monkeypatch.setattr(gate, "fetch_hermes_export", lambda sha, extras, work: MARKED)
    real = gate.live_pin_count
    monkeypatch.setattr(gate, "live_pin_count", lambda text, environment=None: real(text, PY313))

    def no_hermes_audit(requirements: Path, *, label: str | None = None, **_: object) -> list[dict]:
        assert not (label and label.startswith(gate.HERMES_LABEL)), "audited a set the floor refuses"
        return []

    monkeypatch.setattr(gate, "audit", no_hermes_audit)
    monkeypatch.setattr(gate, "load_allowlist", dict)
    with pytest.raises(SystemExit) as exc:
        gate.main()
    assert exc.value.code == 2


def test_pip_audit_reporting_fewer_packages_than_the_floor_exits_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The second half of the floor: whatever the pre-count said, pip-audit's own
    # report must cover the set. Stub pip-audit's JSON with seven packages.
    report = {"dependencies": [{"name": f"p{i}", "version": "1", "vulns": []} for i in range(7)]}
    monkeypatch.setattr(gate.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        gate.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, json.dumps(report), ""),
    )
    req = tmp_path / "r.txt"
    req.write_text("p0==1\n", encoding="utf-8")
    assert gate.audit(req, label="x", hashed=False) == []
    with pytest.raises(SystemExit) as exc:
        gate.audit(req, label="x", hashed=False, min_audited=gate.HERMES_MIN_AUDITED)
    assert exc.value.code == 2


_LIVE = os.environ.get("PIP_AUDIT_LIVE") == "1" and shutil.which("pip-audit") and shutil.which("uv")


@pytest.mark.skipif(not _LIVE, reason="needs PIP_AUDIT_LIVE=1, network, pip-audit and uv")
def test_live_hermes_lock_at_the_pin_carries_the_advisory_the_override_closes(tmp_path: Path) -> None:
    sha, extras = gate.hermes_pin()
    export = gate.fetch_hermes_export(sha, extras, tmp_path)
    bare = tmp_path / "bare.txt"
    bare.write_text(export, encoding="utf-8")
    before = {f["package"] for f in gate.audit(bare, label="bare", hashed=False)}
    assert "brotlicffi" in before, before
    overrides = gate.pins((REQUIREMENTS / "hermes-security.txt").read_text(encoding="utf-8"))
    effective = tmp_path / "effective.txt"
    effective.write_text(gate.apply_overrides(export, overrides), encoding="utf-8")
    after = {f["package"] for f in gate.audit(effective, label="effective", hashed=False)}
    assert "brotlicffi" not in after, after
    assert len(gate.pins(export)) >= gate.HERMES_MIN_AUDITED


def test_the_hermes_lock_fetch_retries_a_rate_limited_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """raw.githubusercontent.com answers 429 under load; one 429 must not fail the gate (2026-10-27)."""
    seen: list[list[str]] = []

    def fake_run(argv, *a, **k):
        seen.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "stop here")

    monkeypatch.setattr(gate.subprocess, "run", fake_run)
    monkeypatch.setattr(gate.shutil, "which", lambda name: f"/usr/bin/{name}")
    with pytest.raises(SystemExit):
        gate.fetch_hermes_export("0" * 40, ["all"], tmp_path)
    assert seen and seen[0][0] == "/usr/bin/curl"
    assert "--retry" in seen[0], seen[0]


def test_compile_sh_retries_both_hermes_fetches() -> None:
    src = (Path(__file__).resolve().parents[2] / "requirements" / "compile.sh").read_text()
    fetches = [ln for ln in src.splitlines() if ln.startswith("curl ") and "/hermes/" in ln]
    assert len(fetches) == 2 and all("--retry" in ln for ln in fetches), fetches

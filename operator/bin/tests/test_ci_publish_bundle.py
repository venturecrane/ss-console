"""scripts/ci-publish-bundle.sh, run end to end against fakes: what it publishes,
in what order, and what it refuses.

2026-10-31, the first real publish (run 41238271283): the content check reported
"the tarball holds no jobs/.venv/bin/jobs" for an entry the tarball held. The
check must pass a tarball that holds every guaranteed entry and refuse one that
lacks any.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci-publish-bundle.sh"


def _code() -> str:
    return "\n".join(
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
    )


# -- the layout (ADR 0103), the script run end to end against fakes ---------------------
INSTALLER_MANIFEST = SCRIPT.parents[1] / "operator" / "hermes-smd-overlay" / "bundle_installer" / "manifest.py"
AFTER = "ab" * 20


def test_the_published_layout_is_the_one_the_seat_installer_mounts() -> None:
    """FALSIFIER: bump one side alone and every seat refuses every new bundle
    (or adopts one built for other paths)."""
    published = re.search(r"^LAYOUT=(\d+)$", _code(), re.M)
    spec = re.search(r"^    layout: int = (\d+)$", INSTALLER_MANIFEST.read_text(encoding="utf-8"), re.M)
    assert published and spec and published.group(1) == spec.group(1)
    # The guaranteed paths are the layout's: each one is a path the installer requires.
    source = INSTALLER_MANIFEST.read_text(encoding="utf-8")
    listed = (SCRIPT.parents[1] / "operator" / "templates" / "jobs-bundle-paths.txt").read_text(encoding="utf-8")
    for entry in (ln.strip() for ln in listed.splitlines()):
        if entry and not entry.startswith("#"):
            assert f'"{entry}"' in source, entry


_FAKE_DOCKER = """#!/bin/sh
case "$1" in
  login) cat >/dev/null; exit 0 ;;
  pull) exit 0 ;;
  run) ;;
  *) exit 1 ;;
esac
out=""
prev=""
for a in "$@"; do
  if [ "$prev" = "-v" ]; then case "$a" in *:/out) out="${a%:/out}" ;; esac; fi
  prev="$a"
done
tree="$(mktemp -d)"
mkdir -p "$tree/jobs/.venv/bin" "$tree/connectors/smokeball/.venv/bin"
for f in jobs/.venv/bin/jobs jobs/.venv/bin/python connectors/smokeball/.venv/bin/smokeball-mcp \\
         connectors/smokeball/.venv/bin/python; do echo x > "$tree/$f"; done
echo 3.13.9 > "$out/python"
[ -z "$SKIP_ENTRY" ] || rm "$tree/$SKIP_ENTRY"
tar -C "$tree" -cf "$out/bundle.tar" jobs connectors/smokeball
"""

_FAKE_AWS = """#!/bin/sh
# aws s3 cp SRC DST [flags]: s3://<bucket>/<key> is $FAKE_R2/<bucket>/<key>.
[ "$1 $2" = "s3 cp" ] || exit 1
to_path() { case "$1" in s3://*) echo "$FAKE_R2/${1#s3://}" ;; *) echo "$1" ;; esac; }
src="$(to_path "$3")" dst="$(to_path "$4")"
mkdir -p "$(dirname "$dst")"
cp "$src" "$dst"
case "$4" in s3://*) echo "put ${4#s3://*/}" >> "$EVENTS" ;; esac
"""

_FAKE_FLEET = """#!/bin/sh
case "$1" in
  bundle-base) echo registry.fly.io/hermes-fleet:deadbeef ;;
  commit-seq) echo 2680 ;;
  bundle-record) echo "record $*" >> "$EVENTS"; [ -z "$FAIL_RECORD" ] ;;
  *) exit 1 ;;
esac
"""

# /bin/bash on macOS is 3.2 and has no mapfile; CI's bash 5 has it. The shim is
# loaded only where the builtin is missing, so CI runs the script as written.
_MAPFILE_SHIM = """
if ! type mapfile >/dev/null 2>&1; then
  mapfile() { local _n="$2" _l _i=0; eval "$_n=()"; while IFS= read -r _l; do eval "$_n[$_i]=\\$_l"; _i=$((_i+1)); done; }
fi
"""


def _publish(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    for tool in ("zstd", "sha256sum", "tar"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not on PATH")
    repo, fakes, r2 = tmp_path / "repo", tmp_path / "bin", tmp_path / "r2"
    (repo / "scripts" / "lib").mkdir(parents=True)
    (repo / "operator" / "bin").mkdir(parents=True)
    (repo / "operator" / "templates").mkdir(parents=True)
    fakes.mkdir()
    shutil.copy(SCRIPT, repo / "scripts" / SCRIPT.name)
    shutil.copy(SCRIPT.parents[1] / "operator" / "templates" / "jobs-bundle-paths.txt", repo / "operator" / "templates")
    (repo / "scripts" / "lib" / "r2-creds.sh").write_text(
        "derive_r2_creds() { R2_ACCESS_KEY_ID=k; R2_SECRET_ACCESS_KEY=s; R2_ENDPOINT_URL=http://fake; }\n"
    )
    for path, body in ((fakes / "docker", _FAKE_DOCKER), (fakes / "aws", _FAKE_AWS),
                       (repo / "operator" / "bin" / "fleet", _FAKE_FLEET)):  # fmt: skip
        path.write_text(body)
        path.chmod(0o755)
    shim = tmp_path / "bash_env"
    shim.write_text(_MAPFILE_SHIM)
    events = tmp_path / "events"
    run_env = {
        "PATH": f"{fakes}:{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "AFTER_SHA": AFTER,
        "FLY_REGISTRY_TOKEN": "t",
        "R2_BUCKET_CONFIG": "cfg",
        "FAKE_R2": str(r2),
        "EVENTS": str(events),
        "BASH_ENV": str(shim),
        **env,
    }
    proc = subprocess.run(  # noqa: S603 - fixed argv, the script under test
        ["bash", str(repo / "scripts" / SCRIPT.name)], env=run_env, capture_output=True, text=True, timeout=120
    )
    lines = events.read_text().splitlines() if events.exists() else []
    return proc, r2 / "cfg" / "fleet" / "bundles" / "jobs", lines


def test_a_publish_writes_the_layout_and_then_the_layout_latest_pointer(tmp_path: Path) -> None:
    """FALSIFIER: no layout-2/latest.json and a new-layout image on an old pointer
    boots with no runner; write it before the D1 record and a seat can boot a
    version the controller never recorded."""
    proc, prefix, events = _publish(tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    (manifest_path,) = prefix.glob("*.json")
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest["layout"] == 2 and manifest["name"] == "jobs" and manifest["ss_console_sha"] == AFTER
    # The seat orders its bundle against its image by this number (jobs-bundle-commit-probe.py).
    assert manifest["ss_console_seq"] == 2680
    latest = json.loads((prefix / "layout-2" / "latest.json").read_bytes())
    assert latest == {
        "version": manifest["version"],
        "manifest_key": f"fleet/bundles/jobs/{manifest['version']}.json",
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
    }
    v = manifest["version"]
    kinds = ["record" if e.startswith("record ") else e.split()[1] for e in events]
    assert kinds == [
        f"fleet/bundles/jobs/{v}.tar.zst",
        f"fleet/bundles/jobs/{v}.json",
        "record",
        "fleet/bundles/jobs/layout-2/latest.json",
    ], events


def test_a_failed_record_writes_no_layout_latest(tmp_path: Path) -> None:
    proc, prefix, _events = _publish(tmp_path, FAIL_RECORD="1")
    assert proc.returncode != 0
    assert list(prefix.glob("*.json")), "the manifest went up before the record"
    assert not (prefix / "layout-2" / "latest.json").exists()


@pytest.mark.parametrize("entry", ["jobs/.venv/bin/jobs", "connectors/smokeball/.venv/bin/smokeball-mcp"])
def test_a_tarball_missing_a_guaranteed_entry_publishes_nothing(tmp_path: Path, entry: str) -> None:
    """FALSIFIER: drop the content check and the manifest goes up for a bundle a seat cannot run."""
    proc, prefix, events = _publish(tmp_path, SKIP_ENTRY=entry)
    assert proc.returncode != 0
    assert f"the tarball holds no {entry}" in proc.stdout + proc.stderr
    assert events == [] and not list(prefix.glob("*.json"))

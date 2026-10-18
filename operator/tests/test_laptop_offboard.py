"""laptop-offboard.py: a failed manifest read stops the run; it never reads as "nothing to delete"."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[1] / "ops-machine" / "laptop-offboard.py"
_spec = importlib.util.spec_from_file_location("laptop_offboard", _SRC)
assert _spec and _spec.loader
lo = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = lo  # @dataclass resolves its module through sys.modules
_spec.loader.exec_module(lo)


def test_check_fetch_returns_manifests_before_end_marker():
    text = f"{'a' * 64}  ./x\n{lo.END}\n"
    assert lo.check_fetch(text, "home") == f"{'a' * 64}  ./x\n"


def test_check_fetch_absent_area_is_empty():
    assert lo.check_fetch(f"{lo.NO_AREA}\n", "tmp") == ""


@pytest.mark.parametrize("stdout", ["", "Error: ssh: handshake failed\n", f"{'a' * 64}  ./x\n"])
def test_check_fetch_without_marker_raises(stdout):
    with pytest.raises(lo.FetchFailed):
        lo.check_fetch(stdout, "home")


def _home_with_file(tmp_path: Path) -> tuple[Path, str]:
    home = tmp_path / "home"
    f = home / "Desktop" / "a.txt"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    return home, f"{hashlib.sha256(b'x').hexdigest()}  ./a.txt\n"


def test_one_failed_area_deletes_nothing_anywhere(tmp_path):
    home, manifest = _home_with_file(tmp_path)

    def fetch(area):
        if area == "home":
            raise lo.FetchFailed("home: could not read")
        return manifest if area == "desktop" else ""

    out = io.StringIO()
    assert lo.run(home, "m16", fetch, execute=True, out=out) == 2
    assert (home / "Desktop" / "a.txt").exists()
    assert "STOPPED" in out.getvalue()


def test_good_reads_delete_verified_file(tmp_path):
    home, manifest = _home_with_file(tmp_path)
    out = io.StringIO()
    assert lo.run(home, "m16", lambda a: manifest if a == "desktop" else "", execute=True, out=out) == 0
    assert not (home / "Desktop" / "a.txt").exists()


def test_unlisted_files_are_counted_and_kept(tmp_path):
    home, manifest = _home_with_file(tmp_path)
    (home / "Desktop" / "mine.txt").write_bytes(b"y")
    out = io.StringIO()
    assert lo.run(home, "m16", lambda a: manifest if a == "desktop" else "", execute=True, out=out) == 0
    assert (home / "Desktop" / "mine.txt").exists()
    assert "unlisted, kept 1 (. 1)" in out.getvalue()

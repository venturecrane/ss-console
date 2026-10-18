"""laptop-offboard.py deletes exactly the verified files and nothing else.

The first version deleted whole ledgered directories, which on mac23 would have
taken ~/.claude/projects/<repo>/memory/ (557 files) and every transcript written
after the copy (peer /code-review, 2026-10-17). These tests pin the replacement:
hash-matched files only, memory and ~/dev never, changed files kept, read-back.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "ops-machine" / "laptop-offboard.py"
_spec = importlib.util.spec_from_file_location("laptop_offboard", _SRC)
assert _spec and _spec.loader
lo = importlib.util.module_from_spec(_spec)
sys.modules["laptop_offboard"] = lo  # dataclasses resolve their module by name
_spec.loader.exec_module(lo)


def _write(p: Path, body: str) -> str:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return hashlib.sha256(body.encode()).hexdigest()


def _manifest(lines: list[tuple[str, str]]) -> str:
    return "".join(f"{d}  {r}\n" for d, r in lines)


def _setup(tmp_path: Path):
    home = tmp_path / "home"
    projects = home / ".claude" / "projects" / "-Users-x-dev-ss-console"
    files = {
        "chron": (home / "smd-medchron-data" / "irma" / "chron.json", "records"),
        "transcript": (projects / "abc.jsonl", "old session"),
        "memory": (projects / "memory" / "MEMORY.md", "index"),
        "later": (projects / "def.jsonl", "copied then appended"),
        "devfile": (home / "dev" / "ss-console" / "x.py", "code"),
        "download": (home / "Downloads" / "TaxReturn.pdf", "personal"),
    }
    digests = {k: _write(p, body) for k, (p, body) in files.items()}
    # A transcript that changed after the copy: the manifest holds the OLD hash.
    digests["later"] = hashlib.sha256(b"copied").hexdigest()
    by_area = {
        "smd-medchron-data": _manifest([(digests["chron"], "./irma/chron.json")]),
        "transcripts": _manifest(
            [
                (digests["transcript"], "./-Users-x-dev-ss-console/abc.jsonl"),
                (digests["memory"], "./-Users-x-dev-ss-console/memory/MEMORY.md"),
                (digests["later"], "./-Users-x-dev-ss-console/def.jsonl"),
            ]
        ),
        "home": _manifest(
            [
                (digests["devfile"], "./dev/ss-console/x.py"),
                (digests["download"], "./Downloads/TaxReturn.pdf"),
                (digests["chron"], "../../etc/passwd"),
            ]
        ),
    }
    return home, files, (lambda area: by_area.get(area, ""))


def test_dry_run_deletes_nothing(tmp_path: Path) -> None:
    home, files, fetch = _setup(tmp_path)
    out = io.StringIO()
    assert lo.run(home, "mac23", fetch, execute=False, out=out) == 0
    assert all(p.exists() for p, _ in files.values())
    assert "would delete 2 files" in out.getvalue()


def test_execute_deletes_only_hash_matched_unprotected_files(tmp_path: Path) -> None:
    home, files, fetch = _setup(tmp_path)
    out = io.StringIO()
    assert lo.run(home, "mac23", fetch, execute=True, out=out) == 0
    assert not files["chron"][0].exists()
    assert not files["transcript"][0].exists()
    assert files["memory"][0].exists(), "agent memory must never be deleted"
    assert files["later"][0].exists(), "a file changed since the copy must be kept"
    assert files["devfile"][0].exists(), "~/dev must never be deleted"
    assert files["download"][0].exists(), "~/Downloads must never be deleted"
    assert "read-back: 0 still present" in out.getvalue()
    # The emptied folder goes; the area base and the memory folder stay.
    assert not (home / "smd-medchron-data" / "irma").exists()
    assert (home / "smd-medchron-data").exists()


def test_path_escaping_its_area_is_kept(tmp_path: Path) -> None:
    home, _, fetch = _setup(tmp_path)
    t = lo.classify(home, lo.parse_manifest(fetch("home")), home)
    assert t.escaped == 1 and t.protected == 2 and not t.match


def test_symlink_is_never_followed(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "elsewhere.txt"
    digest = _write(target, "keep me")
    link = home / "Desktop" / "link.txt"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    t = lo.classify(home / "Desktop", [(digest, "./link.txt")], home)
    assert t.symlink == 1 and not t.match


def test_parse_manifest_ignores_noise() -> None:
    good = "a" * 64 + "  ./x y.txt"
    assert lo.parse_manifest(f"Connecting...\r\n{good}\r\nnot a line\n") == [("a" * 64, "./x y.txt")]


# A failed manifest read stops the run (m16 dry run, 2026-10-18).
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

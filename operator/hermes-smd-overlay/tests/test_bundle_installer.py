"""bundle_installer: the jobs bundle's root boot install and the mount decision (ADR 0100 W3).

Every R2 call goes to an in-memory fake, every write to tmp_path, and the
ownership checks run against this test's own uid. The falsifiers that matter:
a tarball or manifest that does not match its digest, an import check that
fails, a member outside the bundle and a link into the agent's volume must each
leave ``current`` where it was and mount it (N-1) when it still verifies, else
NOTHING; R2 being unreachable must still mount what is verified on disk; no
pointer must mean the image copy.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tarfile
from pathlib import Path

import pytest

from bundle_installer import __main__ as cli
from bundle_installer import installer as ins
from bundle_installer.manifest import JOBS, pointer_key, tarball_key

zstd = pytest.importorskip("compression.zstd", reason="the seat's Python 3.14 reads .tar.zst")

SLUG = "acme"
BUCKET = "cfg"
UID = os.getuid()
V1, V2, V3 = "202610310101-aaaaaaaa", "202610310202-bbbbbbbb", "202610310303-cccccccc"


class _Missing(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.gets: list[str] = []
        self.down = False

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 — boto3 shape
        self.gets.append(Key)
        if self.down:
            raise ConnectionError("R2 unreachable")
        if Key not in self.objects:
            raise _Missing(Key)
        return {"Body": io.BytesIO(self.objects[Key])}


def _entry(tar: tarfile.TarFile, name: str, data: bytes = b"", *, mode=0o644, link=None):
    info = tarfile.TarInfo(name)
    if link is not None:
        info.type, info.linkname = tarfile.SYMTYPE, link
    elif name.endswith("/"):
        info.type, info.mode = tarfile.DIRTYPE, 0o755
    else:
        info.size, info.mode = len(data), mode
    tar.addfile(info, io.BytesIO(data) if link is None and not name.endswith("/") else None)


def make_tarball(extra=()) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:zst") as tar:  # pyright: ignore[reportCallIssue, reportArgumentType]
        for d in ("medchron/", "medchron/.venv/", "medchron/.venv/bin/"):
            _entry(tar, d)
        _entry(tar, "medchron/.venv/bin/python", link="/usr/bin/python3")
        _entry(
            tar, "medchron/.venv/bin/medchron", b"#!/opt/medchron/.venv/bin/python\n", mode=0o700
        )
        _entry(tar, "medchron/anthropic_pricing.json", b"{}")
        for d in ("connectors/", "connectors/smokeball/", "connectors/smokeball/.venv/"):
            _entry(tar, d)
        _entry(tar, "connectors/smokeball/.venv/bin/", b"")
        _entry(tar, "connectors/smokeball/.venv/bin/python", link="python3")
        _entry(tar, "connectors/smokeball/.venv/bin/python3", link="/usr/bin/python3")
        _entry(tar, "connectors/smokeball/.venv/bin/smokeball-mcp", b"#!x\n", mode=0o755)
        for args, kwargs in extra:
            _entry(tar, *args, **kwargs)
    return buf.getvalue()


def publish(s3: FakeS3, version: str, *, tarball: bytes | None = None, manifest_patch=None):
    data = make_tarball() if tarball is None else tarball
    s3.objects[tarball_key("jobs", version)] = data
    doc = {
        "name": "jobs",
        "version": version,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "ss_console_sha": "abc",
        "base_image_ref": "registry.fly.io/hermes-fleet:x",
        "python": "3.13",
    }
    doc.update(manifest_patch or {})
    raw = json.dumps(doc).encode()
    key = f"fleet/bundles/jobs/{version}.json"
    s3.objects[key] = raw
    s3.objects[pointer_key(SLUG, "jobs")] = json.dumps(
        {
            "version": version,
            "manifest_key": key,
            "manifest_sha256": hashlib.sha256(raw).hexdigest(),
        }
    ).encode()


class Checks:
    def __init__(self, ok: bool = True) -> None:
        self.ok, self.calls = ok, []

    def __call__(self, python: Path):
        self.calls.append(python)
        return self.ok, "" if self.ok else "ModuleNotFoundError: No module named 'docx'"


def run(s3, home, checks=None):
    return ins.decide(
        s3_client=s3,
        bucket=BUCKET,
        slug=SLUG,
        spec=JOBS,
        home=home,
        import_check=checks or Checks(),
        owner_uid=UID,
    )


def store(home: Path) -> Path:
    return home / ".smd-root" / "bundles" / "jobs"


def current(home: Path) -> str | None:
    link = store(home) / "current"
    return os.readlink(link) if os.path.islink(link) else None


def test_installs_verifies_and_flips_current(tmp_path):
    s3, checks = FakeS3(), Checks()
    publish(s3, V1)
    d = run(s3, tmp_path, checks)
    assert (d.source, d.version, d.path) == ("installed", V1, store(tmp_path) / V1)
    assert current(tmp_path) == V1
    assert d.path is not None
    assert (d.path / "VERSION").read_text().strip() == V1
    # The bundle's own python, checked from staging before the flip.
    assert len(checks.calls) == 1 and str(checks.calls[0]).endswith("medchron/.venv/bin/python")
    assert ".staging-" in str(checks.calls[0])
    # a+rX, as the image gives /opt/medchron (the 0700 script became 0755).
    assert stat.S_IMODE(os.lstat(d.path / "medchron/.venv/bin/medchron").st_mode) == 0o755
    assert stat.S_IMODE(os.lstat(d.path / "medchron/anthropic_pricing.json").st_mode) == 0o644
    assert os.readlink(d.path / "medchron/.venv/bin/python") == "/usr/bin/python3"
    assert not [p for p in os.listdir(store(tmp_path)) if p.startswith(".")]


def test_same_pointer_next_boot_mounts_without_downloading(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    s3.gets.clear()
    checks = Checks()
    d = run(s3, tmp_path, checks)
    assert (d.source, d.version) == ("unchanged", V1)
    assert tarball_key("jobs", V1) not in s3.gets and not checks.calls


def test_keeps_previous_and_deletes_older(tmp_path):
    s3 = FakeS3()
    for v in (V1, V2, V3):
        publish(s3, v)
        assert run(s3, tmp_path).version == v
    assert sorted(p for p in os.listdir(store(tmp_path)) if p != "current") == [V2, V3]
    assert current(tmp_path) == V3


def test_rollback_to_the_kept_version_needs_no_download(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    v1_pointer = s3.objects[pointer_key(SLUG, "jobs")]
    publish(s3, V2)
    run(s3, tmp_path)
    s3.objects[pointer_key(SLUG, "jobs")] = v1_pointer
    s3.gets.clear()
    d = run(s3, tmp_path)
    assert (d.version, current(tmp_path)) == (V1, V1)
    assert tarball_key("jobs", V1) not in s3.gets


@pytest.mark.parametrize(
    "patch",
    [
        {"sha256": "0" * 64},
        {"size": 5},
        {"name": "other"},
        {"version": V3},
    ],
    ids=["tarball-sha", "size", "name", "version"],
)
def test_a_manifest_the_tarball_does_not_match_keeps_the_previous_version(tmp_path, patch):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    publish(s3, V2, manifest_patch=patch)
    d = run(s3, tmp_path)
    # N-1, not the image: the image's copy is a seed frozen at its last build.
    assert (d.source, d.version, d.path) == ("on-disk", V1, store(tmp_path) / V1)
    assert f"jobs {V2} refused" in d.reasons[0]
    assert current(tmp_path) == V1 and not (store(tmp_path) / V2).exists()
    assert not [p for p in os.listdir(store(tmp_path)) if p.startswith(".")]


def test_manifest_bytes_must_match_the_pointer(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    s3.objects["fleet/bundles/jobs/" + V1 + ".json"] += b" "
    d = run(s3, tmp_path)
    # Nothing on disk to fall back to: the image's copy runs.
    assert d.path is None and "pointer.manifest_sha256" in d.reasons[0]
    assert "no installed bundle on disk" in d.reasons[0]


def test_import_check_failure_never_flips_and_keeps_the_previous_version(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    publish(s3, V2)
    d = run(s3, tmp_path, Checks(ok=False))
    assert (d.source, d.version) == ("on-disk", V1) and "No module named" in d.reasons[0]
    assert current(tmp_path) == V1 and not (store(tmp_path) / V2).exists()


def test_an_unreadable_pointer_keeps_the_previous_version(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    s3.objects[pointer_key(SLUG, "jobs")] = b"not json"
    d = run(s3, tmp_path)
    assert (d.source, d.version) == ("on-disk", V1)
    assert current(tmp_path) == V1


def test_no_pointer_runs_the_image_and_drops_current(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    del s3.objects[pointer_key(SLUG, "jobs")]
    d = run(s3, tmp_path)
    assert d.path is None and "no jobs bundle pointer" in d.reasons[0]
    assert current(tmp_path) is None
    # ... so a later boot that cannot reach R2 does not resurrect it.
    s3.down = True
    assert run(s3, tmp_path).path is None


def test_r2_unreachable_mounts_the_verified_on_disk_current(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    s3.down = True
    d = run(s3, tmp_path)
    assert (d.source, d.version, d.path) == ("on-disk", V1, store(tmp_path) / V1)
    assert "unreachable" in d.reasons[0]


def test_r2_unreachable_with_nothing_on_disk_runs_the_image(tmp_path):
    s3 = FakeS3()
    s3.down = True
    d = run(s3, tmp_path)
    assert d.path is None and "no installed bundle" in d.reasons[0]


def test_no_r2_config_uses_on_disk(tmp_path):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    assert run(None, tmp_path).source == "on-disk"


@pytest.mark.parametrize(
    "extra",
    [
        (("etc/cron.d/x", b"* * * * * root sh"), {}),
        (("medchron/.venv/bin/python3",), {"link": "/opt/data/evil"}),
        (("medchron/esc",), {"link": "../../../../x"}),
        (("../outside", b"x"), {}),
    ],
    ids=["outside-members", "link-into-volume", "relative-escape", "dotdot"],
)
def test_members_outside_the_bundle_are_refused(tmp_path, extra):
    s3 = FakeS3()
    publish(s3, V1, tarball=make_tarball([extra]))
    d = run(s3, tmp_path)
    assert d.path is None and current(tmp_path) is None
    assert not (tmp_path / "etc").exists() and not (tmp_path / "outside").exists()


def test_a_required_path_missing_is_refused(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:zst") as tar:  # pyright: ignore[reportCallIssue, reportArgumentType]
        _entry(tar, "medchron/")
        _entry(tar, "medchron/anthropic_pricing.json", b"{}")
    s3 = FakeS3()
    publish(s3, V1, tarball=buf.getvalue())
    d = run(s3, tmp_path)
    assert d.path is None and "lacks" in d.reasons[-1]


def test_a_writable_entry_in_an_installed_tree_is_never_mounted(tmp_path, monkeypatch):
    s3 = FakeS3()
    publish(s3, V1)
    run(s3, tmp_path)
    # The falsifier: the tree check sees one group-writable file (reported
    # through lstat, so the test never widens a real file's mode).
    planted = str(store(tmp_path) / V1 / "medchron/anthropic_pricing.json")
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        st = real_lstat(path, *args, **kwargs)
        if str(path) != planted:
            return st
        fields = list(st)
        fields[stat.ST_MODE] = st.st_mode | stat.S_IWGRP
        return os.stat_result(fields)

    monkeypatch.setattr(ins.os, "lstat", lstat)
    d = run(s3, tmp_path)
    assert d.path is None and "writable" in d.reasons[-1]
    s3.down = True
    assert run(s3, tmp_path).path is None


def test_a_planted_store_link_is_refused(tmp_path):
    elsewhere = tmp_path / "planted"
    elsewhere.mkdir()
    os.symlink(elsewhere, tmp_path / ".smd-root")
    s3 = FakeS3()
    publish(s3, V1)
    d = run(s3, tmp_path)
    assert d.path is None and "store refused" in d.reasons[0]
    assert not list(elsewhere.iterdir())


def test_store_owned_by_another_uid_is_refused(tmp_path):
    (tmp_path / ".smd-root").mkdir()
    d = ins.decide(
        s3_client=FakeS3(), bucket=BUCKET, slug=SLUG, spec=JOBS, home=tmp_path, owner_uid=UID + 1
    )
    assert d.path is None and "owned by uid" in d.reasons[0]


def test_run_import_check_calls_the_images_script(tmp_path):
    runtime = tmp_path / "jobs-runtime.sh"
    log = tmp_path / "argv"
    runtime.write_text(f'printf "%s\\n" "$@" > {log}\n[ "$2" = /good/python ]\n')
    assert ins.run_import_check(Path("/good/python"), runtime=str(runtime))[0] is True
    assert log.read_text().split() == ["import-check", "/good/python"]
    assert ins.run_import_check(Path("/bad"), runtime=str(runtime))[0] is False
    ok, why = ins.run_import_check(Path("/x"), runtime=str(tmp_path / "absent.sh"))
    assert not ok and "not in this image" in why


def test_cli_prints_the_dir_to_mount_or_nothing(tmp_path, monkeypatch, capsys, caplog):
    s3 = FakeS3()
    publish(s3, V1)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("CUSTOMER_SLUG", SLUG)
    monkeypatch.setenv("R2_BUCKET_CONFIG", BUCKET)
    kw = {"client_factory": lambda: s3, "require_root": False, "owner_uid": UID}
    assert cli.main(["jobs"], import_check=Checks(), **kw) == 0
    assert capsys.readouterr().out == f"{V1}\t{store(tmp_path) / V1}\n"
    del s3.objects[pointer_key(SLUG, "jobs")]
    assert cli.main(["jobs"], import_check=Checks(), **kw) == 0
    assert capsys.readouterr().out == ""
    assert "NOT mounted, the image copy runs (no jobs bundle pointer" in caplog.text


def test_cli_a_crash_is_the_image_not_a_failed_boot(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def boom():
        raise RuntimeError("no boto3")

    kw = {"require_root": False, "owner_uid": UID}
    assert cli.main(["jobs"], client_factory=boom, **kw) == 0
    assert capsys.readouterr().out == ""
    assert cli.main(["nope"], **kw) == 2

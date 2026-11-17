"""The signed boot copy of customer.yaml on the volume (config_applier.volume_copy).

A Machine's root filesystem resets on every start, so the live customer.yaml
under /var/lib/smd-config is gone after a restart and an R2 outage meant a
FATAL boot (2026-11-12). The spec verifier now keeps the trio its proof rests
on (yaml, signed pointer, spec) on the volume, and the entrypoint boots from it
only after ``restore`` verifies it again: the volume is the agent's, so a copy
trusted for where it sits would let the agent write its own ceiling.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from config_applier import volume_copy
from config_applier.spec_verifier import SpecVerifier
from tests.test_spec_verifier import SLUG, Clock, FakeS3, image, make_spec, publish  # noqa: F401

POINTER = f"vaults/{SLUG}/spec-pointer.json"


def _pubkey_file(tmp_path: Path, key: Ed25519PrivateKey) -> Path:
    path = tmp_path / "seat-spec.pub"
    path.write_bytes(key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo))
    return path


def _verifier(tmp_path, image, objects, key, copy_dir):  # noqa: F811 - the fixture's name
    running = tmp_path / "customer.yaml"
    running.write_bytes(b"cfg\n")
    return SpecVerifier(
        s3_client=FakeS3(objects),
        bucket="b",
        slug=SLUG,
        running_yaml_path=running,
        state_path=tmp_path / "state.json",
        image=image,
        public_key_loader=key.public_key,
        clock=Clock(),
        volume_copy_dir=copy_dir,
    )


@pytest.fixture
def copy_dir(tmp_path: Path) -> Path:
    return tmp_path / ".smd-root" / "config-copy"


def test_a_verified_running_yaml_is_kept_and_restores_after_a_reset(
    tmp_path,
    image,  # noqa: F811
    copy_dir,
):
    """FALSIFIER: drop ``_keep_copy`` from the verifier's judge and nothing is
    on the volume when the root filesystem comes back empty."""
    key = Ed25519PrivateKey.generate()
    objects: dict[str, bytes] = {}
    sha = publish(objects, make_spec(), key)
    assert _verifier(tmp_path, image, objects, key, copy_dir).step().verdict == "verified"  # type: ignore[union-attr]
    assert (copy_dir / "customer.yaml").read_bytes() == b"cfg\n"

    yaml, verdict = volume_copy.load_verified(SLUG, copy_dir, _pubkey_file(tmp_path, key))
    assert (yaml, verdict.spec_sha256, verdict.compiled_from) == (b"cfg\n", sha, "1" * 40)


def test_a_running_yaml_the_spec_does_not_name_is_never_kept(
    tmp_path,
    image,  # noqa: F811
    copy_dir,
):
    key = Ed25519PrivateKey.generate()
    objects: dict[str, bytes] = {}
    publish(objects, make_spec(), key)
    verifier = _verifier(tmp_path, image, objects, key, copy_dir)
    verifier.running_yaml_path.write_bytes(b"something the agent wrote\n")
    assert verifier.step().customer_yaml_match is False  # type: ignore[union-attr]
    assert not copy_dir.exists()


@pytest.mark.parametrize(
    "tamper",
    ["yaml", "spec", "signature", "other_key", "other_seat"],
)
def test_a_tampered_copy_is_refused(tmp_path, image, copy_dir, tamper):  # noqa: F811
    """The agent owns /opt/data. Whatever it writes into the copy, restore
    refuses unless the image's key verifies a spec naming those exact bytes."""
    key = Ed25519PrivateKey.generate()
    objects: dict[str, bytes] = {}
    publish(objects, make_spec(), key)
    _verifier(tmp_path, image, objects, key, copy_dir).step()
    pubkey = _pubkey_file(tmp_path, key)
    if tamper == "yaml":
        (copy_dir / "customer.yaml").write_bytes(b"external_send: autonomous\n")
    elif tamper == "spec":
        spec = copy_dir / "spec.json"
        spec.write_bytes(spec.read_bytes().replace(b"matter-reader", b"matter-writer"))
    elif tamper == "signature":
        pointer = copy_dir / "spec-pointer.json"
        pointer.write_bytes(pointer.read_bytes().replace(b'"signature": "', b'"signature": "AA'))
    elif tamper == "other_key":
        pubkey = _pubkey_file(tmp_path / "..", Ed25519PrivateKey.generate())
    elif tamper == "other_seat":
        with pytest.raises(volume_copy.CopyRefused):
            volume_copy.load_verified("seat-beta", copy_dir, pubkey)
        return
    with pytest.raises(volume_copy.CopyRefused):
        volume_copy.load_verified(SLUG, copy_dir, pubkey)


def test_a_planted_directory_or_link_is_refused_and_never_written_through(
    tmp_path,
    copy_dir,
):
    """Root never follows a link the agent planted: not to read, not to write."""
    copy_dir.parent.mkdir()
    target = tmp_path / "elsewhere"
    target.mkdir()
    os.symlink(target, copy_dir)
    with pytest.raises(OSError):
        volume_copy.save(b"y", b"p", b"s", copy_dir)
    assert list(target.iterdir()) == []
    with pytest.raises((OSError, volume_copy.CopyRefused)):
        volume_copy.load_verified(SLUG, copy_dir, tmp_path / "none.pub")

    os.unlink(copy_dir)
    copy_dir.mkdir(mode=0o777)
    os.chmod(copy_dir, 0o777)
    with pytest.raises(volume_copy.CopyRefused):
        volume_copy.save(b"y", b"p", b"s", copy_dir)


def test_restore_cli_writes_the_verified_yaml_and_names_its_spec(
    tmp_path,
    image,  # noqa: F811
    copy_dir,
    monkeypatch,
    capsys,
):
    key = Ed25519PrivateKey.generate()
    objects: dict[str, bytes] = {}
    sha = publish(objects, make_spec(), key)
    _verifier(tmp_path, image, objects, key, copy_dir).step()
    monkeypatch.setattr(volume_copy, "COPY_DIR", copy_dir)
    monkeypatch.setattr(volume_copy, "PUBKEY_PATH", _pubkey_file(tmp_path, key))
    dest = tmp_path / "live.yaml"
    assert volume_copy.main(["restore", SLUG, str(dest)]) == 0
    assert dest.read_bytes() == b"cfg\n"
    assert capsys.readouterr().out.split() == [sha, "1" * 40]
    assert volume_copy.main(["restore", "seat-beta", str(tmp_path / "other.yaml")]) == 1
    assert not (tmp_path / "other.yaml").exists()


def test_no_copy_is_a_refusal_not_a_crash(tmp_path, copy_dir):
    with pytest.raises(volume_copy.CopyRefused, match="no copy"):
        volume_copy.load_verified(SLUG, copy_dir, tmp_path / "none.pub")

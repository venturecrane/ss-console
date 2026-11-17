"""The seat's signed copy of its own customer.yaml, kept on the volume for boot.

WHY (2026-11-12). The live customer.yaml lives in the seat's config directory
(``seat_layout.SMD_CONFIG``) on the image's root filesystem, which a Fly Machine resets on every start. So the
entrypoint's "R2 fetch failed; using existing root-owned customer.yaml" branch
could never fire after a restart: an R2 or regional outage meant ``FATAL:
customer.yaml not present and R2 fetch failed``, three quick restarts, and a
client seat parked at "stopped" until someone started it by hand.

THE COPY. Whenever the root spec verifier proves the customer.yaml this seat is
running is the one an authentic signed seat spec names (ADR 0094 amendment,
"a signed seat spec"), it keeps the three things that proof rests on in
``.smd-root/config-copy/`` on the volume (``seat_layout.DATA``): the yaml,
the signed pointer and the spec.

WHY VERIFY AGAIN AT BOOT. The volume belongs to the agent uid, so it can
rename ``.smd-root`` aside and plant a directory of its own (the residual named
in ``runners/supervisor/root_ledger.py``). A copy trusted because of WHERE it
sits would hand the agent its own ceiling on the next outage. So nothing here
trusts the directory: ``restore`` re-checks the Ed25519 signature against the
key baked into the image, the spec digest, that the spec is for THIS seat, and
that the yaml hashes to what the spec names, exactly as the live verifier does.
The agent cannot forge any of it. What it could do is put back an OLDER copy
SMD signed for this same seat; that is the accepted residual, it lasts only
until R2 answers and the config applier converges, and the entrypoint's boot
line names the spec it booted on.

Root writes only through directory fds opened without following a link, each
checked root-owned and writable by nobody else, so a planted link can never
make root write elsewhere (the same rule as ``root_ledger.py``).

    python -m config_applier.volume_copy restore <slug> <dest>
        exit 0: <dest> holds the verified yaml; prints "<spec sha256> <compiled_from>"
        exit 1: no usable copy (the reason on stderr)
"""

from __future__ import annotations

import hashlib
import os
import stat
import sys
from pathlib import Path

from config_applier.spec_verifier import (
    MAX_POINTER_BYTES,
    MAX_SPEC_BYTES,
    PUBKEY_PATH,
    SpecVerdict,
    _authentic_spec,
    _parse_pointer,
    load_public_key,
)
from shared import seat_layout

#: The copy's home: root's own directory on the volume. ``SMD_CONFIG_COPY_DIR``
#: is a test seam only.
COPY_DIR = Path(
    os.environ.get("SMD_CONFIG_COPY_DIR") or Path(seat_layout.DATA) / ".smd-root" / "config-copy"
)
YAML_NAME = "customer.yaml"
POINTER_NAME = "spec-pointer.json"
SPEC_NAME = "spec.json"


class CopyRefused(Exception):
    """The directory or a file in it is not root's own plain file."""


def _check_dir(fd: int, path: str) -> int:
    st = os.fstat(fd)
    if st.st_uid != os.geteuid() or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        os.close(fd)
        raise CopyRefused(f"{path}: not owned by uid {os.geteuid()} or writable by others")
    return fd


def _open_dir(directory: Path, *, create: bool) -> int:
    """``directory`` and its parent, each a real directory of ours, never a link."""
    parent, name = os.path.split(os.path.normpath(str(directory)))
    if create:
        try:
            os.mkdir(parent, 0o755)
        except FileExistsError:
            pass
    pfd = _check_dir(os.open(parent or ".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW), parent)
    try:
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=pfd)
            except FileExistsError:
                pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=pfd)
    finally:
        os.close(pfd)
    return _check_dir(fd, str(directory))


def _read_at(dfd: int, name: str, cap: int) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.geteuid():
            raise CopyRefused(f"{name}: not a plain file of uid {os.geteuid()}")
        data = os.read(fd, cap + 1)
    finally:
        os.close(fd)
    if len(data) > cap:
        raise CopyRefused(f"{name}: larger than {cap} bytes")
    return data


def _write_at(dfd: int, name: str, data: bytes) -> None:
    """``name`` replaced in one rename, from a fresh 0600 file inside ``dfd``."""
    tmp = f".{name}.tmp"
    try:
        os.unlink(tmp, dir_fd=dfd)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=dfd)
    try:
        view = memoryview(data)
        while view:
            view = view[os.write(fd, view) :]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)


def save(yaml: bytes, pointer: bytes, spec: bytes, directory: Path | None = None) -> bool:
    """Keep the proven trio; True when anything changed. Raises OSError/CopyRefused.

    The spec and pointer are written before the yaml, so a crash mid-save leaves
    a trio that fails the yaml-hash check (refused) rather than one that lies.
    """
    dfd = _open_dir(directory or COPY_DIR, create=True)
    try:
        wanted = ((SPEC_NAME, spec), (POINTER_NAME, pointer), (YAML_NAME, yaml))
        current: dict[str, bytes | None] = {}
        for name, _ in wanted:
            try:
                current[name] = _read_at(dfd, name, MAX_SPEC_BYTES)
            except (OSError, CopyRefused):
                current[name] = None
        if all(current[name] == data for name, data in wanted):
            return False
        for name, data in wanted:
            _write_at(dfd, name, data)
        return True
    finally:
        os.close(dfd)


def load_verified(
    slug: str, directory: Path | None = None, pubkey_path: Path | None = None
) -> tuple[bytes, SpecVerdict]:
    """The copy's yaml and the verdict naming its spec. Raises CopyRefused."""
    directory = directory or COPY_DIR
    try:
        dfd = _open_dir(directory, create=False)
    except FileNotFoundError as exc:
        raise CopyRefused(f"no copy on the volume ({directory})") from exc
    try:
        yaml = _read_at(dfd, YAML_NAME, MAX_SPEC_BYTES)
        pointer_bytes = _read_at(dfd, POINTER_NAME, MAX_POINTER_BYTES)
        spec_bytes = _read_at(dfd, SPEC_NAME, MAX_SPEC_BYTES)
    except FileNotFoundError as exc:
        raise CopyRefused(f"the copy is incomplete ({exc.filename})") from exc
    finally:
        os.close(dfd)
    pointer = _parse_pointer(pointer_bytes)
    if pointer is None:
        raise CopyRefused("the copy's pointer is malformed")
    v = SpecVerdict(
        "verified", 0.0, spec_sha256=str(pointer["spec_sha256"]), key_id=str(pointer["key_id"])
    )
    want_key = f"fleet/specs/{slug}/{v.spec_sha256}.json"
    spec = _authentic_spec(
        v,
        pointer,
        slug,
        lambda key: spec_bytes if key == want_key else None,
        load_public_key(pubkey_path or PUBKEY_PATH),
    )
    if spec is None:
        raise CopyRefused(f"the copy's spec does not verify ({v.verdict})")
    named = spec.get("customer_yaml")
    if not isinstance(named, dict) or hashlib.sha256(yaml).hexdigest() != named.get("sha256"):
        raise CopyRefused("the copy's customer.yaml is not the one its signed spec names")
    return yaml, v


def restore(slug: str, dest: Path, directory: Path | None = None) -> SpecVerdict:
    """Write the verified yaml to ``dest`` (0644). Raises CopyRefused / OSError."""
    yaml, verdict = load_verified(slug, directory)
    tmp = dest.with_name(dest.name + ".part")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as fh:
        fh.write(yaml)
    os.replace(tmp, dest)
    return verdict


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] != "restore":
        print("usage: python -m config_applier.volume_copy restore <slug> <dest>", file=sys.stderr)
        return 2
    try:
        verdict = restore(args[1], Path(args[2]))
    except (CopyRefused, OSError) as exc:
        print(f"volume copy not usable: {exc}", file=sys.stderr)
        return 1
    print(f"{verdict.spec_sha256} {verdict.compiled_from or 'unknown'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Root install of a capability bundle, and the decision what to mount (ADR 0100 W3).

    pointer -> manifest (sha-verified) -> tarball (streamed, size + sha verified)
            -> extracted root-owned into a staging dir -> required paths
            -> import check with the bundle's own python -> VERSION -> rename
            -> flip ``current`` -> keep the previous version, delete older

INSTALLED LAYOUT::

    /opt/data/.smd-root/bundles/<name>/
      current -> <version>        (symlink; the commit point, swapped atomically)
      <version>/
        medchron/ ...             (the tarball's members, root-owned, a+rX)
        connectors/smokeball/ ...
        manifest.json             (the verified manifest bytes)
        VERSION                   (written last: present = import check passed)

WHAT GETS MOUNTED (:func:`decide`):

* the pointed-at version, once installed and checked (fresh or already on disk);
* no pointer: NOTHING (the image copy runs, today's behaviour), and ``current``
  is dropped so a later boot that cannot reach R2 does not resurrect a bundle
  the release controller withdrew;
* R2 unreachable: the on-disk ``current``, with a warning;
* a pointed-at version that fails to verify: the on-disk ``current`` (the
  version the seat ran before, N-1) when it still verifies, else NOTHING, and
  the reasons are logged. A bad publish costs the seat the update, never its
  job runner. The image's own copy is the last resort: since runner changes
  stopped building images (the cut), it is a seed frozen at the image's build.

THE TREE CHECK. ``.smd-root`` lives under ``/opt/data``, which the agent uid
owns, so the agent can rename directories there. Root-owned content can only
have been written by root, so before ANY mount the whole chain (``.smd-root``,
``bundles``, ``<name>``, the version dir) and every entry under the version dir
must be root-owned, a real directory or file (never a link where a directory
belongs), and not group- or other-writable. A tree that fails is not mounted:
the root job runner never executes anything another uid could have written.
At this point in boot no agent process exists, and the bind mount pins the
checked inodes, so the check is not raced.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import stat
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from bundle_installer.manifest import (
    MAX_MANIFEST_BYTES,
    BundleManifest,
    BundleSpec,
    parse_bundle_manifest,
    parse_bundle_pointer,
    pointer_key,
    tarball_key,
)
from skills_applier.applier import (
    CURRENT,
    PointerMissing,
    SkillsApplyError,
    _current_target,
    _harden,
    _read_nofollow,
    _remove,
    _write_new,
    commit,
    fetch,
    prune,
    read_version_file,
)
from skills_applier.manifest import MAX_POINTER_BYTES, ManifestError, Pointer, valid_version

logger = logging.getLogger("bundle_installer")

DEFAULT_HOME = "/opt/data"
STORE_PARTS = (".smd-root", "bundles")
MANIFEST_FILE = "manifest.json"
VERSION_FILE = "VERSION"
#: The image's import check (bundle-release, ADR 0100 W2): the same script the
#: Dockerfile and the CI bundle build run, so the three cannot drift apart.
DEFAULT_JOBS_RUNTIME = "/app/jobs-runtime.sh"
#: Absolute symlink targets a bundle may carry. A venv's ``bin/python`` points
#: at the base interpreter under /usr; nothing in a bundle points into the
#: agent's volume, /tmp or /run.
_LINK_ROOTS = ("/usr/", "/opt/medchron/", "/opt/connectors/")
_CHUNK = 1024 * 1024
_IMPORT_TIMEOUT_SECONDS = 300

ImportCheck = Callable[[Path], tuple[bool, str]]


class BundleError(RuntimeError):
    """A bundle that must not be mounted; the message says why."""


class Decision(NamedTuple):
    """``path`` set = mount it; ``path`` None = the image copy runs."""

    source: str  # installed | unchanged | on-disk | image
    version: str | None = None
    path: Path | None = None
    reasons: tuple[str, ...] = ()


def _image(*reasons: str) -> Decision:
    return Decision("image", reasons=tuple(reasons))


# ---------------------------------------------------------------------------
# Ownership checks
# ---------------------------------------------------------------------------


def _check_entry(path: Path, owner_uid: int, *, want_dir: bool) -> None:
    st = os.lstat(path)
    if want_dir and not stat.S_ISDIR(st.st_mode):
        raise BundleError(f"{path} is not a real directory")
    if st.st_uid != owner_uid:
        raise BundleError(f"{path} is owned by uid {st.st_uid}, not {owner_uid}")
    if not stat.S_ISLNK(st.st_mode) and st.st_mode & 0o022:
        raise BundleError(f"{path} is group- or other-writable")


def ensure_store(home: str | Path, name: str, owner_uid: int = 0) -> Path:
    """``<home>/.smd-root/bundles/<name>``, created root-owned 0755 where absent.

    An existing link, a directory another uid owns, or a writable one is
    refused, never repaired: the boot's ``smd_root_loss_dir`` replaces a
    planted ``.smd-root`` later, and nothing will have been mounted from it.
    """
    current = Path(home)
    for part in (*STORE_PARTS, name):
        current = current / part
        try:
            os.mkdir(current, 0o755)
            _harden(current, 0o755)
        except FileExistsError:
            pass
        _check_entry(current, owner_uid, want_dir=True)
    return current


def verify_tree(version_dir: Path, spec: BundleSpec, owner_uid: int = 0) -> None:
    """Refuse a version dir anything but ``owner_uid`` could have written."""
    _check_entry(version_dir, owner_uid, want_dir=True)
    for rel in spec.required:
        if not os.path.lexists(version_dir / rel):
            raise BundleError(f"{version_dir.name}: {rel} is missing")
    for dirpath, dirnames, filenames in os.walk(version_dir, followlinks=False):
        for entry in (*dirnames, *filenames):
            _check_entry(Path(dirpath) / entry, owner_uid, want_dir=False)


# ---------------------------------------------------------------------------
# Download + extraction
# ---------------------------------------------------------------------------


def download(s3_client: Any, bucket: str, key: str, dest: Path, manifest: BundleManifest) -> None:
    """Stream ``key`` into ``dest`` (created new), verifying size and sha256."""
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"]
    except Exception as exc:  # noqa: BLE001 — classified by the caller's reasons
        raise BundleError(f"could not read s3://{bucket}/{key}: {exc}") from exc
    digest, size = hashlib.sha256(), 0
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        while chunk := body.read(_CHUNK):
            size += len(chunk)
            if size > manifest.size:
                raise BundleError(f"{key} is larger than its declared {manifest.size} bytes")
            digest.update(chunk)
            handle.write(chunk)
    if size != manifest.size:
        raise BundleError(f"{key} is {size} bytes, manifest says {manifest.size}")
    if digest.hexdigest() != manifest.sha256:
        raise BundleError(f"{key} does not match the manifest's sha256")


def _inside(rel: str, spec: BundleSpec) -> bool:
    parents = {p.rsplit("/", n)[0] for p in spec.members for n in range(p.count("/") + 1)}
    return rel in parents or any(rel == m or rel.startswith(f"{m}/") for m in spec.members)


def _link_ok(member: tarfile.TarInfo, rel: str, spec: BundleSpec) -> bool:
    target = member.linkname
    if member.issym() and target.startswith("/"):
        return target.startswith(_LINK_ROOTS) and ".." not in target.split("/")
    base = os.path.dirname(rel) if member.issym() else ""
    resolved = os.path.normpath(os.path.join(base, target))
    return not resolved.startswith("..") and _inside(resolved, spec)


def member_filter(spec: BundleSpec) -> Callable[[tarfile.TarInfo, str], tarfile.TarInfo]:
    """The tarfile filter: the stdlib ``tar`` filter plus this bundle's rules.

    Only the spec's members, only files, dirs and links, links that stay in the
    bundle (absolute ones only into :data:`_LINK_ROOTS`), owner root, modes
    a+rX with no setuid and no group/other write (what the image gives
    ``/opt/medchron`` today: ``chown -R root:root`` and ``chmod -R a+rX``).
    """

    def _filter(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo:
        member = tarfile.tar_filter(member, dest)
        rel = os.path.normpath(member.name)
        if not _inside(rel, spec):
            raise tarfile.FilterError(f"{member.name!r} is outside {spec.members}")
        if not (member.isreg() or member.isdir() or member.issym() or member.islnk()):
            raise tarfile.FilterError(f"{member.name!r} is not a file, directory or link")
        if (member.issym() or member.islnk()) and not _link_ok(member, rel, spec):
            raise tarfile.FilterError(f"{member.name!r} links outside the bundle")
        mode = member.mode | 0o444
        if member.isdir() or member.mode & 0o100:
            mode |= 0o111
        return member.replace(mode=mode, uid=0, gid=0, uname="root", gname="root", deep=False)

    return _filter


def extract(tar_path: Path, dest: Path, spec: BundleSpec) -> None:
    try:
        import compression.zstd  # noqa: F401  # pyright: ignore[reportMissingImports] — 3.14+
    except ImportError as exc:
        raise BundleError("this Python cannot read .tar.zst (needs 3.14+)") from exc
    check = member_filter(spec)
    try:
        # Member by member, each one judged by member_filter (the stdlib tar
        # filter plus this bundle's rules) before it is written: one refused
        # member refuses the bundle, and the caller removes the staging dir.
        with tarfile.open(tar_path, mode="r:zst") as archive:  # type: ignore[call-overload]
            for member in archive:
                archive.extract(member, path=dest, filter=check)
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise BundleError(f"extracting {tar_path.name} failed: {exc}") from exc
    # The members' parents (``connectors/`` for ``connectors/smokeball``) may come
    # from no tar entry at all; give every one the image's 0755 root.
    for member in spec.members:
        parts = member.split("/")
        for n in range(1, len(parts) + 1):
            path = dest.joinpath(*parts[:n])
            if os.path.isdir(path) and not os.path.islink(path):
                _harden(path, 0o755)


def run_import_check(python: Path, *, runtime: str = DEFAULT_JOBS_RUNTIME) -> tuple[bool, str]:
    """``sh <runtime> import-check <python>``: the image's own module list."""
    if not os.path.isfile(runtime):
        return False, f"{runtime} is not in this image"
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, no shell, scrubbed env
            ["/bin/sh", runtime, "import-check", str(python)],
            env=env,
            cwd="/",
            capture_output=True,
            text=True,
            timeout=_IMPORT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"import check did not run: {exc}"
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
    return proc.returncode == 0, " | ".join(tail)


# ---------------------------------------------------------------------------
# Install + decide
# ---------------------------------------------------------------------------


def _stored_manifest_matches(version_dir: Path, pointer: Pointer) -> bool:
    raw = _read_nofollow(version_dir / MANIFEST_FILE, MAX_MANIFEST_BYTES)
    return raw is not None and hashlib.sha256(raw).hexdigest() == pointer.manifest_sha256


def install(
    *,
    s3_client: Any,
    bucket: str,
    store: Path,
    spec: BundleSpec,
    pointer: Pointer,
    import_check: ImportCheck,
) -> Path:
    """Verify and install ``pointer.version`` into ``store/<version>``."""
    raw = fetch(s3_client, bucket, pointer.manifest_key, MAX_MANIFEST_BYTES)
    if hashlib.sha256(raw).hexdigest() != pointer.manifest_sha256:
        raise BundleError("manifest bytes do not match pointer.manifest_sha256")
    manifest = parse_bundle_manifest(raw, name=spec.name, expected_version=pointer.version)
    token = secrets.token_hex(4)
    staging = store / f".staging-{pointer.version}-{token}"
    tarball = store / f".staging-{pointer.version}-{token}.tar.zst"
    final = store / pointer.version
    try:
        download(s3_client, bucket, tarball_key(spec.name, pointer.version), tarball, manifest)
        os.mkdir(staging, 0o755)
        _harden(staging, 0o755)
        extract(tarball, staging, spec)
        missing = [rel for rel in spec.required if not os.path.lexists(staging / rel)]
        if missing:
            raise BundleError(f"bundle lacks {missing}")
        ok, detail = import_check(staging / spec.import_check)
        if not ok:
            raise BundleError(f"import check failed: {detail}")
        _write_new(staging / MANIFEST_FILE, raw, 0o644)
        # VERSION last: a version dir without it never passed its import check.
        _write_new(staging / VERSION_FILE, f"{pointer.version}\n".encode(), 0o644)
        if os.path.lexists(final):
            aside = store / f".old-{pointer.version}-{token}"
            os.rename(final, aside)
            _remove(aside)
        os.rename(staging, final)
    finally:
        for leftover in (tarball, staging):
            try:
                _remove(leftover)
            except OSError:
                logger.warning("bundle_installer: could not remove %s", leftover)
    return final


def _on_disk(store: Path, spec: BundleSpec, owner_uid: int, why: str) -> Decision:
    target = _current_target(store)
    if not target or read_version_file(store / target) != target:
        return _image(f"{why}; no installed bundle on disk")
    try:
        verify_tree(store / target, spec, owner_uid)
    except (BundleError, OSError) as exc:
        return _image(f"{why}; on-disk {target} refused: {exc}")
    logger.warning("bundle_installer: %s; mounting on-disk %s", why, target)
    return Decision("on-disk", target, store / target, (why,))


def _read_pointer(s3_client: Any, bucket: str, slug: str, spec: BundleSpec) -> Pointer:
    data = fetch(s3_client, bucket, pointer_key(slug, spec.name), MAX_POINTER_BYTES)
    return parse_bundle_pointer(data, spec.name)


def _drop_current(store: Path) -> None:
    link = store / CURRENT
    if os.path.islink(link):
        link.unlink()


def decide(
    *,
    s3_client: Any,
    bucket: str,
    slug: str,
    spec: BundleSpec,
    home: str | Path = DEFAULT_HOME,
    import_check: ImportCheck = run_import_check,
    owner_uid: int = 0,
) -> Decision:
    """Install what the pointer names if needed; say what to mount. Never raises."""
    try:
        store = ensure_store(home, spec.name, owner_uid)
    except (BundleError, OSError) as exc:
        return _image(f"bundle store refused: {exc}")
    if s3_client is None:
        return _on_disk(store, spec, owner_uid, "R2 is not configured on this seat")
    try:
        pointer = _read_pointer(s3_client, bucket, slug, spec)
    except PointerMissing:
        _drop_current(store)
        return _image(f"no {spec.name} bundle pointer for {slug}")
    except ManifestError as exc:
        return _on_disk(store, spec, owner_uid, "; ".join(exc.reasons) or "bundle pointer refused")
    except SkillsApplyError as exc:
        return _on_disk(store, spec, owner_uid, f"R2 unreachable ({exc})")
    previous = _current_target(store)
    final = store / pointer.version
    source = "unchanged" if previous == pointer.version else "installed"
    try:
        complete = read_version_file(final) == pointer.version
        if not (complete and _stored_manifest_matches(final, pointer)):
            source = "installed"
            install(
                s3_client=s3_client,
                bucket=bucket,
                store=store,
                spec=spec,
                pointer=pointer,
                import_check=import_check,
            )
        verify_tree(final, spec, owner_uid)
        commit(store, pointer.version)
    except (BundleError, ManifestError, SkillsApplyError, OSError) as exc:
        reasons = exc.reasons if isinstance(exc, ManifestError) else (str(exc),)
        why = "; ".join((f"{spec.name} {pointer.version} refused", *reasons))
        # The version that ran last boot, not the image: the image's copy is a
        # seed frozen at its last build, and runner changes no longer build one.
        return _on_disk(store, spec, owner_uid, why)
    keep = {pointer.version} | ({previous} if previous and valid_version(previous) else set())
    prune(store, keep)
    return Decision(source, pointer.version, final)


__all__ = [
    "DEFAULT_HOME",
    "DEFAULT_JOBS_RUNTIME",
    "BundleError",
    "Decision",
    "decide",
    "download",
    "ensure_store",
    "extract",
    "install",
    "member_filter",
    "run_import_check",
    "verify_tree",
]

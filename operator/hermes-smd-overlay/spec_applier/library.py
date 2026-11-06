"""Root-owned install of a firm's accepted library files and their ledger.

The library sibling of ``spec_applier.applier`` and ``spec_applier.preferences``:
pull, parse, hash-verify, install root-owned, commit the root-computed manifest,
prune. Same fail-static rule, same hardening walk, a separate module so the
spec path stays as it is.

THE SOURCE OBJECTS, a key space of their own:

    vaults/<slug>/library/accepted.json            the ledger (library_ledger.py)
    vaults/<slug>/library/accepted.previous.json   one generation back, never installed
    vaults/<slug>/library/files/<sha256>.docx      each accepted file's bytes

written by the root establishment intake (``establish_intake/library_accept.py``)
and by nothing else. WHY NOT A FIELD OF ``output-classes.json``: that object has
three writers that rebuild it from the fields they know (the intake's
``_put_and_converge``, the portal, the proving-seat retire script), so a ledger
inside it would be one unrelated voice edit away from being dropped, and a
dropped ledger is a pruned seat. A Word file is 20 to 90 KB of binary that JSON
would carry only as base64, and a firm can accept a file before it has any voice
spec at all, while a missing ``output-classes.json`` stops the spec apply before
it starts. One key per copy, named by its sha256, is immutable: a copy is written
once and never rewritten.

ON THE SEAT, beside ``layout.json`` in the root-owned spec store:

    <SMD_SPEC_DIR>/library/accepted.json           the ledger, the vault bytes verbatim
    <SMD_SPEC_DIR>/library/files/<sha256>.docx     each copy an entry names

root-owned, files 0644 and dirs 0755 so the jobs runner's user can read them,
and recorded in ``manifest.json``: ``library_ledger`` (the ledger's digest, which
``library_ledger.read_installed`` verifies), ``library_files`` (each copy's
digest) and ``library_source_digest`` (the change key, which the intake's
converge-wait polls). The spec install rewrites ``manifest.json`` and carries
these keys forward (:func:`carried_keys`).

FAIL-STATIC. A copy the vault does not hold, or whose bytes do not hash to its
name, refuses the whole cycle: nothing is installed and the previous library
stands. A manifest that is present and cannot be read refuses too: rewriting it
would turn "cannot look" into "nothing installed" for every spec reader.

GONE MEANS GONE. A copy no entry names is pruned. A ledger removed from the vault
(``spec_applier.library_purge``) removes the installed ledger and every copy. A
vault that cannot be read removes nothing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_applier.applier import atomic_write
from shared import library_ledger
from shared.ids import sha256
from spec_applier.applier import (
    _DIR_MODE,
    _FILE_MODE,
    MANIFEST_NAME,
    SCHEMA_VERSION,
    SpecApplyError,
    SpecApplyOutcome,
    SpecApplyResult,
    _harden,
    _harden_ancestors,
    _is_missing_object,
)

logger = logging.getLogger(__name__)

LEDGER_REL = library_ledger.LEDGER_REL
COPIES_REL = "library/files"
SUFFIX = ".docx"
#: Manifest keys this module owns.
LEDGER_KEY = library_ledger.MANIFEST_KEY
FILES_KEY = "library_files"
SOURCE_KEY = "library_source_digest"
OWNED_KEYS = (LEDGER_KEY, FILES_KEY, SOURCE_KEY)

#: A Word form is tens of KB; past this it is a mistake, refused, never cut.
MAX_COPY_BYTES = 16 * 1024 * 1024
_SLUG = re.compile(r"\A[a-z0-9][a-z0-9-]{0,63}\Z")


def library_prefix(slug: str) -> str:
    """``vaults/<slug>/library/``. A slug outside ``[a-z0-9-]`` is refused, so no
    call can address the vault root or another firm's prefix."""
    if not isinstance(slug, str) or not _SLUG.match(slug):
        raise SpecApplyError(f"library_prefix: not a customer slug: {slug!r}")
    return f"vaults/{slug}/library/"


def ledger_key(slug: str) -> str:
    return f"{library_prefix(slug)}accepted.json"


def previous_ledger_key(slug: str) -> str:
    return f"{library_prefix(slug)}accepted.previous.json"


def copy_rel(digest: str) -> str:
    return f"{COPIES_REL}/{digest}{SUFFIX}"


def copy_key(slug: str, digest: str) -> str:
    return f"{library_prefix(slug)}files/{digest}{SUFFIX}"


@dataclass(frozen=True)
class _Pulled:
    ledger_raw: bytes
    copies: dict[str, bytes]


# ---- R2 -----------------------------------------------------------------------------


def get_object(s3_client: Any, bucket: str, key: str) -> bytes | None:
    """The object's bytes, ``None`` when it does not exist; raises on a fault."""
    try:
        response = s3_client.get_object(Bucket=bucket, Key=key)
    except Exception as exc:  # noqa: BLE001 (classified, then wrapped)
        if _is_missing_object(exc):
            return None
        raise SpecApplyError(f"library: could not read s3://{bucket}/{key}: {exc}") from exc
    body = response.get("Body") if isinstance(response, dict) else getattr(response, "Body", None)
    if body is None:
        raise SpecApplyError(f"library: response for {key} has no Body")
    data = body.read()
    if not isinstance(data, (bytes, bytearray)):
        raise SpecApplyError(f"library: body of {key} is not bytes")
    return bytes(data)


def library_signature(s3_client: Any, bucket: str, slug: str) -> str | None:
    """Every object under the library prefix with its ETag, or ``None`` on a fault.

    The poll loop's change key: a new copy changes it as surely as a new ledger,
    so a cycle refused for a copy not yet uploaded retries once the copy lands.
    """
    prefix = library_prefix(slug)
    parts: list[str] = []
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        try:
            page = s3_client.list_objects_v2(**kwargs)
        except Exception as exc:  # noqa: BLE001 (a list fault skips the tick)
            logger.debug("spec_applier(library): list failed (%s); skipping tick", exc)
            return None
        if not isinstance(page, dict):
            return None
        for entry in page.get("Contents") or []:
            if isinstance(entry, dict) and isinstance(entry.get("Key"), str):
                parts.append(f"{entry['Key']}:{entry.get('ETag')}")
        token = page.get("NextContinuationToken") if page.get("IsTruncated") else None
        if not token:
            break
    return "|".join(sorted(parts))


def _pull_copies(
    s3_client: Any, bucket: str, slug: str, ledger: Any
) -> tuple[dict[str, bytes], list[str]]:
    """Each copy the ledger's entries name, verified against its name BEFORE install."""
    copies: dict[str, bytes] = {}
    errors: list[str] = []
    for digest in sorted({e.sha256 for e in ledger.entries}):
        key = copy_key(slug, digest)
        data = get_object(s3_client, bucket, key)
        if data is None:
            errors.append(
                f"the ledger names {copy_rel(digest)} and the vault holds no copy at {key}"
            )
        elif len(data) > MAX_COPY_BYTES:
            errors.append(
                f"{copy_rel(digest)} is {len(data)} bytes, over the {MAX_COPY_BYTES}-byte ceiling"
            )
        elif hashlib.sha256(data).hexdigest() != digest:
            errors.append(
                f"{copy_rel(digest)}: the vault's bytes do not hash to the name the ledger gives them"
            )
        else:
            copies[digest] = data
    return copies, errors


# ---- the installed manifest -----------------------------------------------------------


def read_manifest(spec_dir: Path) -> dict[str, Any] | None:
    """The installed manifest, ``None`` when there is none; raises when it cannot be read.

    Unlike the spec install's best-effort read, an unreadable manifest is not
    treated as absent here: this module rewrites the manifest, and rewriting an
    unreadable one would replace "cannot look" with "nothing installed".
    """
    path = spec_dir / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SpecApplyError(
            f"library: the installed manifest cannot be read ({type(exc).__name__})"
        ) from exc
    if not isinstance(raw, dict):
        raise SpecApplyError("library: the installed manifest is not an object")
    return raw


def carried_keys(manifest: dict[str, Any]) -> dict[str, Any]:
    """The library's keys of an installed manifest, for the spec install to keep."""
    return {k: manifest[k] for k in OWNED_KEYS if k in manifest}


def _write_manifest(
    spec_dir: Path, manifest: dict[str, Any] | None, slug: str, library: dict[str, Any]
) -> None:
    doc = {k: v for k, v in (manifest or {}).items() if k not in OWNED_KEYS}
    doc.setdefault("schema_version", SCHEMA_VERSION)
    doc.setdefault("customer", slug)
    # A manifest without ``specs`` reads as UNREADABLE to every spec reader
    # (``shared/spec_manifest.py`` ``manifest_state``); a library-only seat
    # must read as "no spec installed", which is what an empty map says.
    if not isinstance(doc.get("specs"), dict):
        doc["specs"] = {}
    doc.update(library)
    blob = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path = spec_dir / MANIFEST_NAME
    atomic_write(path, blob)
    _harden(path, _FILE_MODE)


# ---- install and prune -----------------------------------------------------------------


def _write(spec_dir: Path, rel: str, data: bytes) -> dict[str, Any]:
    target = spec_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    _harden_ancestors(spec_dir, target.parent)
    atomic_write(target, data)
    _harden(target, _FILE_MODE)
    return {"sha256": sha256(target.read_bytes()), "bytes": len(data)}


def _prune(spec_dir: Path, keep: set[str]) -> tuple[str, ...]:
    """Remove copies the manifest no longer names. Scoped to ``library/files/*.docx``."""
    root = spec_dir / COPIES_REL
    if not root.is_dir():
        return ()
    pruned: list[str] = []
    for path in sorted(root.glob(f"*{SUFFIX}")):
        rel = path.relative_to(spec_dir).as_posix()
        if rel in keep:
            continue
        try:
            path.unlink()
            pruned.append(rel)
        except OSError as exc:
            logger.warning("spec_applier(library): could not prune %s: %s", path, exc)
    return tuple(pruned)


def _install(
    spec_dir: Path, pulled: _Pulled, digest: str, manifest: dict | None, slug: str
) -> SpecApplyResult:
    """Copies first, the ledger second, the manifest third (the commit), prune last.

    A reader between the ledger and the manifest finds the ledger's digest
    disagreeing with the manifest and reads UNUSABLE: the safe direction.
    """
    spec_dir.mkdir(parents=True, exist_ok=True)
    _harden(spec_dir, _DIR_MODE)
    files = {
        copy_rel(d): _write(spec_dir, copy_rel(d), data)
        for d, data in sorted(pulled.copies.items())
    }
    ledger_entry = _write(spec_dir, LEDGER_REL, pulled.ledger_raw)
    _write_manifest(
        spec_dir,
        manifest,
        slug,
        {LEDGER_KEY: {LEDGER_REL: ledger_entry}, FILES_KEY: files, SOURCE_KEY: digest},
    )
    pruned = _prune(spec_dir, set(files))
    installed = (*files, LEDGER_REL)
    return SpecApplyResult(
        outcome=SpecApplyOutcome.APPLIED,
        installed=tuple(installed),
        pruned=pruned,
        source_digest=digest,
        metadata={"copies": len(files)},
    )


def _remove_all(spec_dir: Path, manifest: dict | None, slug: str) -> SpecApplyResult:
    """No ledger in the vault: the manifest stops naming the library, then the files go."""
    ledger_path = spec_dir / LEDGER_REL
    has_files = (spec_dir / COPIES_REL).is_dir() and any((spec_dir / COPIES_REL).glob(f"*{SUFFIX}"))
    named = manifest is not None and any(k in manifest for k in OWNED_KEYS)
    if not (named or ledger_path.exists() or has_files):
        return SpecApplyResult(outcome=SpecApplyOutcome.UNCHANGED, source_digest=None)
    if manifest is not None and named:
        _write_manifest(spec_dir, manifest, slug, {})
    pruned = list(_prune(spec_dir, set()))
    if ledger_path.exists():
        ledger_path.unlink()
        pruned.append(LEDGER_REL)
    for empty in (spec_dir / COPIES_REL, spec_dir / "library"):
        try:
            empty.rmdir()
        except OSError:
            pass  # not empty (a stray file outside this module's scope) or already gone
    return SpecApplyResult(
        outcome=SpecApplyOutcome.APPLIED, pruned=tuple(pruned), metadata={"copies": 0}
    )


def apply_library(
    *, s3_client: Any, bucket: str, slug: str, spec_dir: str | Path
) -> SpecApplyResult:
    """Pull, verify and install ``slug``'s accepted-file ledger and every copy it names.

    APPLIED / REJECTED / UNCHANGED. Raises ``SpecApplyError`` on a vault fault, an
    unreadable installed manifest, or an install write fault; the installed
    library is then exactly as it was, and the caller retries next tick.
    """
    target = Path(spec_dir)
    raw = get_object(s3_client, bucket, ledger_key(slug))
    manifest = read_manifest(target)
    if raw is None:
        try:
            return _remove_all(target, manifest, slug)
        except OSError as exc:
            raise SpecApplyError(f"library: removing the installed library failed: {exc}") from exc
    digest = sha256(raw) or ""
    if manifest is not None and manifest.get(SOURCE_KEY) == digest:
        return SpecApplyResult(outcome=SpecApplyOutcome.UNCHANGED, source_digest=digest)
    try:
        ledger = library_ledger.parse_ledger(raw)
    except library_ledger.LedgerInvalid as exc:
        return SpecApplyResult(
            outcome=SpecApplyOutcome.REJECTED, reasons=tuple(exc.problems), source_digest=digest
        )
    copies, errors = _pull_copies(s3_client, bucket, slug, ledger)
    if errors:
        return SpecApplyResult(
            outcome=SpecApplyOutcome.REJECTED, reasons=tuple(errors), source_digest=digest
        )
    try:
        return _install(target, _Pulled(raw, copies), digest, manifest, slug)
    except OSError as exc:
        raise SpecApplyError(f"library: installing into {target} failed: {exc}") from exc


__all__ = [
    "COPIES_REL",
    "FILES_KEY",
    "LEDGER_KEY",
    "LEDGER_REL",
    "MAX_COPY_BYTES",
    "OWNED_KEYS",
    "SOURCE_KEY",
    "apply_library",
    "carried_keys",
    "copy_key",
    "copy_rel",
    "get_object",
    "ledger_key",
    "library_prefix",
    "library_signature",
    "previous_ledger_key",
    "read_manifest",
]

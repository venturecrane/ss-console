"""Root seat-spec verifier (ss-console Wave 2 W4), SHADOW MODE.

Runs inside the root ``config_applier`` poll loop. It reads the seat's spec
pointer ``vaults/<slug>/spec-pointer.json``, fetches the spec it names from
``fleet/specs/<slug>/<sha256>.json``, and judges it against the image this
process is running in:

* the bytes hash to the pointer's ``spec_sha256``;
* the pointer's ``key_id`` is the key baked into this image at
  ``/app/seat-spec.pub`` and its Ed25519 signature over those exact bytes
  verifies (``cryptography``, the Hermes venv's pin);
* the spec is for THIS seat;
* every requirement it records holds on this image: each ``skill:``/``pin:``/
  ``pre_run:`` in ``/app/image-manifest.json`` or the skills release the spec
  names, each capability need on this filesystem or in
  ``/app/image-capabilities.json``, and each lane schema module present at the
  path named with the digest named.

The verdict is written to ``/run/smd-spec/state.json`` (0644, tmp + rename) for
the gate's heartbeat (``shared/spec_check.py`` ships it as ``spec``) and the
``spec_unverified`` pager. SHADOW: nothing here changes what the seat applies;
``config_applier`` still applies ``customer.yaml`` exactly as before. The
cutover to applying only verified spec bytes is a later, separate change.

The ``customer_yaml_match`` field records whether the customer.yaml this seat
runs is the one the spec names, which is the evidence the cutover waits on.

Verdicts: ``verified``; ``absent`` (no pointer published); ``fetch_error`` (R2
could not be read: the previous verdict's spec is kept); and the failures
``malformed``, ``unknown_key``, ``bad_digest``, ``bad_signature``,
``wrong_seat``, ``spec_missing``, ``unmet_requirement``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_public_key,
)

from skills_applier.applier import PointerMissing, SkillsApplyError, fetch

logger = logging.getLogger(__name__)

STATE_PATH = Path("/run/smd-spec/state.json")
PUBKEY_PATH = Path("/app/seat-spec.pub")
IMAGE_MANIFEST_PATH = Path("/app/image-manifest.json")
IMAGE_CAPABILITIES_PATH = Path("/app/image-capabilities.json")
MAX_POINTER_BYTES = 64 * 1024
MAX_SPEC_BYTES = 1024 * 1024
MAX_SKILLS_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_UNMET = 32
#: Re-judge an unchanged pointer this often, so the state file stays fresh and
#: a requirement that stops holding (a lane module replaced) is noticed.
REVERIFY_SECONDS = 300

_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_KEY_ID = re.compile(r"\A[0-9a-f]{16}\Z")
_COMMIT = re.compile(r"\A[0-9a-f]{40}\Z")
_SKILLS_KEY = re.compile(r"\Afleet/skills/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json\Z")
_LANE_PATH = re.compile(r"\A/app/runners/[A-Za-z0-9_./-]+\.py\Z")
_UNMET_ID = re.compile(r"\A[A-Za-z0-9:@/._-]{1,200}\Z")
NEED_KINDS = ("paths", "tools", "verbs", "action_classes")


def pointer_key(slug: str) -> str:
    return f"vaults/{slug}/spec-pointer.json"


def key_id_of(public_key: Ed25519PublicKey) -> str:
    """First 16 hex of sha256 over the raw 32-byte public key (the compiler's rule)."""
    raw = public_key.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def load_public_key(path: Path = PUBKEY_PATH) -> Ed25519PublicKey | None:
    try:
        key = load_pem_public_key(path.read_bytes())
    except (OSError, ValueError) as exc:
        logger.warning("spec_verifier: cannot load %s (%s)", path, exc)
        return None
    return key if isinstance(key, Ed25519PublicKey) else None


@dataclass
class SpecVerdict:
    verdict: str
    checked_at: float
    spec_sha256: str | None = None
    key_id: str | None = None
    compiled_from: str | None = None
    unmet: list[str] = field(default_factory=list)
    customer_yaml_match: bool | None = None

    def to_json(self) -> dict[str, Any]:
        unmet = [u if _UNMET_ID.match(u) else "malformed-requirement" for u in self.unmet]
        return {
            "v": 1,
            "verdict": self.verdict,
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.checked_at)),
            "spec_sha256": self.spec_sha256,
            "key_id": self.key_id,
            "compiled_from": self.compiled_from,
            "unmet_count": len(unmet),
            "unmet": unmet[:MAX_UNMET],
            "customer_yaml_match": self.customer_yaml_match,
        }


# ---------------------------------------------------------------- requirements


def provided_by_image(manifest: Any) -> set[str] | None:
    """Port of scripts/lib/seat-spec.ts providedByImage."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        return None
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        return None
    out: set[str] = set()
    for name, body in skills.items():
        out.add(f"skill:{name}")
        if isinstance(body, dict):
            if isinstance(body.get("pin"), str):
                out.add(f"pin:{name}@{body['pin']}")
            for f in body.get("files") or []:
                out.add(f"pre_run:{name}/{f}")
    return out


def provided_by_skills(manifest: Any) -> set[str]:
    """Port of scripts/lib/seat-spec.ts providedBySkills."""
    out: set[str] = set()
    files = manifest.get("files") if isinstance(manifest, dict) else None
    for rel in files or {}:
        name, _, rest = str(rel).partition("/")
        if name and rest:
            out.update({f"skill:{name}", f"pre_run:{name}/{rest}"})
    pins = manifest.get("pins") if isinstance(manifest, dict) else None
    for name, pin in (pins or {}).items():
        if isinstance(pin, str):
            out.add(f"pin:{name}@{pin}")
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


@dataclass
class ImageView:
    """What this image provides: the files the requirements are checked against."""

    manifest_path: Path = IMAGE_MANIFEST_PATH
    capabilities_path: Path = IMAGE_CAPABILITIES_PATH
    path_exists: Callable[[str], bool] = os.path.exists
    read_bytes: Callable[[str], bytes | None] = field(default=lambda p: _read_file(p))


def _read_file(path: str) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(MAX_SPEC_BYTES)
    except OSError:
        return None


def _skills_provided(
    spec: dict, fetch_bytes: Callable[[str], bytes | None]
) -> tuple[set[str], list[str]]:
    ref = spec.get("skills")
    if ref is None:
        return set(), []
    key = ref.get("manifest_key") if isinstance(ref, dict) else None
    want = ref.get("manifest_sha256") if isinstance(ref, dict) else None
    if not isinstance(key, str) or not _SKILLS_KEY.match(key) or not isinstance(want, str):
        return set(), ["skills-ref-malformed"]
    data = fetch_bytes(key)
    if data is None:
        return set(), ["skills-manifest-missing"]
    if hashlib.sha256(data).hexdigest() != want:
        return set(), ["skills-manifest-mismatch"]
    try:
        return provided_by_skills(json.loads(data)), []
    except ValueError:
        return set(), ["skills-manifest-unreadable"]


def _unmet_capabilities(needs: Any, image: ImageView) -> list[str]:
    needs = needs if isinstance(needs, dict) else {}
    out = [f"path:{p}" for p in needs.get("paths") or [] if not image.path_exists(str(p))]
    caps = _read_json(image.capabilities_path)
    for kind in ("tools", "verbs", "action_classes"):
        wanted = [str(v) for v in needs.get(kind) or []]
        if not wanted:
            continue
        have = caps.get(kind) if isinstance(caps, dict) else None
        if not isinstance(have, (list, dict)):
            out.append("image-capabilities-unreadable")
            continue
        out.extend(
            f"{kind[:-1] if kind.endswith('s') else kind}:{w}" for w in wanted if w not in have
        )
    return out


def _unmet_lanes(lanes: Any, image: ImageView) -> list[str]:
    out: list[str] = []
    for lane, ref in sorted((lanes or {}).items()) if isinstance(lanes, dict) else []:
        path = ref.get("path") if isinstance(ref, dict) else None
        want = ref.get("sha256") if isinstance(ref, dict) else None
        if not isinstance(path, str) or not _LANE_PATH.match(path) or ".." in path:
            out.append(f"lane:{lane}")
            continue
        data = image.read_bytes(path)
        if data is None or hashlib.sha256(data).hexdigest() != want:
            out.append(f"lane:{lane}")
    return out


def unmet_requirements(
    spec: dict, image: ImageView, fetch_bytes: Callable[[str], bytes | None]
) -> list[str]:
    """Every requirement of ``spec`` this image does not meet, as stable ids."""
    requires = spec.get("requires") if isinstance(spec.get("requires"), dict) else {}
    have = provided_by_image(_read_json(image.manifest_path))
    unmet: list[str] = [] if have is not None else ["image-manifest-unreadable"]
    from_skills, skills_problems = _skills_provided(spec, fetch_bytes)
    unmet += skills_problems
    provided = (have or set()) | from_skills
    unmet += sorted(str(r) for r in requires.get("image") or [] if str(r) not in provided)
    unmet += _unmet_capabilities(requires.get("capabilities"), image)
    unmet += _unmet_lanes(requires.get("lane_schema_digests"), image)
    return unmet


# ---------------------------------------------------------------- the verdict


def _parse_pointer(data: bytes) -> dict | None:
    try:
        pointer = json.loads(data)
    except ValueError:
        return None
    if not isinstance(pointer, dict):
        return None
    sha, key_id, sig = pointer.get("spec_sha256"), pointer.get("key_id"), pointer.get("signature")
    ok = (
        isinstance(sha, str)
        and bool(_SHA256.match(sha))
        and isinstance(key_id, str)
        and bool(_KEY_ID.match(key_id))
        and isinstance(sig, str)
    )
    return pointer if ok else None


def _signature_ok(public_key: Ed25519PublicKey, signature: str, data: bytes) -> bool:
    try:
        public_key.verify(base64.b64decode(signature, validate=True), data)
    except (InvalidSignature, binascii.Error, ValueError):
        return False
    return True


def _authentic_spec(
    v: SpecVerdict,
    pointer: dict,
    slug: str,
    fetch_bytes: Callable[[str], bytes | None],
    public_key: Ed25519PublicKey | None,
) -> dict | None:
    """The spec the pointer names, if it is authentic; else None with ``v.verdict`` set."""
    if public_key is None or v.key_id != key_id_of(public_key):
        v.verdict = "unknown_key"
        return None
    data = fetch_bytes(f"fleet/specs/{slug}/{v.spec_sha256}.json")
    if data is None:
        v.verdict = "spec_missing"
        return None
    if hashlib.sha256(data).hexdigest() != v.spec_sha256:
        v.verdict = "bad_digest"
        return None
    if not _signature_ok(public_key, str(pointer["signature"]), data):
        v.verdict = "bad_signature"
        return None
    try:
        spec = json.loads(data)
    except ValueError:
        spec = None
    if not isinstance(spec, dict) or spec.get("schema_version") != 1:
        v.verdict = "malformed"
        return None
    commit = spec.get("compiled_from")
    v.compiled_from = commit if isinstance(commit, str) and _COMMIT.match(commit) else None
    if spec.get("slug") != slug:
        v.verdict = "wrong_seat"
        return None
    return spec


def judge(
    *,
    slug: str,
    pointer_bytes: bytes | None,
    fetch_bytes: Callable[[str], bytes | None],
    public_key: Ed25519PublicKey | None,
    image: ImageView,
    running_yaml: bytes | None,
    now: float,
) -> SpecVerdict:
    """The verdict for one pointer. ``fetch_bytes`` returns None on a 404 and
    raises on any other read failure (the caller maps that to ``fetch_error``)."""
    if pointer_bytes is None:
        return SpecVerdict("absent", now)
    pointer = _parse_pointer(pointer_bytes)
    if pointer is None:
        return SpecVerdict("malformed", now)
    v = SpecVerdict(
        "verified", now, spec_sha256=str(pointer["spec_sha256"]), key_id=str(pointer["key_id"])
    )
    spec = _authentic_spec(v, pointer, slug, fetch_bytes, public_key)
    if spec is None:
        return v
    named = spec.get("customer_yaml")
    if running_yaml is not None and isinstance(named, dict):
        v.customer_yaml_match = hashlib.sha256(running_yaml).hexdigest() == named.get("sha256")
    v.unmet = unmet_requirements(spec, image, fetch_bytes)
    if v.unmet:
        v.verdict = "unmet_requirement"
    return v


def write_state(verdict: SpecVerdict, path: Path = STATE_PATH) -> None:
    """``path`` 0644 by rename, in a 0755 directory: the gate (another uid) reads it."""
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    # mkdir's mode is masked by the umask and ignored for an existing directory,
    # so the mode is set explicitly: a narrower one would blind the heartbeat.
    # nosemgrep: python.lang.security.audit.insecure-file-permissions.insecure-file-permissions - root-owned 0755 so the hermes-uid gate's heartbeat can traverse it to read the root-written 0644 state.json; no uid but root can write here
    os.chmod(path.parent, 0o755)
    fd, tmp = tempfile.mkstemp(prefix=".state.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(verdict.to_json(), fh, sort_keys=True)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# ---------------------------------------------------------------- the loop step


@dataclass
class SpecVerifier:
    """One step per applier tick: HEAD the pointer; judge on a change or every
    ``REVERIFY_SECONDS``; write the state. Never raises into the applier."""

    s3_client: Any
    bucket: str
    slug: str
    running_yaml_path: Path
    state_path: Path = STATE_PATH
    image: ImageView = field(default_factory=ImageView)
    public_key_loader: Callable[[], Ed25519PublicKey | None] = load_public_key
    clock: Callable[[], float] = time.time
    _etag: str | None = None
    _judged_at: float = 0.0
    _last: SpecVerdict | None = None

    def _fetch(self, key: str, cap: int) -> bytes | None:
        try:
            return fetch(self.s3_client, self.bucket, key, cap)
        except PointerMissing:
            return None

    def _fetch_any(self, key: str) -> bytes | None:
        cap = MAX_SKILLS_MANIFEST_BYTES if key.startswith("fleet/skills/") else MAX_SPEC_BYTES
        return self._fetch(key, cap)

    def _pointer_etag(self) -> str:
        try:
            head = self.s3_client.head_object(Bucket=self.bucket, Key=pointer_key(self.slug))
        except Exception as exc:  # noqa: BLE001 -- absent and unreachable both re-judge on change
            return f"head-failed:{type(exc).__name__}"
        etag = head.get("ETag") if isinstance(head, dict) else None
        return etag if isinstance(etag, str) else "no-etag"

    def _fetch_error(self, now: float) -> SpecVerdict:
        last = self._last
        return SpecVerdict(
            "fetch_error",
            now,
            spec_sha256=last.spec_sha256 if last else None,
            key_id=last.key_id if last else None,
            compiled_from=last.compiled_from if last else None,
        )

    def _judge(self, now: float) -> SpecVerdict:
        try:
            pointer = self._fetch(pointer_key(self.slug), MAX_POINTER_BYTES)
            return judge(
                slug=self.slug,
                pointer_bytes=pointer,
                fetch_bytes=self._fetch_any,
                public_key=self.public_key_loader(),
                image=self.image,
                running_yaml=_read_file(str(self.running_yaml_path)),
                now=now,
            )
        except (SkillsApplyError, ValueError) as exc:
            logger.warning("spec_verifier: R2 read failed (%s); keeping the last spec", exc)
            return self._fetch_error(now)

    def step(self) -> SpecVerdict | None:
        """Judge and record when the pointer moved or the verdict is due; else None."""
        now = self.clock()
        etag = self._pointer_etag()
        if etag == self._etag and now - self._judged_at < REVERIFY_SECONDS:
            return None
        verdict = self._judge(now)
        self._etag, self._judged_at, self._last = etag, now, verdict
        try:
            write_state(verdict, self.state_path)
        except OSError as exc:
            logger.warning("spec_verifier: cannot write %s (%s)", self.state_path, exc)
        quiet = verdict.verdict in ("verified", "absent")
        logger.log(
            logging.INFO if quiet else logging.WARNING,
            "spec_verifier: %s spec=%s unmet=%s",
            verdict.verdict,
            verdict.spec_sha256,
            verdict.unmet[:5],
        )
        return verdict


__all__ = [
    "REVERIFY_SECONDS",
    "STATE_PATH",
    "ImageView",
    "SpecVerdict",
    "SpecVerifier",
    "judge",
    "key_id_of",
    "load_public_key",
    "unmet_requirements",
    "write_state",
]

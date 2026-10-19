"""R2 over the aws CLI: release artifacts, skills manifests, per-seat pointers.

Credentials are the operator-local R2_* set provision-customer.sh uses (run under
``infisical run --env=prod --path=/ss``); they reach the aws CLI as environment
on the child process only and are never printed.

Keys this module reads or writes (bucket R2_BUCKET_CONFIG, default
smd-customer-config):

  fleet/images/<digest-hex>/release.json            fleet build writes
  fleet/images/<digest-hex>/image-manifest.json     fleet build writes (from the image)
  fleet/images/<digest-hex>/image-capabilities.json fleet build writes (from the image)
  fleet/images/<digest-hex>/required-env.json       fleet build writes (from the image)
  fleet/images/<digest-hex>/lane-inputs.json        fleet build writes (same commit)
  fleet/skills/<version>.json                       CI writes (scripts/ci-publish-skills.sh)
  vaults/<slug>/skills-pointer.json                 ONLY `fleet release --skills` writes

The pointer key is assembled from a validated slug and checked against a
whole-string pattern before every write, the same belt as the CI config
publisher's assert_config_key.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import Any

from . import ids
from .runner import Runner, ToolingError

POINTER_KEY_RE = re.compile(r"^vaults/[a-z0-9][a-z0-9-]{0,38}[a-z0-9]/skills-pointer\.json$")
FLEET_KEY_RE = re.compile(r"^fleet/(images/[0-9a-f]{64}/[a-z-]+\.json|skills/[0-9]{12}-[0-9a-f]{8}\.json)$")


def image_key(digest: str, name: str) -> str:
    return f"fleet/images/{ids.digest_hex(digest)}/{name}"


def skills_manifest_key(version: str) -> str:
    return f"fleet/skills/{ids.skills_version(version)}.json"


def pointer_key(seat: str) -> str:
    return f"vaults/{ids.slug(seat)}/skills-pointer.json"


class R2:
    def __init__(self, runner: Runner, env: dict[str, str] | None = None) -> None:
        self.runner = runner
        src = env if env is not None else dict(os.environ)
        self.bucket = src.get("R2_BUCKET_CONFIG") or "smd-customer-config"
        self.endpoint = src.get("R2_ENDPOINT_URL", "")
        self._auth = {
            "AWS_ACCESS_KEY_ID": src.get("R2_ACCESS_KEY_ID", ""),
            "AWS_SECRET_ACCESS_KEY": src.get("R2_SECRET_ACCESS_KEY", ""),
        }

    def _require_creds(self) -> None:
        if not (self.endpoint and self._auth["AWS_ACCESS_KEY_ID"] and self._auth["AWS_SECRET_ACCESS_KEY"]):
            raise ToolingError(
                "R2 credentials missing (R2_ENDPOINT_URL / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY). "
                "Run under: infisical run --env=prod --path=/ss --silent -- operator/bin/fleet ..."
            )

    def _aws(self, *args: str, input_text: str | None = None) -> Any:
        self._require_creds()
        return self.runner.run(
            ["aws", *args, "--endpoint-url", self.endpoint], env=self._auth, input_text=input_text, timeout=120
        )

    def get_bytes(self, key: str) -> bytes | None:
        """The object's bytes, or None on a 404. Any other failure is a ToolingError."""
        with tempfile.TemporaryDirectory() as tmp:
            dest = os.path.join(tmp, "obj")
            res = self._aws("s3", "cp", f"s3://{self.bucket}/{key}", dest, "--only-show-errors")
            if not res.ok:
                if "404" in res.text or "does not exist" in res.text.lower() or "not found" in res.text.lower():
                    return None
                raise ToolingError(f"R2 read of {key} failed: {res.text.strip()[:300]}")
            with open(dest, "rb") as fh:
                return fh.read()

    def get_json(self, key: str) -> Any | None:
        raw = self.get_bytes(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ToolingError(f"R2 object {key} is not JSON") from exc

    def put_bytes(self, key: str, data: bytes, content_type: str = "application/json") -> None:
        if not (POINTER_KEY_RE.match(key) or FLEET_KEY_RE.match(key)):
            raise ValueError(f"refusing to write R2 key outside the fleet key spaces: {key}")
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "obj")
            with open(src, "wb") as fh:
                fh.write(data)
            res = self._aws(
                "s3", "cp", src, f"s3://{self.bucket}/{key}", "--only-show-errors", "--content-type", content_type
            )
            if not res.ok:
                raise ToolingError(f"R2 write of {key} failed: {res.text.strip()[:300]}")
        back = self.get_bytes(key)
        if back != data:
            raise ToolingError(f"R2 read-back of {key} does not match what was written")

    def put_json(self, key: str, value: Any) -> bytes:
        data = (json.dumps(value, indent=1, sort_keys=True) + "\n").encode()
        self.put_bytes(key, data)
        return data

    def delete(self, key: str) -> None:
        if not POINTER_KEY_RE.match(key):
            raise ValueError(f"refusing to delete R2 key outside the pointer key space: {key}")
        res = self._aws("s3", "rm", f"s3://{self.bucket}/{key}", "--only-show-errors")
        if not res.ok:
            raise ToolingError(f"R2 delete of {key} failed: {res.text.strip()[:300]}")

    def head(self, key: str) -> dict[str, Any] | None:
        res = self._aws("s3api", "head-object", "--bucket", self.bucket, "--key", key)
        if not res.ok:
            if "404" in res.text or "Not Found" in res.text:
                return None
            raise ToolingError(f"R2 head of {key} failed: {res.text.strip()[:300]}")
        try:
            return json.loads(res.stdout or "{}")
        except json.JSONDecodeError:
            return {}

    def exists_prefix(self, prefix: str) -> bool:
        res = self._aws("s3", "ls", f"s3://{self.bucket}/{prefix}")
        if res.ok:
            return bool(res.stdout.strip())
        if res.rc == 1 and not res.stderr.strip():
            return False  # `aws s3 ls` exits 1 with no output for an empty prefix
        raise ToolingError(f"R2 list of {prefix} failed: {res.text.strip()[:300]}")

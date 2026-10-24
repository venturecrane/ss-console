"""One verify-ledger row per judged seat: the pipeline proves its own release.

After a seat's rollout ends (done, rolled back, failed, tooling error), the
controller POSTs a ``live_state`` row to crane-context's verify ledger, the
same ledger ``crane_verify`` writes and the runtime-ac-proof merge gate reads
(``scripts/runtime-ac-proof.mjs``). The route is ``POST /verify``
(crane-console ``workers/crane-context/src/router.ts``), authenticated by the
``X-Relay-Key`` header against the same relay key CI already holds as
``CRANE_RELAY_KEY`` for ``GET /verify/lookup``. The body is the shape that
route's validator accepts (``endpoints/verify-ledger/validation.ts``): method,
tool_used, a claim of at most 300 characters, an output of at most 8 KiB, and
a ``command`` (required for ``live_state``) that re-reads the observation.

A write is evidence about the release, never part of it: any failure (no key in
a CI run, a network error, a non-201 answer, an answer with no id) becomes a
note in ``fleet_rollouts.detail`` and the rollout continues. The key travels in
one request header and is never logged or put in a message.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import urllib.parse
from typing import Any, Callable

from . import ids

LEDGER_BASE = "https://crane-context.automation-ab6.workers.dev"
VERIFY_ID_RE = re.compile(r"^vfy_[0-9A-HJKMNP-TV-Z]{26}$")
MAX_CLAIM_CHARS = 300
MAX_OUTPUT_BYTES = 8 * 1024
HEAD_TAIL_BYTES = 4 * 1024
SENTINEL = "\n...[truncated]...\n"

# (url, json body, headers) -> (HTTP status, parsed JSON body or None)
Post = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]


def _https_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    target = urllib.parse.urlsplit(url)
    if target.scheme != "https" or not target.hostname:
        raise ValueError("the verify ledger is reached over https only")
    conn = http.client.HTTPSConnection(target.hostname, timeout=30, context=ssl.create_default_context())  # nosemgrep: python.lang.security.audit.httpsconnection-detected.httpsconnection-detected - an explicit default context verifies certificates and hostnames  # fmt: skip
    try:
        conn.request(
            "POST", target.path, body=json.dumps(body), headers={**headers, "Content-Type": "application/json"}
        )
        resp = conn.getresponse()
        raw = resp.read()
    finally:
        conn.close()
    try:
        return resp.status, json.loads(raw or b"null")
    except json.JSONDecodeError:
        return resp.status, None


def head_tail(text: str) -> tuple[str, str]:
    """The output as the ledger takes it: whole, or first 4 KiB + sentinel + last 4 KiB."""
    data = text.encode("utf-8")
    if len(data) <= MAX_OUTPUT_BYTES:
        return text, "none"
    head = data[: HEAD_TAIL_BYTES - len(SENTINEL)].decode("utf-8", "ignore")
    tail = data[-HEAD_TAIL_BYTES:].decode("utf-8", "ignore")
    return head + SENTINEL + tail, "head_tail"


def readback_command(seat: str) -> str:
    """The command that re-reads what the judge read: the seat's heartbeat row."""
    return (
        "npx wrangler d1 execute ss-console-db --remote --json --command "  # noqa: S608 - a command line recorded in the ledger for re-running, never executed here; the slug is ids-validated
        f"\"SELECT last_heartbeat_ts, image_ref, version, selftest_json FROM fleet_status WHERE customer_slug = '{ids.slug(seat)}'\""
    )


class VerifyLedger:
    """Write one row per judged seat, or say in one line why none was written."""

    def __init__(self, key: str | None, *, required: bool = False, base: str = LEDGER_BASE,
                 post: Post | None = None, session: str | None = None) -> None:  # fmt: skip
        self._key = (key or "").strip() or None
        self.required = required
        self.base = base.rstrip("/")
        self.post = post or _https_post
        self.session = session

    @classmethod
    def from_environ(cls) -> "VerifyLedger":
        env = os.environ
        run = env.get("GITHUB_RUN_ID")
        return cls(
            env.get("CRANE_RELAY_KEY"),
            required=env.get("GITHUB_ACTIONS") == "true",
            session=f"gha-{env.get('GITHUB_WORKFLOW', 'fleet')}-{run}" if run else None,
        )

    def __repr__(self) -> str:
        return f"VerifyLedger(key={'set' if self._key else 'unset'}, base={self.base})"

    def body(self, seat: str, release_id: str, rollout_id: str, state: str, detail: str) -> dict[str, Any]:
        output, truncation = head_tail(f"rollout {rollout_id} state={state}\n{detail}")
        claim = f"{ids.slug(seat)} on {release_id}: rollout {rollout_id} ended {state}"[:MAX_CLAIM_CHARS]
        body: dict[str, Any] = {
            "method": "live_state",
            "tool_used": "wrangler",
            "source": "tool",
            "claim": claim,
            "output": output,
            "output_truncation": truncation,
            "command": readback_command(seat),
            "fresh_runtime": True,
            "venture": "ss",
            "repo": "venturecrane/ss-console",
            "files_touched": [],
        }
        if self.session:
            body["session_id"] = self.session
        return body

    def record(self, seat: str, release_id: str, rollout_id: str, state: str, detail: str) -> str:
        """``verify row: vfy_...``, ``verify row: not written (<why>)``, or "" (a laptop run with no key)."""
        if not self._key:
            return "verify row: not written (no CRANE_RELAY_KEY in this run)" if self.required else ""
        try:
            status, answer = self.post(
                f"{self.base}/verify",
                self.body(seat, release_id, rollout_id, state, detail),
                {"X-Relay-Key": self._key},
            )
        except (OSError, http.client.HTTPException, ValueError) as exc:
            return f"verify row: not written ({type(exc).__name__})"
        if status != 201:
            return f"verify row: not written (HTTP {status})"
        vid = ((answer or {}).get("verify") or {}).get("id") if isinstance(answer, dict) else None
        if not isinstance(vid, str) or not VERIFY_ID_RE.match(vid):
            return "verify row: not written (the ledger answered with no verify id)"
        return f"verify row: {vid}"

"""One Anthropic Messages call with no tools: the client two processes share.

WHO CALLS IT. The sealed web reader (``shared/sealed_reader.py``, in the
gateway) and the mailbox keeper (``workspace_broker/mailbox_keeper_model.py``,
in the workspace broker). They run in separate virtualenvs and import nothing
from one another, so this file is stamped into the broker's tree byte for byte
by ``operator/bin/gen-skill-shared.py`` (``operator/contracts/skill-shared-
modules.yaml``, module ``model_call``). Edit this file, never the copy.

WHAT IT OWNS. The request headers, the one POST, the response text, the
fractional cost of a response and a strict JSON reading of it. The KEY is
always passed in: the gateway reads it from its own environment, the broker
from the file the entrypoint materializes for it (``materialize_credential``
below; ``operator/templates/entrypoint-mailbox-keeper.sh``). Nothing here reads
a key from the environment except ``materialize_credential``, which runs once,
as root, at boot.

THE TRANSPORT is a parameter. The sealed reader passes ``safe_fetch``'s own
(address checks on every hop, one deadline), so its behaviour is unchanged; the
broker uses ``https_post`` below, which reaches one fixed host and nothing else.

Stdlib only: the broker venv carries no overlay package.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

API_HOST = "api.anthropic.com"
API_PATH = "/v1/messages"
API_VERSION = "2023-06-01"
KEY_ENV = "ANTHROPIC_API_KEY"
#: The overlay's pricing table, beside this file in the gateway's install.
PRICING_FILE = "anthropic_pricing.json"
MAX_RESPONSE_BYTES = 1_000_000

Post = Callable[[str, str, str, dict, bytes, float], Any]


class ModelCallError(RuntimeError):
    """The call could not be made, or its answer could not be read. The text is
    ours (a status code or a class name), never the vendor's body."""


def request_headers(key: str) -> dict[str, str]:
    return {"content-type": "application/json", "anthropic-version": API_VERSION, "x-api-key": key}


def https_post(
    host: str, method: str, path: str, headers: dict, body: bytes, deadline: float
) -> dict[str, Any]:
    """One HTTPS request to ``API_HOST`` only, inside ``deadline`` (monotonic)."""
    if host != API_HOST or not path.startswith("/v1/"):
        raise ModelCallError("refusing a request to any host but the Messages API")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ModelCallError("deadline passed before the call")
    request = urllib.request.Request(
        f"https://{API_HOST}{path}", data=body, method=method, headers=headers
    )
    try:
        # The URL is the fixed Messages API host, checked above.
        # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
        with urllib.request.urlopen(request, timeout=remaining) as response:  # noqa: S310 - https Messages host, checked above
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise ModelCallError(f"model call failed: HTTP {exc.code}") from exc
    except OSError as exc:
        raise ModelCallError(f"model call failed: {type(exc).__name__}") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ModelCallError("model answer too large")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise ModelCallError("model answer is not JSON") from exc
    if not isinstance(parsed, dict):
        raise ModelCallError("model answer is not an object")
    return parsed


def call(
    request: dict[str, Any], *, key: str, deadline: float, post: Post | None = None
) -> dict[str, Any]:
    """POST one Messages request. ``request`` must carry no ``tools`` key."""
    if not key:
        raise ModelCallError("no model key")
    if "tools" in request:
        raise ModelCallError("a no-tools call may not carry tools")
    send = post or https_post
    body = json.dumps(request).encode("utf-8")
    return send(API_HOST, "POST", API_PATH, request_headers(key), body, deadline)


def response_text(response: dict[str, Any]) -> str:
    parts = []
    for block in (response or {}).get("content") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text") or ""))
    return "".join(parts)


def usage_cents(response: dict[str, Any], rates: dict[str, Any]) -> float:
    """The response's cost in cents, unrounded (``rates``: cents per million)."""
    usage = (response or {}).get("usage") or {}
    in_tok = int(usage.get("input_tokens") or 0)
    out_tok = int(usage.get("output_tokens") or 0)
    return (
        in_tok * float(rates["input_per_million_cents"])
        + out_tok * float(rates["output_per_million_cents"])
    ) / 1_000_000


def parse_json_object(text: str) -> dict[str, Any] | None:
    """The answer as ONE JSON object, or None. Whitespace and a single ```json
    fence around it are allowed; any other text before or after is not."""
    body = (text or "").strip()
    if body.startswith("```"):
        first, _, rest = body.partition("\n")
        if first.strip() not in ("```", "```json") or not rest.rstrip().endswith("```"):
            return None
        body = rest.rstrip()[:-3].strip()
    if not (body.startswith("{") and body.endswith("}")):
        return None
    try:
        doc = json.loads(body)
    except ValueError:
        return None
    return doc if isinstance(doc, dict) else None


def pricing_table(path: Path | None = None) -> dict[str, Any]:
    """The models table of the pricing file (cents per million tokens)."""
    found = path or Path(__file__).with_name(PRICING_FILE)
    models = json.loads(found.read_text(encoding="utf-8")).get("models")
    return models if isinstance(models, dict) else {}


def materialize_credential(path: Path, *, environ: dict[str, str] | None = None) -> bool:
    """Write the seat's model key and the pricing table to ``path`` (0600).

    Run once at boot, as root, under the gateway's interpreter (where the
    pricing file is installed). False, and no file, when the key is unset."""
    key = ((environ if environ is not None else os.environ).get(KEY_ENV) or "").strip()
    if not key:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"key": key, "rates": pricing_table()}, handle)
    os.replace(tmp, path)
    return True


def load_credential(path: Path) -> tuple[str, dict[str, Any]]:
    """(key, models rate table) from a materialized file. Raises ModelCallError."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ModelCallError(f"no model credential: {type(exc).__name__}") from exc
    key = doc.get("key") if isinstance(doc, dict) else None
    rates = doc.get("rates") if isinstance(doc, dict) else None
    if not isinstance(key, str) or not key or not isinstance(rates, dict):
        raise ModelCallError("model credential file is malformed")
    return key, rates


__all__ = [
    "API_HOST",
    "API_PATH",
    "API_VERSION",
    "KEY_ENV",
    "ModelCallError",
    "call",
    "https_post",
    "load_credential",
    "materialize_credential",
    "parse_json_object",
    "pricing_table",
    "request_headers",
    "response_text",
    "usage_cents",
]

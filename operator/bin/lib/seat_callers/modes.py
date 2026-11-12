"""Mode labels, computed from recorded behaviour only.

Every label is a function of the run records the child wrote; none reads the
site's code. The vocabulary:

  <class>-on-<input>   what the site did on that input, where class is
                       returns-none | returns-false | returns-empty |
                       returns-value | returns-as-good | raises-<Type> |
                       exits-<code> | blocked-<kind> | timeout | crashed
  fails-open-on-<input>
      customer.yaml: returned a non-empty value from an input it could not
        read as configuration (empty, not a mapping, bad YAML, unreadable,
        a block of the wrong type), so its caller cannot tell a broken file
        from a configured seat.
      broker socket: returned exactly what it returns on a good reply, from a
        reply that was not good (or no broker at all).
      CUSTOMER_SLUG: returned a non-empty value with no usable slug in the
        environment (unset, empty, whitespace only).
  treats-malformed-as-absent   block-malformed returned what block-missing returned
  never-opens-customer-yaml    no input caused an open of the customer.yaml path
  no-unix-connect              no input caused an AF_UNIX connect
  wire-utf8 | wire-ascii-escaped | wire-empty   how the non-ASCII payload went out
  prefers-CUSTOMER_SLUG | prefers-alternate | strips-padding | keeps-padding
"""

from __future__ import annotations

from typing import Any

from .inputs import ALT_SLUG, BAD_INPUTS, GOOD_SLUG

SOCKET_FAILURES = ("not-ok", "empty-reply", "non-json", "fault-after-send", "no-socket", "refused")
NO_SLUG = ("unset", "empty", "whitespace")


def outcome_class(run: dict[str, Any], good: dict[str, Any] | None = None) -> str:
    o = run.get("outcome")
    if o == "return":
        if good is not None and good.get("outcome") == "return" and run.get("digest") == good.get("digest"):
            return "returns-as-good"
        shape = str(run.get("shape", ""))
        if shape == "none":
            return "returns-none"
        if shape == "false":
            return "returns-false"
        return "returns-empty" if shape.startswith("empty:") else "returns-value"
    if o == "raise":
        return f"raises-{run.get('type')}"
    if o == "exit":
        return f"exits-{run.get('code')}"
    if o == "blocked":
        return f"blocked-{run.get('kind')}"
    return str(o)


def _by_input(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["input"]: r for r in runs if not str(r["input"]).startswith("$")}


def _insensitive(runs: list[dict[str, Any]]) -> bool:
    """Every input that ran returned the same value: the site's result does not depend on its input."""
    ran = [r for r in runs if r.get("outcome") not in ("not-applicable", "not-executable")]
    return len(ran) > 1 and all(r.get("outcome") == "return" for r in ran) and len({r.get("digest") for r in ran}) == 1


def _yaml_modes(runs: dict[str, dict[str, Any]]) -> list[str]:
    good = runs.get("good")
    opens = any(r.get("reads") for r in runs.values())
    if not opens and _insensitive([r for r in runs.values() if not r.get("real")]):
        # Never opens the file and returns one value whatever it is handed: it cannot fail open on it.
        # (A site that DOES open the file and always returns its defaults is not this: it keeps its labels.)
        return ["input-insensitive", "never-opens-customer-yaml"]
    labels = []
    for name, run in runs.items():
        if name == "good" or run.get("outcome") in ("not-applicable", "not-executable"):
            continue
        if run.get("real"):
            cls = outcome_class(run)
            if cls.startswith(("raises-", "exits-", "blocked-", "timeout", "crashed")):
                labels.append(f"{cls}-on-real")
            continue
        cls = outcome_class(run, good)
        labels.append(f"{cls}-on-{name}")
        if name in BAD_INPUTS and cls in ("returns-value", "returns-as-good"):
            labels.append(f"fails-open-on-{name}")
    absent, malformed = runs.get("block-missing"), runs.get("block-malformed")
    if (
        absent
        and malformed
        and absent.get("outcome") == malformed.get("outcome") == "return"
        and absent.get("digest") == malformed.get("digest")
    ):
        labels.append("treats-malformed-as-absent")
    if not any(r.get("reads") for r in runs.values()):
        labels.append("never-opens-customer-yaml")
    return labels


def _wire_label(run: dict[str, Any] | None) -> str | None:
    if not run or not run.get("wire"):
        return "wire-empty" if run else None
    text = "".join(w["bytes"] for w in run["wire"])
    if "é" in text:
        return "wire-utf8"
    if "\\u00e9" in text:
        return "wire-ascii-escaped"
    return "wire-other"


def _socket_modes(runs: dict[str, dict[str, Any]]) -> list[str]:
    ok = runs.get("ok/ascii")
    labels = [lbl for lbl in [_wire_label(runs.get("ok/non-ascii"))] if lbl]
    for scenario in SOCKET_FAILURES:
        run = runs.get(f"{scenario}/ascii")
        if not run:
            continue
        cls = outcome_class(run, ok)
        labels.append(f"{cls}-on-{scenario}")
        reached = bool((ok or {}).get("unix_connects"))
        if cls == "returns-as-good" and reached and str((ok or {}).get("shape", "")).startswith("value:"):
            labels.append(f"fails-open-on-{scenario}")
    if not any(r.get("unix_connects") for r in runs.values()):
        labels.append("no-unix-connect")
    return labels


def _slug_modes(runs: dict[str, dict[str, Any]]) -> list[str]:
    labels = []
    if _insensitive(list(runs.values())):
        return ["input-insensitive"]
    for state, run in runs.items():
        cls = outcome_class(run)
        labels.append(f"{cls}-on-{state}")
        if state in NO_SLUG and cls == "returns-value":
            labels.append(f"fails-open-on-{state}")
    both = runs.get("both", {}).get("mentions")
    if both == [GOOD_SLUG]:
        labels.append("prefers-CUSTOMER_SLUG")
    elif both == [ALT_SLUG]:
        labels.append("prefers-alternate")
    padded = runs.get("padded", {})
    if padded.get("value") == GOOD_SLUG:
        labels.append("strips-padding")
    elif isinstance(padded.get("value"), str) and padded.get("value") != GOOD_SLUG:
        labels.append("keeps-padding")
    return labels


def modes(concept: str, runs: list[dict[str, Any]]) -> list[str]:
    table = _by_input(runs)
    derive = {"customer-yaml": _yaml_modes, "broker-socket": _socket_modes, "slug": _slug_modes}[concept]
    return sorted(set(derive(table)))


def fails_open(labels: list[str]) -> list[str]:
    return [lbl for lbl in labels if lbl.startswith("fails-open-on-")]

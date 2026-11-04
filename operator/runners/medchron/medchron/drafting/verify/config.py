"""The source check's knobs (``drafting-firm.yaml`` ``verify:``) and its price.

The section is OPTIONAL and every key in it is optional: absent, the check
runs on the classes in ``DEFAULT_CLASSES`` (the mediation brief, 2026-11-04:
the brief whose hand verification found 23 wrong items that every later stage
passed) with the defaults below. ``enabled: false`` turns it off for the firm.

    verify:
      enabled:     bool       default true
      classes:     [class]    default [mediation_brief]
      max_loops:   int 0..4   default 2   repair-and-recheck loops
      model:       str        default models.review, else models.audit
      parallelism: int 1..16  default 4   checker calls in flight
      authorities: path       default none: a directory under the inputs root
                              whose PINNED files are primary legal texts
                              (opinions, statutes, jury instructions), one
                              authority per file, named for it

An authority sentence is checked only against primary text: a file in the
matter, or an entry in ``authorities``. With neither, it is "could not verify",
never confirmed from what a model remembers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

KEY = "verify"
DEFAULT_CLASSES = ("mediation_brief",)
DEFAULT_LOOPS = 2
DEFAULT_PARALLELISM = 4
#: (key, kind, low, high) for the int keys; the closed key set.
KEYS = {
    "enabled": "bool",
    "classes": "list[str]",
    "max_loops": "int",
    "model": "str",
    "parallelism": "int",
    "authorities": "str",
}
RANGES = {"max_loops": (0, 4), "parallelism": (1, 16)}
#: The brief the preflight estimate prices before any draft exists: about the
#: size of the brief the method was measured on (8,784 words, 2026-11-04).
NOMINAL_WORDS = 9_000


@dataclass(frozen=True)
class Settings:
    enabled: bool
    max_loops: int
    model: str
    parallelism: int
    authorities: str | None


def validate(body: Any, classes: tuple[str, ...], inputs: dict[str, Any] | None) -> list[str]:
    """Every problem in the ``verify`` section as ``verify.key: message``."""
    if not isinstance(body, dict):
        return [f"{KEY}: expected a map"]
    out = [f"{KEY}.{k}: unknown key (closed key set)" for k in body if k not in KEYS]
    for k, v in body.items():
        kind = KEYS.get(k)
        if kind == "bool" and not isinstance(v, bool):
            out.append(f"{KEY}.{k}: expected bool")
        elif kind == "int":
            lo, hi = RANGES[k]
            if not (isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi):
                out.append(f"{KEY}.{k}: expected an integer {lo}..{hi}")
        elif kind == "str" and not (isinstance(v, str) and v.strip()):
            out.append(f"{KEY}.{k}: expected str")
        elif kind == "list[str]":
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                out.append(f"{KEY}.{k}: expected a list of class names")
            else:
                out += [f"{KEY}.classes: unknown class {x!r} (expected {list(classes)})" for x in v if x not in classes]
    auth = body.get("authorities")
    if isinstance(auth, str) and auth.strip():
        if auth.startswith("/") or ".." in auth.split("/"):
            out.append(f"{KEY}.authorities: must be a directory relative to the inputs directory")
        elif inputs is not None and not authority_paths(auth, inputs):
            out.append(f"{KEY}.authorities: no pinned file under {auth}/ in inputs")
    return out


def authority_paths(directory: str, inputs: dict[str, Any]) -> list[str]:
    """The pinned files of the authority store: inputs entries under it."""
    pre = directory.rstrip("/") + "/"
    return sorted(p for p in inputs if isinstance(p, str) and p.startswith(pre))


def settings(firm_data: dict[str, Any], cls: str) -> Settings:
    body = firm_data.get(KEY) or {}
    classes = tuple(body.get("classes") or DEFAULT_CLASSES)
    models = firm_data["models"]
    model = str(body.get("model") or models.get("review") or models["audit"])
    return Settings(
        enabled=bool(body.get("enabled", True)) and cls in classes,
        max_loops=int(body.get("max_loops", DEFAULT_LOOPS)),
        model=model,
        parallelism=int(body.get("parallelism", DEFAULT_PARALLELISM)),
        authorities=str(body["authorities"]) if body.get("authorities") else None,
    )


# ---- the price -----------------------------------------------------------------------
#: Token shape of one check of a brief, per the design in ``check.py``: no
#: cache credit (conservative). Measured inputs: 5.6 items per 100 words
#: (2026-11-04 hand run, 489 items over 8,784 words); the rest are this
#: design's bounds (``sources.EVIDENCE_CHARS`` and friends).
WORDS_PER_PART = 1_100
TOKENS_PER_WORD = 1.35
ITEMS_PER_WORD = 0.056
INSTRUCTION_TOKENS = 3_000
ORIENTATION_TOKENS = 8_000
EVIDENCE_TOKENS = 60_000
FLAG_RATE = 0.06  # items sent to the second read
IMAGE_RATE = 0.05  # items whose page is read as an image
REPAIR_IN, REPAIR_OUT = 46_000, 6_000
RECHECK_SHARE = 0.3  # of a full round, over every loop together


def projected_tokens(words: int, loops: int) -> tuple[int, int]:
    """(input, output) tokens for one check of a ``words``-word document."""
    parts = max(1, math.ceil(words / WORDS_PER_PART)) + 1  # the attorney notes are a part
    text = words * TOKENS_PER_WORD
    items = words * ITEMS_PER_WORD
    per_part = items / parts
    enum_in = parts * (INSTRUCTION_TOKENS + ORIENTATION_TOKENS + text / parts)
    enum_out = parts * (per_part * 40 + 1_500)
    grade_in = parts * (INSTRUCTION_TOKENS + EVIDENCE_TOKENS + text / parts)
    grade_out = parts * (per_part * 90 + 4_000)
    image_in, image_out = items * IMAGE_RATE * 2_500, items * IMAGE_RATE * 800
    adv_in, adv_out = text + INSTRUCTION_TOKENS + 30_000, 6_000
    adj_in, adj_out = items * FLAG_RATE * 8_000, items * FLAG_RATE * 1_000
    rep_in, rep_out = loops * REPAIR_IN, loops * REPAIR_OUT
    re_in = RECHECK_SHARE * (enum_in + grade_in) if loops else 0
    re_out = RECHECK_SHARE * (enum_out + grade_out) if loops else 0
    tin = enum_in + grade_in + image_in + adv_in + adj_in + rep_in + re_in
    tout = enum_out + grade_out + image_out + adv_out + adj_out + rep_out + re_out
    return int(tin), int(tout)


def projected_usd(words: int, loops: int, in_cents_per_m: float, out_cents_per_m: float) -> float:
    tin, tout = projected_tokens(words, loops)
    return round((tin * in_cents_per_m + tout * out_cents_per_m) / 1e6 / 100, 2)


def projected_for(pricing: Any, s: Settings, words: int = NOMINAL_WORDS) -> float:
    """The check's price at the run's own rate card (``budget.Pricing``)."""
    rate = pricing.rate_for(s.model)
    return projected_usd(words, s.max_loops, rate.input_per_million_cents, rate.output_per_million_cents)

"""Which seat this is, and the seat's customer.yaml as parsed YAML: one reader.

Every process on a seat (the gateway, the broker, the job runner, each
connector, each skill's pre_run.py, the operator tools) used to read the seat
slug from the environment and parse customer.yaml with its own copy of the same
two lines. This module is the one home for both; each process carries a
byte-identical copy, stamped by operator/bin/gen-skill-shared.py from
operator/contracts/skill-shared-modules.yaml. Edit this file (the source), then
run the generator; never edit a copy.

What a missing value does is the CALLER's choice, named at every call:
``on_missing`` has no default, so a reader that falls open on an unset slug or
an absent file says so in its own text. Every call site's behaviour is pinned by
operator/contracts/seat-config-callers.json, which runs each one over fixed
inputs.

Stdlib only at import time: PyYAML is imported when a document is parsed, so a
process that only asks which seat it is never needs it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, overload

#: The environment variable every seat process reads to learn which seat it is.
SLUG_VAR = "CUSTOMER_SLUG"
#: The name the gateway's plugins receive the slug under (the agent's own process).
ALT_SLUG_VAR = "SMD_CUSTOMER_SLUG"

#: ``on_missing`` for :func:`slug`: what an UNSET variable reads as.
#:   raise   KeyError, as ``os.environ[name]``
#:   none    None, as ``os.environ.get(name)``
#:   empty   "", as ``os.environ.get(name, "")``
SLUG_MISSING = ("raise", "none", "empty")
#: ``alternate`` for :func:`slug`: whether ``SMD_CUSTOMER_SLUG`` is consulted.
#:   none    never
#:   first   ``SMD_CUSTOMER_SLUG or <slug>``
#:   after   ``<slug> or SMD_CUSTOMER_SLUG``
SLUG_ALTERNATE = ("none", "first", "after")
#: ``on_missing`` for :func:`load`: what an ABSENT file (FileNotFoundError) reads as.
#:   raise   FileNotFoundError propagates
#:   none    None
#:   empty   {}
FILE_MISSING = ("raise", "none", "empty")


def _primary(on_missing: str) -> str | None:
    if on_missing == "raise":
        return os.environ[SLUG_VAR]
    if on_missing == "none":
        return os.environ.get(SLUG_VAR)
    if on_missing == "empty":
        return os.environ.get(SLUG_VAR, "")
    raise ValueError(f"on_missing must be one of {SLUG_MISSING}, not {on_missing!r}")


@overload
def slug(
    *,
    on_missing: Literal["raise", "empty"],
    alternate: Literal["none", "first"] = "none",
) -> str: ...
@overload
def slug(*, on_missing: str, alternate: str = "none") -> str | None: ...
def slug(*, on_missing: str, alternate: str = "none") -> str | None:
    """This seat's slug from the environment, exactly as read (no stripping).

    ``on_missing`` names what an unset variable reads as (:data:`SLUG_MISSING`);
    ``alternate`` names whether, and in which order, ``SMD_CUSTOMER_SLUG`` is
    consulted (:data:`SLUG_ALTERNATE`). Each is evaluated left to right with
    Python's ``or``, so an empty value falls through to the next exactly as the
    expression it replaces did.
    """
    if alternate == "none":
        return _primary(on_missing)
    if alternate == "first":
        return os.environ.get(ALT_SLUG_VAR) or _primary(on_missing)
    if alternate == "after":
        return _primary(on_missing) or os.environ.get(ALT_SLUG_VAR)
    raise ValueError(f"alternate must be one of {SLUG_ALTERNATE}, not {alternate!r}")


def parse(source: Any) -> Any:
    """A YAML document (text, bytes or an open stream) parsed safely; what
    ``yaml.safe_load`` returns or raises."""
    import yaml

    return yaml.safe_load(source)


def load(path: str | os.PathLike[str], *, encoding: str | None, on_missing: str) -> Any:
    """The YAML document at ``path`` (customer.yaml, or a file beside it), parsed.

    ``encoding`` is passed to the read as given (None is the locale's, as
    ``Path.read_text()`` with no argument). ``on_missing`` names what an absent
    file reads as (:data:`FILE_MISSING`); every other failure (a directory, a
    permission error, bytes that do not decode, YAML that does not parse)
    propagates as the exception the read or the parse raised.
    """
    if on_missing not in FILE_MISSING:
        raise ValueError(f"on_missing must be one of {FILE_MISSING}, not {on_missing!r}")
    try:
        text = Path(path).read_text(encoding=encoding)
    except FileNotFoundError:
        if on_missing == "raise":
            raise
        return None if on_missing == "none" else {}
    return parse(text)


__all__ = ["ALT_SLUG_VAR", "SLUG_VAR", "load", "parse", "slug"]

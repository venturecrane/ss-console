"""Walled-store directories that belong to nobody listed on the seat (stdlib only).

WHY (ss-console #3946). A store walled per person keeps each person's records in
a directory named by ``person_slug`` of their address. A removal that missed one
directory, or data written for a person who has since left, leaves a directory
no listed person owns: nobody can reach it through the tools, and nobody has
been told it is still there. The hourly job asks this module, and SMD's
``sender_enrollment_cli orphans`` prints the answer: per store, the slugs and
the count. Never anything inside a directory.

It reads the volume through the same no-follow helpers the purge uses
(``nofollow.py``): every directory is opened one component at a time with
``O_NOFOLLOW`` and a store that cannot be examined is a problem in the result,
never an absence. READ-ONLY.

A roster entry that grants a whole domain (``@firm.example``) cannot be turned
into a slug, so on a seat that authors one, a partition of a person at that
domain is reported here; the command line says so beside the count.
"""

from __future__ import annotations

import stat
from collections.abc import Iterable
from dataclasses import dataclass, field

from .nofollow import CannotExamine, Volume, look, scan, why
from .person_purge import PurgeTargets, normalize_person_address, person_slug


@dataclass
class Orphans:
    """``found``: store root to the slugs nobody listed owns. ``problems``: the
    stores (or the volume) that could not be examined, with why."""

    found: dict[str, list[str]] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not self.found and not self.problems


def _store(vol: Volume, root, slugs: set[str], out: Orphans) -> None:
    with vol.dir(root) as fd:
        if fd is None:
            return  # an absent store holds nobody's directory
        names = []
        for entry in scan(fd, root):
            if entry.name.startswith("."):
                continue
            st = look(entry.name, fd, root / entry.name)
            if st is not None and (stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode)):
                names.append(entry.name)
        orphans = sorted(n for n in names if n not in slugs)
        if orphans:
            out.found[str(root)] = orphans


def orphan_partitions(targets: PurgeTargets, listed: Iterable[str]) -> Orphans:
    """The directories in every walled store ``targets`` names whose slug
    belongs to no address in ``listed`` (the seat's people now: its authored
    roster's exact addresses, its administrators and its enrolled list)."""
    slugs = {person_slug(a) for a in listed if normalize_person_address(a) is not None}
    out = Orphans()
    if targets.config_error:
        out.problems.append(f"the walled stores could not be listed: {targets.config_error}")
        return out
    try:
        vol = Volume(targets.data_root)
    except CannotExamine as exc:
        out.problems.append(f"{exc.location}: {exc.why}")
        return out
    try:
        for root in targets.partition_roots:
            try:
                _store(vol, root, slugs, out)
            except CannotExamine as exc:
                out.problems.append(f"{exc.location}: {exc.why}")
            except OSError as exc:
                out.problems.append(f"{root}: {why(exc)}")
    finally:
        vol.close()
    return out


__all__ = ["Orphans", "orphan_partitions"]

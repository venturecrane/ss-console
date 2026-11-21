"""The official form interrogatory text, as data (``tools/forms/build_catalog.py`` makes it).

``load()`` returns the catalogue of one form revision: per interrogatory
number its section, the official text exactly as printed (whitespace
normalised, line-break hyphenation repaired), its lettered subparts, the
defined terms it uses, what it repeats over where the form's words say so,
and the page and rectangle of its checkbox. The data ships as a JSON file in
this package, beside this module (the wheel ships non-Python package files,
as the chronology lane's prompts and drug list show).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

DISC_001 = "DISC-001"
#: form id -> revision -> the catalogue file in this package.
_FILES: Mapping[str, Mapping[str, str]] = {DISC_001: {"Rev. January 1, 2024": "disc001-rev-2024-01-01.json"}}
#: The revision a served DISC-001 prints today.
CURRENT = "Rev. January 1, 2024"
REPEATS = frozenset({"provider", "employer", "person", "item", "vehicle", "none", "unknown"})


class CatalogueError(ValueError):
    """An unknown form or revision, or a number the form does not carry."""


def ws(text: str) -> str:
    """Whitespace normalised: the comparison every check makes."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Box:
    page: int  # 0-based page of the official form
    rect: tuple[float, float, float, float]  # the printed box, PDF points, origin top-left
    widget: tuple[float, float, float, float]  # the AcroForm checkbox inside it


@dataclass(frozen=True)
class Interrogatory:
    number: str
    section: str
    stem: str
    subparts: tuple[tuple[str, str], ...]
    text: str
    defined_terms: tuple[str, ...]
    repeats_over: str
    box: Box

    @property
    def letters(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.subparts)


@dataclass(frozen=True)
class Section:
    number: str
    title: str  # as the form's own table of contents prints it
    heading: str  # as printed above the section in the body


@dataclass(frozen=True)
class Catalogue:
    form: str
    revision: str
    sha256: str
    sections: tuple[Section, ...]
    interrogatories: tuple[Interrogatory, ...]
    definitions: tuple[tuple[str, str], ...]
    instructions_answering: tuple[tuple[str, str], ...]
    definition_boxes: Mapping[str, Box]
    page_size: tuple[float, float]

    @property
    def numbers(self) -> tuple[str, ...]:
        return tuple(i.number for i in self.interrogatories)

    def get(self, number: str) -> Interrogatory:
        for i in self.interrogatories:
            if i.number == number:
                return i
        raise CatalogueError(f"{number} is not an interrogatory on {self.form} [{self.revision}]")

    def has(self, number: str) -> bool:
        return number in self.numbers

    def position(self, number: str) -> int:
        """Form order: the index of ``number`` among the form's interrogatories."""
        try:
            return self.numbers.index(number)
        except ValueError:
            raise CatalogueError(f"{number} is not an interrogatory on {self.form} [{self.revision}]") from None

    def section_of(self, number: str) -> Section:
        sec = self.get(number).section
        return next(s for s in self.sections if s.number == sec)


def _box(raw: Mapping[str, Any]) -> Box:
    return Box(int(raw["page"]), tuple(float(v) for v in raw["rect"]), tuple(float(v) for v in raw["widget"]))  # type: ignore[arg-type]


def from_data(data: Mapping[str, Any]) -> Catalogue:
    items = tuple(
        Interrogatory(
            number=str(i["number"]),
            section=str(i["section"]),
            stem=str(i["stem"]),
            subparts=tuple((str(k), str(t)) for k, t in i["subparts"]),
            text=str(i["text"]),
            defined_terms=tuple(str(t) for t in i["defined_terms"]),
            repeats_over=str(i["repeats_over"]),
            box=_box(i["box"]),
        )
        for i in data["interrogatories"]
    )
    bad = [i.number for i in items if i.repeats_over not in REPEATS]
    if bad:
        raise CatalogueError(f"repeats_over not one of {sorted(REPEATS)}: {bad}")
    return Catalogue(
        form=str(data["form"]),
        revision=str(data["revision"]),
        sha256=str(data["source"]["sha256"]),
        sections=tuple(Section(str(s["number"]), str(s["title"]), str(s["heading"])) for s in data["sections"]),
        interrogatories=items,
        definitions=tuple((str(k), str(t)) for k, t in data["definitions"]),
        instructions_answering=tuple((str(k), str(t)) for k, t in data["instructions_answering"]),
        definition_boxes={str(k): _box(v) for k, v in data["definition_boxes"].items()},
        page_size=(float(data["page_size"][0]), float(data["page_size"][1])),
    )


def raw_data(form: str = DISC_001, revision: str = CURRENT) -> dict[str, Any]:
    name = _FILES.get(form, {}).get(revision)
    if name is None:
        raise CatalogueError(f"no catalogue for {form} [{revision}]")
    return json.loads((Path(__file__).resolve().parent / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=4)
def load(form: str = DISC_001, revision: str = CURRENT) -> Catalogue:
    return from_data(raw_data(form, revision))

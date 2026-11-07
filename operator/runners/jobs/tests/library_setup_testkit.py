"""The REAL objects the "set up our ..." piece reads, built from made-up data.

WHY THIS FILE (2026-11-07). The piece's first tests stubbed the drafting inputs
as ``SimpleNamespace(attorneys=lambda: ...)``, a method, where the real loader
(``drafting.firm.DraftingFirm``) has a PROPERTY. ``inputs.attorneys()`` passed
every test and raised TypeError on a firm's real inputs, at the client
workspace's $0 rehearsal. So nothing here is shaped like the real thing: each
helper returns the real thing.

* ``drafting_inputs``: a drafting inputs directory written to disk and loaded by
  ``drafting.firm.load``, the loader the job calls, with authored attorneys.
* ``switch``: ``logic_in_code.Switch``, the dataclass ``load_switch`` returns.
* ``customer_yaml``: a seat config the real ``load_library_config`` and
  ``load_firm_identity`` read (via ``SMD_CUSTOMER_YAML_PATH``).
* ``LibraryTenant``: a case system answering the exact calls the real
  ``SmokeballLibrary``, ``lookup_matter``, ``find_folder_id`` and
  ``list_matter_files`` make, in the shapes the pilot tenant was observed to
  return (``library.py``'s OBSERVED notes), and recording every write.

Every name, address and number is made up.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from drafting_testkit import make_inputs

LIB_MATTER = "00000000-0000-0000-0000-00000000a11b"
LIB_NUMBER = "LIB-EXAMPLE"
FOLDER = "00000000-0000-0000-0000-0000000f01de"
FIRM_IDENTITY = {
    "name": "Example & Example, LLP",
    "street": "One Example Way",
    "city_state_zip": "Exampletown, California 90000",
    "phone": "(000) 000-0000",
}


def drafting_inputs(root: Path, *emails: str) -> str:
    """A drafting inputs directory with these authored attorneys; its path."""
    blocks = [{"email": e, "block": [f"Attorney {i}, Esq.", FIRM_IDENTITY["name"]]} for i, e in enumerate(emails, 1)]
    make_inputs(root, **({"attorneys": blocks} if blocks else {}))
    return str(root)


def loaded(root: Path, *emails: str) -> Any:
    """The real ``DraftingFirm`` for those attorneys."""
    from jobs.drafting import firm as drafting_firm

    return drafting_firm.load(drafting_inputs(root, *emails))


def switch(mode: str = "enforce", error: str | None = None) -> Any:
    """``load_switch``'s own answer type, for injecting a switch state."""
    from smokeball_connector import logic_in_code

    sw = logic_in_code.Switch(logic_in_code.LIBRARY_SETUP, mode, error=error)
    return lambda: sw


def customer_yaml(path: Path, monkeypatch: Any) -> Path:
    """A seat config with the library location and the firm's identity."""
    doc = {
        "self_initiation": {"document_library": {"matter_number": LIB_NUMBER, "folder_name": "Document Library"}},
        "firm_identity": FIRM_IDENTITY,
    }
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    return path


class LibraryTenant:
    """The calls the real library reads make, answered; every write recorded.

    ``files``: file id -> (matter id, name without extension, bytes). Library
    files sit in ``FOLDER`` on ``LIB_MATTER``; any other matter is a client
    matter whose files the accept job downloads."""

    def __init__(self, files: dict[str, tuple[str, str, bytes]] | None = None) -> None:
        self.files = dict(files or {})
        self.version = {k: "v1" for k in self.files}
        self.writes: list[tuple[str, str]] = []

    def _entry(self, fid: str) -> dict[str, Any]:
        matter, name, _data = self.files[fid]
        out = {"id": fid, "name": name, "fileExtension": ".docx", "versionId": self.version[fid], "isDeleted": False}
        return {**out, "folder": {"id": FOLDER}} if matter == LIB_MATTER else out

    def get(self, path: str, **params: Any) -> Any:
        if path == "/matters":
            return {"value": [{"id": LIB_MATTER, "number": LIB_NUMBER}]}
        parts = path.strip("/").split("/")
        if parts[-1] == "folders":
            return {"value": [{"folders": [{"id": FOLDER, "name": "Document Library"}]}]}
        if parts[-1] == "files":
            return {"value": [self._entry(f) for f, (m, _n, _d) in self.files.items() if m == parts[1]]}
        if len(parts) == 5 and parts[2:4] == ["documents", "files"]:
            return self._entry(parts[4])
        raise AssertionError(f"the library reads never ask {path}")

    def download_file(self, matter_id: str, file_id: str) -> tuple[dict[str, Any], bytes]:
        return {}, self.files[file_id][2]

    def add_file(self, matter_id: str, file_name: str, data: bytes, *, folder_id: str | None = None) -> dict[str, Any]:
        assert (matter_id, folder_id) == (LIB_MATTER, FOLDER), (matter_id, folder_id)
        self.writes.append(("add_file", file_name))
        fid = f"new-{len(self.writes)}"
        self.files[fid], self.version[fid] = (LIB_MATTER, file_name.removesuffix(".docx"), data), "v1"
        return {"fileId": fid}

    def add_file_version(
        self, matter_id: str, file_id: str, data: bytes, *, expected_version: str, **_: Any
    ) -> dict[str, Any]:
        assert matter_id == LIB_MATTER and self.version[file_id] == expected_version
        self.writes.append(("add_file_version", file_id))
        m, name, _old = self.files[file_id]
        self.files[file_id], self.version[file_id] = (m, name, data), "v2"
        return {"versionBefore": expected_version}

    def sha(self, file_id: str) -> str:
        return hashlib.sha256(self.files[file_id][2]).hexdigest()


__all__ = ["FIRM_IDENTITY", "LIB_MATTER", "LibraryTenant", "customer_yaml", "drafting_inputs", "loaded", "switch"]

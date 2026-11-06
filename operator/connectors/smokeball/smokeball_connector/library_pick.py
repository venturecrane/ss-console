"""Which Word file a chat turn's document starts from: the chat tool's one
caller of the library resolver (design 2026-11-06, piece 2).

``render_docx_draft`` and ``render_docx_template`` with a ``document_class``
ask :func:`chat_base` first, before today's newest-by-name lookup
(``library.resolve_template``). What the tool then does, by the resolver's answer:

* ``none_accepted`` (every seat today): nothing changes. The tool runs today's
  lookup, by today's code in ``server._render_with_format``, and the record says
  which fallback it used (the library file by name, or the starter).
* ``resolved``: the accepted file's bytes are the base, in place of the lookup.
  The chat tool writes a document by clearing its base's body and keeping its
  page setup, header, footer and styles, which is the "styles" contract. An
  accepted file of another contract ("marked", "prototypes") holds its look in
  its body, which this tool would throw away, so it is refused, never used
  as though it were a styles file.
* ``cannot_look``, ``integrity_failure``, ``version_unavailable``, ``refused``:
  the tool refuses in plain words and files nothing. It never falls back to the
  lookup or the starter, because that would file a document in a file the firm
  did not accept while the firm has one.

The matter's attorney is looked up only when the ledger accepts an attorney's
own file of the kind (``caller.needs_matter_attorney``): with nothing accepted,
the tool makes no call it does not make today. The tool has no parameter naming
an attorney, so no requester-named attorney is passed.

The record (``caller.build_record``) goes in the tool's result as
``libraryResolution``.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from operator_connector_sdk.library import (
    CANNOT_LOOK,
    NONE_ACCEPTED,
    REFUSED,
    RESOLVED,
    STYLES,
    Attorney,
    LibraryStore,
    Resolution,
    read_installed,
    resolve,
)
from operator_connector_sdk.library.caller import (
    ACCEPTED,
    BY_NAME,
    CHAT_TOOL,
    NOT_STARTED,
    STARTER,
    BaseUsed,
    LazyStore,
    attorney_key,
    build_record,
    needs_matter_attorney,
)
from operator_connector_sdk.library.resolver import replace_status

from .library import LibraryConfig

#: Where the seat's spec store is installed (``spec_applier``), and the env that moves it.
SPEC_DIR_ENV = "SMD_SPEC_DIR"
DEFAULT_SPEC_DIR = "/var/lib/smd-config/specs"
#: The contracts this tool can write into.
CHAT_CONTRACTS = (STYLES,)

_WORDS = {
    CANNOT_LOOK: "the firm's document library could not be checked just now",
    "integrity_failure": "the firm's accepted file is not the file that was accepted",
    "version_unavailable": "the firm's accepted file was saved again and the accepted version cannot be opened",
    REFUSED: "the firm's accepted file cannot be used for this document",
}


@dataclass(frozen=True)
class ChatPick:
    resolution: Resolution
    refusal: str = ""

    @property
    def data(self) -> bytes | None:
        return self.resolution.data if self.resolution.status == RESOLVED else None

    def template_used(self) -> dict[str, Any]:
        f = self.resolution.file
        assert f is not None
        return {
            "name": "",
            "fileId": f.file_id,
            "sha256": f.sha256,
            "version": f.version,
            "source": "accepted_library_file",
            "scope": f.scope,
            "acceptedBy": f.accepted_by,
        }


def spec_dir() -> Path:
    return Path(os.environ.get(SPEC_DIR_ENV) or DEFAULT_SPEC_DIR)


def open_store(client_factory: Callable[[], Any], cfg: LibraryConfig) -> LibraryStore:
    """The firm's library in its case system. Opened only when the ledger names a file."""
    from .library_store import SmokeballLibrary

    return SmokeballLibrary(client_factory(), cfg)


def matter_attorney(client: Any, matter_id: str) -> Attorney | None:
    """The matter's responsible attorney, by name, from the matter record.
    Raises on a read that did not complete: "could not look" is never "nobody"."""
    matter = client.get(f"/matters/{matter_id}")
    staff_id = matter.get("personResponsibleStaffId") if isinstance(matter, dict) else None
    if not staff_id:
        return None
    staff = client.get(f"/staff/{staff_id}")
    staff = staff if isinstance(staff, dict) else {}
    name = " ".join(p for p in (staff.get("firstName"), staff.get("lastName")) if isinstance(p, str) and p)
    return Attorney(attorney_key(name), name) if name else None


def _refusal(res: Resolution) -> str:
    what = _WORDS.get(res.status, res.status)
    return (
        f"Not made and nothing filed: {what} ({res.reason}). The document is not made on another file "
        "while the firm has an accepted one; SMD needs to look at the library."
    )


def chat_base(
    client_factory: Callable[[], Any],
    cfg: LibraryConfig,
    kind: str,
    matter_id: str | None,
    *,
    spec_dir_path: str | Path | None = None,
) -> ChatPick:
    """The resolver's answer for a chat document of ``kind``, and the refusal, if any."""
    ledger_read = read_installed(spec_dir_path or spec_dir())
    matter: Attorney | None = None
    if matter_id and needs_matter_attorney(ledger_read, kind):
        try:
            matter = matter_attorney(client_factory(), matter_id)
        except Exception as exc:  # noqa: BLE001 - an attorney we could not read is "could not look"
            res = Resolution(
                CANNOT_LOOK, kind, reason=f"the matter's attorney could not be read ({type(exc).__name__})"
            )
            return ChatPick(res, _refusal(res))
    store = LazyStore(lambda: open_store(client_factory, cfg))
    res = resolve(store, ledger_read, kind, matter_attorney=matter)
    if res.status == RESOLVED and res.file is not None and res.file.contract not in CHAT_CONTRACTS:
        res = replace_status(
            res,
            REFUSED,
            f"the accepted file is a {res.file.contract} file, and this tool writes only into a styles file",
        )
    if res.status in (RESOLVED, NONE_ACCEPTED):
        return ChatPick(res)
    return ChatPick(res, _refusal(res))


def chat_record(pick: ChatPick, base: bytes | None, name: str = "") -> dict[str, Any]:
    """The record for the tool's result: the answer, and the base the render used."""
    res = pick.resolution
    if pick.refusal:
        used = BaseUsed(NOT_STARTED)
    elif res.status == RESOLVED:
        used = BaseUsed.of(ACCEPTED, base, contract=res.file.contract if res.file else "")
    else:
        used = BaseUsed.of(BY_NAME, base, name=name) if base is not None else BaseUsed(STARTER)
    return build_record(CHAT_TOOL, res, used)


__all__ = ["CHAT_CONTRACTS", "ChatPick", "chat_base", "chat_record", "matter_attorney", "open_store", "spec_dir"]

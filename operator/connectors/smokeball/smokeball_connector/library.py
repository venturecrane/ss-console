"""Deterministic resolution of the firm's format template for a document class.

The model never chooses typography, and that includes never choosing WHICH
template. This module resolves the one class template from the authored
library location, the same way every time:

    customer.yaml  self_initiation.document_library.{matter_number, folder_name,
                   templates?}            (the live seat copy, world-readable)
      -> the library matter (by its matter number)
      -> the library folder (by name)
      -> the file named ``templates[class]`` or ``Template - <Class>.docx``
      -> its bytes (``client.download_file``)

"Not resolved" is a first-class, reported outcome, never a refusal: the draft
still files, on the starter base, and ``FormatReport.template_expected`` lets
the delivery note say plainly that the firm's template did not resolve and why.
A rename, a moved folder, an unauthored location: each is a sentence in the
note, not a silent fallback that reads like "never configured".

The live customer.yaml is the registered "config-as-data" shape (the block the
self-initiation conductor reads the same way); a connector process cannot rely
on env inheritance from the gateway, so the path is a fixed default with an env
override for tests and local runs.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CUSTOMER_YAML = "/var/lib/smd-config/customer.yaml"
CUSTOMER_YAML_ENV = "SMD_CUSTOMER_YAML_PATH"

# The Operator's own matter (ss-console#2536). One per seat, keyed on this
# number, and it is a CONVENTION rather than a guess: the number is what the
# create tool dedupes on, what the library resolves against, and what the
# self-test falls back to before reporting that it has nowhere to file. A firm
# that wants a different number authors ``operator_matter.number``, and then
# that is the number everywhere.
OPERATOR_LIBRARY_NUMBER = "OPS-OPERATOR-LIBRARY"

# The folder the library lives in, and the name the establishment skill
# proposes. Same reasoning: authored wins, the convention fills the gap.
DEFAULT_FOLDER_NAME = "Document Library"

CLASS_TITLES: dict[str, str] = {
    "discovery_set": "Discovery Set",
    "discovery_response": "Discovery Response",
    "demand_letter": "Demand Letter",
    "mediation_brief": "Mediation Brief",
    "memo": "Memo",
    "letter": "Letter",
}


@dataclass(frozen=True)
class LibraryConfig:
    authored: bool
    matter_number: str | None = None
    folder_name: str | None = None
    templates: dict[str, str] = field(default_factory=dict)
    source: str = ""
    #: True when ``matter_number`` came from the convention rather than the
    #: firm's own authoring. The distinction is reported, never hidden: "the
    #: firm's library matter is missing" and "we looked for the matter we would
    #: have created and it is not there" are different sentences to an admin.
    fallback_number: bool = False

    def template_name(self, document_class: str) -> str:
        custom = self.templates.get(document_class)
        if custom:
            return custom if custom.lower().endswith(".docx") else f"{custom}.docx"
        return f"Template - {CLASS_TITLES.get(document_class, document_class)}.docx"


def load_library_config(path: str | None = None) -> LibraryConfig:
    """Read the library location from the seat's live customer.yaml. Missing
    file, missing block, or an unparseable file all mean "not authored"; the
    reason is carried in ``source`` for the report."""
    path = path or os.environ.get(CUSTOMER_YAML_ENV) or DEFAULT_CUSTOMER_YAML
    try:
        with open(path, encoding="utf-8") as fh:
            raw = fh.read()
    except OSError as exc:
        return LibraryConfig(authored=False, source=f"customer.yaml not readable at {path}: {exc.__class__.__name__}")
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - an unparseable config is "not authored", reported
        return LibraryConfig(authored=False, source=f"customer.yaml not parseable: {exc.__class__.__name__}")
    block = ((data.get("self_initiation") or {}).get("document_library")) if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return LibraryConfig(authored=False, source="self_initiation.document_library not authored")
    templates = block.get("templates") or {}
    if not isinstance(templates, dict):
        templates = {}
    # RESOLUTION ORDER, and each step is a real authoring decision:
    #   1. matter_number      the firm's own library matter, whatever it is called
    #   2. operator_matter.number  the matter the Operator was authorized to create
    #   3. the convention     the number the Operator WOULD create under
    # Step 3 is what lets a seat resolve its library before anybody has
    # authored a number, and the report says the number was a fallback so
    # "not found" never reads as "the firm never configured this".
    operator_matter = block.get("operator_matter")
    if not isinstance(operator_matter, dict):
        operator_matter = {}
    number = _first_nonempty(block.get("matter_number"), operator_matter.get("number"))
    fallback_number = number is None
    if fallback_number:
        number = OPERATOR_LIBRARY_NUMBER
    folder_name = _first_nonempty(block.get("folder_name")) or DEFAULT_FOLDER_NAME
    source = path
    if fallback_number:
        source = f"{path} (matter number defaulted to {OPERATOR_LIBRARY_NUMBER})"
    return LibraryConfig(
        # A block that exists resolves. What it resolves TO may not exist yet,
        # and that is reported by resolve_template as "matter not found", which
        # is the honest sentence and the one an admin can act on.
        authored=True,
        matter_number=number,
        folder_name=folder_name,
        templates={str(k): str(v) for k, v in templates.items()},
        source=source,
        fallback_number=fallback_number,
    )


def _first_nonempty(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if value is not None and not isinstance(value, (str, dict, list)):
            text = str(value).strip()
            if text:
                return text
    return None


@dataclass(frozen=True)
class ResolvedTemplate:
    bytes: bytes
    name: str
    file_id: str
    matter_id: str
    folder_id: str | None


@dataclass(frozen=True)
class NotResolved:
    reason: str
    matter_id: str | None = None
    folder_id: str | None = None


def _listing(resp: Any) -> list[dict[str, Any]]:
    if isinstance(resp, list):
        return [e for e in resp if isinstance(e, dict)]
    if isinstance(resp, dict):
        for key in ("value", "items", "results", "data"):
            if isinstance(resp.get(key), list):
                return [e for e in resp[key] if isinstance(e, dict)]
    return []


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


#: The three answers a matter lookup can give. ``found`` and ``not_found`` are
#: both ANSWERS; ``lookup_failed`` is the absence of one, and the distinction is
#: the whole point of this type. Resolving a template can treat a failed lookup
#: as "no template" and fall back to the starter, because the cost of being
#: wrong is a draft on the wrong letterhead. Deduping a CREATE cannot: the cost
#: of being wrong there is a second matter in the firm's system of record, so a
#: failed lookup has to refuse, and it can only refuse if it can tell itself
#: apart from an empty result.
LOOKUP_FOUND = "found"
LOOKUP_NOT_FOUND = "not_found"
LOOKUP_FAILED = "lookup_failed"


@dataclass(frozen=True)
class MatterLookup:
    state: str
    matter_id: str | None = None
    matched_on: str = ""
    reason: str = ""

    @property
    def found(self) -> bool:
        return self.state == LOOKUP_FOUND

    @property
    def failed(self) -> bool:
        return self.state == LOOKUP_FAILED


def _matter_client_ids(matter: dict[str, Any]) -> set[str]:
    """The contact ids a matter names as its client, across the spellings the
    API uses. Tolerant on purpose: the number is the primary dedupe key and this
    is the second one, so a shape we do not recognize must widen the search
    rather than narrow it."""
    out: set[str] = set()
    for key in ("clientIds", "clients", "clientId"):
        value = matter.get(key)
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    out.add(entry)
                elif isinstance(entry, dict) and entry.get("id"):
                    out.add(str(entry["id"]))
    return out


def lookup_matter(
    client: Any,
    *,
    number: str,
    description: str | None = None,
    client_contact_id: str | None = None,
) -> MatterLookup:
    """Does a matter matching any of these keys already exist?

    Search first (cheap and exact on the number), then a full page-through. Two
    predicates, because a number can be edited off a matter while the matter
    itself remains: the authored NUMBER, and the pair (description, client
    contact) that the create would otherwise duplicate.

    FAILURE IS AN OUTCOME, not a fall-through. A search that raises is tolerated
    only because the page-through that follows enumerates everything the search
    would have; a page-through that raises means the enumeration is INCOMPLETE,
    and an incomplete enumeration is reported as ``lookup_failed`` rather than
    as "nothing found". That distinction is what makes the create tool's dedupe
    fail closed.
    """
    want_number = _norm(number)
    want_description = _norm(description) if description else None
    want_contact = str(client_contact_id) if client_contact_id else None

    def _match(m: dict[str, Any]) -> str | None:
        if want_number and _norm(m.get("number")) == want_number:
            return "number"
        if (
            want_description
            and want_contact
            and _norm(m.get("description")) == want_description
            and want_contact in _matter_client_ids(m)
        ):
            return "description and client"
        return None

    try:
        resp = client.get("/matters", Search=number, Limit=50, Offset=0)
        for m in _listing(resp):
            matched = _match(m)
            if matched:
                return MatterLookup(LOOKUP_FOUND, str(m.get("id")), matched)
    except Exception:  # noqa: BLE001 - the page-through below covers the same ground
        pass
    offset = 0
    while True:
        try:
            resp = client.get("/matters", Limit=500, Offset=offset)
        except Exception as exc:  # noqa: BLE001 - an incomplete enumeration is not an answer
            return MatterLookup(
                LOOKUP_FAILED,
                reason=f"the matter listing could not be read ({exc.__class__.__name__})",
            )
        items = _listing(resp)
        for m in items:
            matched = _match(m)
            if matched:
                return MatterLookup(LOOKUP_FOUND, str(m.get("id")), matched)
        if len(items) < 500:
            return MatterLookup(LOOKUP_NOT_FOUND)
        offset += 500


def find_matter_id(client: Any, matter_number: str) -> str | None:
    """Exact match on the matter ``number`` field. Search first (cheap), then
    page the listing; the number is the stable human-legible key a firm
    authors, never an internal id.

    A thin wrapper over ``lookup_matter`` since ss-console#2536, keeping the
    old two-state answer for template resolution, where "we could not look" and
    "it is not there" lead to the same reported outcome.
    """
    result = lookup_matter(client, number=matter_number)
    return result.matter_id if result.found else None


def _walk_folders(nodes: Any) -> list[dict[str, Any]]:
    """Every folder in a folder listing, at any depth.

    OBSERVED SHAPE (pilot tenant, 2026-08-20): the response is a TREE, not a
    flat list. ``value`` holds one root node carrying ``folders: [...]`` and
    ``files: [...]``; the root itself has no ``name``. A flat read finds
    nothing, which is exactly how the firm's template silently failed to
    resolve on the first live run (every draft fell back to the starter and
    said so). Walk it, and treat a node with a name and an id as a folder at
    any depth.
    """
    out: list[dict[str, Any]] = []
    stack = list(nodes) if isinstance(nodes, list) else [nodes]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("id") and node.get("name"):
            out.append(node)
        for key in ("folders", "children", "subFolders"):
            child = node.get(key)
            if isinstance(child, list):
                stack.extend(child)
    return out


def find_folder_id(client: Any, matter_id: str, folder_name: str) -> str | None:
    try:
        resp = client.get(f"/matters/{matter_id}/documents/folders", Limit=500, Offset=0)
    except Exception:  # noqa: BLE001 - any transport or auth failure listing folders reads as "no such folder"; the caller falls back to the root
        return None
    want = _norm(folder_name)
    for f in _walk_folders(_listing(resp)):
        if _norm(f.get("name")) == want:
            return str(f.get("id"))
    return None


def list_matter_files(client: Any, matter_id: str) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    offset = 0
    while True:
        try:
            resp = client.get(f"/matters/{matter_id}/documents/files", Limit=500, Offset=offset)
        except Exception:  # noqa: BLE001 - a page fetch failing ends the listing with what was already read; the caller treats the list as partial
            break
        items = _listing(resp)
        files.extend(items)
        if len(items) < 500:
            break
        offset += 500
    return files


def _strip_ext(name: str) -> str:
    for ext in (".docx", ".dotx", ".docm", ".dotm"):
        if name.endswith(ext):
            return name[: -len(ext)]
    return name


def names_agree(a: str, b: str) -> bool:
    """Do two file names name the same file, ignoring case, surrounding space,
    and the extension?

    The renderer resolves a class's template by NAME, so a template filed under
    any other name is inert. This is the comparison the write path uses to
    refuse that outcome (#2490), and it normalizes exactly the way
    ``name_matches`` does so the check and the lookup can never disagree.
    """
    return _norm(_strip_ext(a)) == _norm(_strip_ext(b))


def name_matches(entry: dict[str, Any], wanted: str) -> bool:
    """Does this file entry carry the wanted file name?

    OBSERVED (pilot tenant, 2026-08-20, probe H): Smokeball stores ``name``
    WITHOUT the extension and carries ``fileExtension`` separately, so a file
    uploaded as "Template - Discovery Set.docx" lists as
    ``{"name": "Template - Discovery Set", "fileExtension": ".docx"}``. An
    exact match on the full file name therefore never matches anything, which
    is how the firm's template stayed unresolvable even once the folder was
    found. Compare with and without the extension, on both sides, so an
    authored ``templates`` entry works whether or not the firm typed ".docx".
    """
    name = str(entry.get("name") or "")
    ext = str(entry.get("fileExtension") or "")
    have = {_norm(name), _norm(name + ext), _norm(_strip_ext(name))}
    want = {_norm(wanted), _norm(_strip_ext(wanted))}
    return bool(have & want)


def _entry_folder_id(entry: dict[str, Any]) -> str | None:
    """The folder a file entry sits in. Observed live on the pilot tenant
    (2026-08-19, vfy_01M0DTM2EGQZP9FZTM53S17CJ7): a file inside a folder
    carries ``folder: {id, href}`` and a root file carries no ``folder`` key at
    all; ``folderId`` never appears. Both spellings are read so the resolver
    survives either shape; the matter-level ``/documents/files`` listing does
    include files inside folders."""
    folder = entry.get("folder")
    if isinstance(folder, dict) and folder.get("id"):
        return str(folder["id"])
    for key in ("folderId", "folder_id", "parentFolderId"):
        if entry.get(key):
            return str(entry[key])
    return None


def resolve_template(client: Any, cfg: LibraryConfig, document_class: str) -> ResolvedTemplate | NotResolved:
    """The one class template, or the reason there is none. Never raises on a
    missing piece; a transport error downloading a FOUND template does raise,
    because silently rendering on the starter when the firm's file exists would
    misreport the format."""
    if not cfg.authored:
        return NotResolved(f"document library location not authored ({cfg.source or 'self_initiation.document_library'})")
    matter_id = find_matter_id(client, cfg.matter_number or "")
    if not matter_id:
        return NotResolved(f"library matter {cfg.matter_number!r} not found")
    folder_id = find_folder_id(client, matter_id, cfg.folder_name or "")
    want = cfg.template_name(document_class)
    candidates = [e for e in list_matter_files(client, matter_id) if name_matches(e, want)]
    if folder_id:
        in_folder = [e for e in candidates if _entry_folder_id(e) == folder_id]
        if in_folder:
            candidates = in_folder
    if not candidates:
        where = f"folder {cfg.folder_name!r}" if folder_id else f"matter {cfg.matter_number!r} (folder {cfg.folder_name!r} not found)"
        return NotResolved(f"no file named {cfg.template_name(document_class)!r} in {where}", matter_id=matter_id, folder_id=folder_id)
    # Newest wins when a firm re-uploads under the same name.
    candidates.sort(key=lambda e: str(e.get("dateCreated") or e.get("createdDate") or ""), reverse=True)
    chosen = candidates[0]
    file_id = str(chosen.get("id") or chosen.get("fileId"))
    _meta, blob = client.download_file(matter_id, file_id)
    return ResolvedTemplate(bytes=blob, name=str(chosen.get("name")), file_id=file_id, matter_id=matter_id, folder_id=folder_id)


def is_library_file(entry: dict[str, Any], cfg: LibraryConfig, library_folder_id: str | None) -> bool:
    """Templates are not record: a library file must never be a record-check
    source (a header-only letterhead extracts to nothing and would refuse the
    whole draft). Match by the class-template naming convention, and by folder
    when the listing carries a folder id and the library folder is known."""
    if any(name_matches(entry, cfg.template_name(cls)) for cls in CLASS_TITLES):
        return True
    if _norm(entry.get("name")).startswith("template - "):
        return True
    folder = _entry_folder_id(entry)
    return bool(library_folder_id and folder and folder == library_folder_id)


__all__ = [
    "CLASS_TITLES",
    "CUSTOMER_YAML_ENV",
    "DEFAULT_CUSTOMER_YAML",
    "DEFAULT_FOLDER_NAME",
    "LOOKUP_FAILED",
    "LOOKUP_FOUND",
    "LOOKUP_NOT_FOUND",
    "OPERATOR_LIBRARY_NUMBER",
    "LibraryConfig",
    "MatterLookup",
    "NotResolved",
    "ResolvedTemplate",
    "find_folder_id",
    "find_matter_id",
    "lookup_matter",
    "is_library_file",
    "list_matter_files",
    "load_library_config",
    "resolve_template",
]

"""Where the firm's post files (2026-10-01, the firm's written rule): a medical
record or bill into its Medical folder, a vendor invoice into
Accounting/Invoices. Three pieces, each tested so that removing the line it
defends makes it fail:

* ``find_folder_id`` matches a nested PATH by the folder's parents, and only a
  unique one, while a plain name keeps matching at any depth;
* ``combined_post_intake`` is read off the seat's customer.yaml, and an
  unauthored seat files at the root with nothing imposed;
* the page filing chooses the folder from the firm's rule and the letter's
  kind, never from an id the model supplies.
"""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pytest
from smokeball_connector import letter_pages as lp
from smokeball_connector import letter_tools as lt
from smokeball_connector import library
from smokeball_connector import post_intake_config as pic
from smokeball_connector import resolution_token

#: The vendor's folder listing is a TREE under one unnamed root.
TREE = {
    "value": [
        {
            "folders": [
                {"id": "f-med", "name": "Medical", "folders": []},
                {
                    "id": "f-acct",
                    "name": "Accounting",
                    "folders": [
                        {"id": "f-acct-inv", "name": "Invoices", "folders": []},
                        {"id": "f-acct-chk", "name": "Check Copies", "folders": []},
                    ],
                },
                {"id": "f-old", "name": "Old", "folders": [{"id": "f-old-inv", "name": "Invoices", "folders": []}]},
            ]
        }
    ]
}


class _Folders:
    def __init__(self, tree: Any = TREE) -> None:
        self.tree = tree
        self.uploads: list[tuple[str, str, Any]] = []

    def get(self, path: str, **_params: Any) -> Any:
        assert path.endswith("/documents/folders"), path
        return self.tree

    def add_file(self, matter_id: str, file_name: str, blob: bytes, folder_id: Any = None) -> dict:
        self.uploads.append((matter_id, file_name, folder_id))
        return {"fileId": f"file-{len(self.uploads)}"}


# ---- find_folder_id ----------------------------------------------------------


def test_a_nested_path_finds_the_folder_under_its_parent() -> None:
    assert library.find_folder_id(_Folders(), "m-1", "Accounting/Invoices") == "f-acct-inv"
    assert library.find_folder_id(_Folders(), "m-1", " accounting / invoices ") == "f-acct-inv"


def test_a_path_matching_two_folders_or_none_finds_nothing() -> None:
    twice = {
        "value": [
            {
                "folders": [
                    {"id": "a", "name": "Accounting", "folders": [{"id": "a1", "name": "Invoices"}]},
                    {"id": "b", "name": "Accounting", "folders": [{"id": "b1", "name": "Invoices"}]},
                ]
            }
        ]
    }
    assert library.find_folder_id(_Folders(twice), "m-1", "Accounting/Invoices") is None
    assert library.find_folder_id(_Folders(), "m-1", "Billing/Invoices") is None


def test_a_plain_name_still_matches_at_any_depth() -> None:
    assert library.find_folder_id(_Folders(), "m-1", "Medical") == "f-med"
    assert library.find_folder_id(_Folders(), "m-1", "Check Copies") == "f-acct-chk"
    assert library.find_folder_id(_Folders(), "m-1", "") is None


# ---- combined_post_intake ------------------------------------------------------


def test_the_block_parses_and_never_casts(tmp_path: Any) -> None:
    assert pic.parse_post_intake_config(None).folders == {}
    assert pic.parse_post_intake_config({"medical_folder": " Medical "}).folders == {"medical": "Medical"}
    assert pic.parse_post_intake_config({"medical_folder": 7}).error
    assert pic.parse_post_intake_config(["Medical"]).error
    cfg = tmp_path / "customer.yaml"
    cfg.write_text("combined_post_intake:\n  medical_folder: Medical\n")
    assert pic.load_post_intake_config(str(cfg)).folders == {"medical": "Medical"}
    assert pic.load_post_intake_config(str(tmp_path / "absent.yaml")).folders == {}


# ---- the filing ----------------------------------------------------------------

DIGITAL = "Dear Counsel: " + ("this page carries a real text layer. " * 12)


def _pdf(n: int) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    resolution_token._reset_for_tests()
    lp.FILED_PAGES._filed.clear()
    lp.FILED_DOCS._docs.clear()


def _file(monkeypatch: pytest.MonkeyPatch, client: _Folders, cfg_path: str | None, kind: str) -> dict[str, Any]:
    blob = _pdf(2)
    monkeypatch.setattr(lt, "_client", lambda: client)
    monkeypatch.setattr(lt, "fetch_bytes", lambda _c, _u: blob)
    if cfg_path:
        monkeypatch.setenv(library.CUSTOMER_YAML_ENV, cfg_path)
    else:
        monkeypatch.setenv(library.CUSTOMER_YAML_ENV, "/nonexistent/customer.yaml")
    return lt.file_attachment_pages_to_matter(
        matter_id="m-1",
        matter_resolution=resolution_token.mint("m-1", "2024-0117", ("client_name", "date_of_loss")),
        download_url="spool:" + "0" * 32,
        file_name="2026-10-01 Imaging Center",
        sha256=hashlib.sha256(blob).hexdigest(),
        first_page=1,
        last_page=2,
        document_kind=kind,
    )


def _cfg(tmp_path: Any, text: str = "combined_post_intake:\n  medical_folder: Medical\n") -> str:
    p = tmp_path / "customer.yaml"
    p.write_text(text)
    return str(p)


def test_a_medical_document_files_into_the_firms_medical_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = _Folders()
    out = _file(monkeypatch, client, _cfg(tmp_path), "medical")
    assert out["status"] == "filed", out
    assert client.uploads == [("m-1", out["fileName"], "f-med")]
    assert out["folder"] == {"name": "Medical", "id": "f-med"}
    assert "folderNote" not in out


def test_a_matter_without_the_folder_files_at_the_root_and_says_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    client = _Folders({"value": [{"folders": [{"id": "x", "name": "Pleadings"}]}]})
    out = _file(monkeypatch, client, _cfg(tmp_path), "medical")
    assert out["status"] == "filed"
    assert client.uploads[0][2] is None
    assert "no Medical folder" in out["folderNote"] and "folder" not in out


def test_an_ordinary_letter_ignores_the_medical_rule(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = _Folders()
    out = _file(monkeypatch, client, _cfg(tmp_path), "letter")
    assert client.uploads[0][2] is None and "folder" not in out and "folderNote" not in out


def test_an_unauthored_seat_files_at_the_root_and_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Folders()
    out = _file(monkeypatch, client, None, "medical")
    assert client.uploads[0][2] is None and "folder" not in out and "folderNote" not in out


def test_a_malformed_rule_is_reported_and_the_letter_still_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    client = _Folders()
    out = _file(monkeypatch, client, _cfg(tmp_path, "combined_post_intake:\n  medical_folder: 7\n"), "medical")
    assert out["status"] == "filed" and client.uploads[0][2] is None
    assert "could not be read" in out["folderNote"]


def test_a_kind_outside_the_set_refuses_and_files_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    client = _Folders()
    out = _file(monkeypatch, client, _cfg(tmp_path), "Medical/../Accounting")
    assert out["status"] == "refused" and client.uploads == []


def test_no_argument_takes_a_folder_id() -> None:
    import inspect

    params = set(inspect.signature(lt.file_attachment_pages_to_matter).parameters)
    assert "folder_id" not in params and "folder_name" not in params
    assert "document_kind" in params

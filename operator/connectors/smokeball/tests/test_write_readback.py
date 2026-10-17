"""Every agent-driven Smokeball write proves the far end (readback.py,
2026-10-13).

The census that started this: of 26 write tools, 8 read back and compared, 5
checked existence only, and 13 reported success on the vendor's 2xx/202 alone.
Where a read-back did exist its verdict sat in a key the audit plugin never
reads, so a write that never landed scored ``ok`` and SMD was never paged.

Each test below plants ONE far-end disagreement in a fake tenant (the file
never appears; it appears at the wrong size; with the wrong bytes; an event
time shifted by a zone offset; a dropped matter link; memo text that differs; a
delete still present) and asserts the tool returns the top-level shortfall
``status`` that pages SMD, with the do-not-retry note. The same tenant left
alone must come back ``readback: "confirmed"``: a check that also holds clean
writes would be a check nobody could ship.
"""

from __future__ import annotations

import base64
import re
from typing import Any, Callable

import pytest
from fake_files import FileStore, FilesMixin
from smokeball_connector import medicals_tools, readback, server
from smokeball_connector.client import SmokeballApiError, SmokeballClient

M = "m-1"


class Tenant(FilesMixin):
    """A scripted Smokeball tenant: every write lands as sent unless a plant
    rewrites what a read returns."""

    def __init__(self) -> None:
        self.store = FileStore()
        self.tasks: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self.reminders: dict[str, list[dict[str, Any]]] = {}
        self.folders: list[dict[str, Any]] = []
        self.webhooks: list[dict[str, Any]] = []
        self.memos: dict[str, dict[str, Any]] = {}
        self.plants: dict[str, Callable[[Any], Any]] = {}
        self.ignore_deletes = False
        self._n = 0

    def _id(self, prefix: str) -> str:
        self._n += 1
        return f"{prefix}-{self._n}"

    # ---- writes ---------------------------------------------------------------
    def request(self, method: str, path: str, *, json: dict | None = None) -> Any:
        body = dict(json or {})
        if (method, path) == ("POST", "/tasks"):
            tid = self._id("t")
            self.tasks[tid] = self._task(tid, body)
            return {"id": tid}
        if method == "PUT" and path.startswith("/tasks/"):
            tid = path.rsplit("/", 1)[1]
            self.tasks[tid] = self._task(tid, body)
            return {"id": tid}
        if (method, path) == ("POST", "/events"):
            eid = self._id("e")
            self.events[eid] = self._event(eid, body)
            return {"id": eid}
        if method == "POST" and path.endswith("/reminders"):
            rid = self._id("r")
            self.reminders.setdefault(path.split("/")[2], []).append({"id": rid, **body})
            return {"id": rid}
        if method == "PUT" and path.startswith("/events/"):
            eid = path.rsplit("/", 1)[1]
            self.events[eid] = self._event(eid, body)
            return {"id": eid}
        if method == "POST" and path.endswith("/documents/folders"):
            fid = self._id("fo")
            self.folders.append({"id": fid, "name": body["name"]})
            return {"id": fid}
        if method == "DELETE" and "/documents/files/" in path:
            if not self.ignore_deletes:
                self.store.deleted.add(path.rsplit("/", 1)[1])
            return {"href": path}
        if (method, path) == ("POST", "/webhooks"):
            wid = self._id("w")
            self.webhooks.append({"id": wid, "name": body["name"], "eventTypes": list(body["eventTypes"])})
            return {"id": wid}
        if method == "POST" and path.endswith("/memos"):
            mid = self._id("memo")
            self.memos[mid] = {"id": mid, "plainText": body["text"]}
            return {"id": mid}
        raise AssertionError(f"unscripted {method} {path}")

    @staticmethod
    def _task(tid: str, body: dict[str, Any]) -> dict[str, Any]:
        rec = {"id": tid, "subject": body.get("subject"), "note": body.get("note")}
        if body.get("dueDateOnly"):
            rec["dueDateOnly"] = f"{body['dueDateOnly']}T00:00:00"  # the read echoes a datetime
        rec["matter"] = {"id": body["matterId"]} if body.get("matterId") else None
        rec["assignees"] = [{"id": a} for a in body.get("assigneeIds") or []]
        rec["isCompleted"] = bool(body.get("isCompleted"))
        return rec

    @staticmethod
    def _event(eid: str, body: dict[str, Any]) -> dict[str, Any]:
        rec = {k: v for k, v in body.items() if k not in ("matterId", "attendees")}
        rec["id"] = eid
        rec["matter"] = {"id": body["matterId"]} if body.get("matterId") else None
        rec["attendees"] = [{"id": a} for a in body.get("attendees") or []]
        return rec

    # ---- reads ----------------------------------------------------------------
    # The client's own wire methods, so these writes go through ``request`` above.
    create_folder = SmokeballClient.create_folder
    delete_file = SmokeballClient.delete_file

    def get(self, path: str, **_params: Any) -> Any:
        plant = self.plants.get(path)
        if plant is not None:
            try:
                value = self._read(path)
            except AssertionError:
                value = None  # a planted path with no scripted read
            return plant(value)
        return self._read(path)

    def _read(self, path: str) -> Any:
        record = self.store.read(path)
        if record is not None:
            return record
        if path.startswith("/tasks/"):
            return dict(self.tasks[path.rsplit("/", 1)[1]])
        if m := re.fullmatch(r"/events/([^/]+)/reminders", path):
            return {"value": list(self.reminders.get(m.group(1), []))}
        if path.startswith("/events/"):
            return dict(self.events[path.rsplit("/", 1)[1]])
        if path.endswith("/documents/folders"):
            return {"value": [{"folders": list(self.folders)}]}
        if path == "/webhooks":
            return {"value": list(self.webhooks)}
        if m := re.fullmatch(r"/matters/[^/]+/memos/([^/]+)", path):
            return dict(self.memos[m.group(1)])
        if path == f"/matters/{M}":
            return {"id": M}
        raise AssertionError(f"unscripted GET {path}")


@pytest.fixture()
def tenant(monkeypatch: pytest.MonkeyPatch) -> Tenant:
    t = Tenant()
    monkeypatch.setattr(server, "_get_client", lambda: t)
    return t


def _shortfall(out: dict[str, Any], status: str) -> None:
    assert out["status"] == status, out
    assert "Do NOT retry" in out["readback_note"] and "SMD has been alerted" in out["readback_note"]
    assert "—" not in out["readback_note"]  # no em dash in agent-facing text


# ---- files: add_file and every tool that files through it -----------------------


def test_a_clean_upload_reads_back_by_id_size_and_sha(tenant: Tenant) -> None:
    out = server.add_file(M, "note.txt", content_text="the note")
    assert out["readback"] == "confirmed" and out["uploaded"] is True
    assert out["readbackSha256"] and "status" not in out


def test_a_file_that_never_appears_is_link_not_visible(tenant: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    real_add = tenant.store.add

    def add_hidden(*a: Any, **k: Any) -> str:
        fid = real_add(*a, **k)
        tenant.store.hide.add(fid)
        return fid

    monkeypatch.setattr(tenant.store, "add", add_hidden)
    out = server.add_file(M, "note.txt", content_text="the note")
    _shortfall(out, "link_not_visible")
    assert out["fileId"]  # the upload's own keys are kept


def test_a_file_stored_at_the_wrong_size_is_a_mismatch(tenant: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    real_add = tenant.store.add

    def add_short(*a: Any, **k: Any) -> str:
        fid = real_add(*a, **k)
        tenant.store.wrong_size[fid] = 3
        return fid

    monkeypatch.setattr(tenant.store, "add", add_short)
    out = server.add_file(M, "note.txt", content_text="the note")
    _shortfall(out, "readback_mismatch")
    assert "3 bytes" in out["readback_detail"]


def test_a_file_stored_with_other_bytes_is_a_mismatch(tenant: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    real_add = tenant.store.add

    def add_swapped(*a: Any, **k: Any) -> str:
        fid = real_add(*a, **k)
        tenant.store.wrong_bytes[fid] = b"the nite"  # same length, other bytes
        return fid

    monkeypatch.setattr(tenant.store, "add", add_swapped)
    out = server.add_file(M, "note.txt", content_text="the note")
    _shortfall(out, "readback_mismatch")
    assert "downloaded bytes differ" in out["readback_detail"]


def test_an_upload_without_an_id_is_found_by_name_in_its_folder(tenant: Tenant) -> None:
    """No fileId from the upload: the file is located by name in its folder,
    reading the listing in pages (``library.list_matter_files``)."""
    fid = tenant.store.add(M, "served", b"%PDF served", "fld-9")
    listing = {
        "value": [{"id": fid, "name": "served", "fileExtension": ".pdf", "folder": {"id": "fld-9"}, "sizeBytes": 11}]
    }
    tenant.plants[f"/matters/{M}/documents/files"] = lambda _v: listing
    proof = readback.prove_file(tenant, M, None, b"%PDF served", file_name="served.pdf", folder_id="fld-9")
    assert proof["ok"] is True and proof["extra"]["readbackFileId"] == fid


def test_a_file_over_the_download_limit_is_proven_by_size_and_says_so(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readback, "HASH_LIMIT_BYTES", 4)
    out = server.add_file(M, "big.txt", content_text="more than four bytes")
    assert out["readback"] == "confirmed" and "not hashed" in out["readback_detail"]


def test_a_rendered_draft_keeps_its_keys_and_carries_the_shortfall(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_add = tenant.store.add

    def add_swapped(*a: Any, **k: Any) -> str:
        fid = real_add(*a, **k)
        tenant.store.wrong_bytes[fid] = b"PK not the template"
        return fid

    monkeypatch.setattr(tenant.store, "add", add_swapped)
    out = server.render_docx_template(M, "Intake skeleton", "# Intake\n\nClient name: {{FILL: name | intake form}}")
    _shortfall(out, "readback_mismatch")
    assert out["sha256"] and out["sizeBytes"] and out["refusals"] == []


def test_file_attachment_to_matter_reads_back(tenant: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "fetch_bytes", lambda _c, _u: b"%PDF attached")
    assert server.file_attachment_to_matter(M, "spool:abc", "served.pdf")["readback"] == "confirmed"


def test_a_workbook_reads_back(tenant: Tenant) -> None:
    from smokeball_connector import workbook_tools

    sheets = [{"name": "Costs", "columns": [{"header": "Item"}], "rows": [["Mileage"]]}]
    out = workbook_tools.add_workbook(M, "costs", sheets)  # its _client is the server's, patched by the fixture
    assert out["readback"] == "confirmed" and out["sheets"][0]["rows"] == 1


# ---- deletes ---------------------------------------------------------------------


def test_a_deleted_file_reads_back_deleted(tenant: Tenant) -> None:
    fid = tenant.store.add(M, "old.txt", b"old")
    assert server.delete_file(M, fid)["readback"] == "confirmed"


def test_a_file_still_present_after_its_delete_is_a_mismatch(tenant: Tenant) -> None:
    fid = tenant.store.add(M, "old.txt", b"old")
    tenant.ignore_deletes = True
    out = server.delete_file(M, fid)
    _shortfall(out, "readback_mismatch")
    assert out["href"]  # the tracking link is kept


def test_a_404_after_a_delete_is_gone(tenant: Tenant) -> None:
    fid = tenant.store.add(M, "old.txt", b"old")

    def gone(_v: Any) -> Any:
        raise SmokeballApiError("GET", "x", 404, "")

    tenant.plants[f"/matters/{M}/documents/files/{fid}"] = gone
    assert server.delete_file(M, fid)["readback"] == "confirmed"


# ---- memos -----------------------------------------------------------------------


def test_a_memo_that_reads_back_as_written_is_confirmed(tenant: Tenant) -> None:
    out = server.create_memo(M, "Called the adjuster; claim open.")
    assert out["confirmed"] is True and out["readback"] == "confirmed"


def test_a_memo_whose_text_differs_is_a_mismatch(tenant: Tenant) -> None:
    tenant.plants = {}
    real = tenant._read

    def differs(path: str) -> Any:
        value = real(path)
        return {**value, "plainText": "something else"} if "/memos/" in path else value

    tenant._read = differs  # type: ignore[method-assign]
    out = server.create_memo(M, "Called the adjuster; claim open.")
    assert out["confirmed"] is False  # the existing key keeps its meaning
    _shortfall(out, "readback_mismatch")


def test_a_memo_that_cannot_be_read_back_is_link_not_visible(tenant: Tenant) -> None:
    real = tenant._read

    def unreadable(path: str) -> Any:
        if "/memos/" in path:
            raise LookupError("404")
        return real(path)

    tenant._read = unreadable  # type: ignore[method-assign]
    out = server.create_memo(M, "Called the adjuster; claim open.")
    assert out["confirmed"] == "unknown"
    _shortfall(out, "link_not_visible")


# ---- tasks -----------------------------------------------------------------------


def test_a_clean_task_reads_back(tenant: Tenant) -> None:
    out = server.create_task("s-1", "Serve responses", matter_id=M, due_date="2026-11-02", assignee_ids=["s-2"])
    assert out["readback"] == "confirmed" and "status" not in out


def test_a_task_due_date_that_moved_is_a_mismatch(tenant: Tenant) -> None:
    out = server.create_task("s-1", "Serve responses", matter_id=M, due_date="2026-11-02")
    assert out["readback"] == "confirmed"
    tenant.plants[f"/tasks/{out['id']}"] = lambda v: {**v, "dueDateOnly": "2026-11-01T00:00:00"}
    out = server.update_task(out["id"], due_date="2026-11-02", staff_id="s-1")
    _shortfall(out, "readback_mismatch")
    assert "due date" in out["readback_detail"]


def test_a_task_subject_that_differs_is_a_mismatch(tenant: Tenant) -> None:
    tenant.plants["/tasks/t-1"] = lambda v: {**v, "subject": "[Operator] Something else"}
    out = server.create_task("s-1", "Serve responses", matter_id=M)
    _shortfall(out, "readback_mismatch")
    assert "subject" in out["readback_detail"]


def test_a_task_write_with_no_id_is_link_not_visible(tenant: Tenant, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tenant, "request", lambda *_a, **_k: {"href": "/tasks"})
    _shortfall(server.create_task("s-1", "Serve responses", matter_id=M), "link_not_visible")


# ---- events ----------------------------------------------------------------------


def _create_event() -> dict[str, Any]:
    return server.create_event(
        subject="Hearing on motion",
        start_time="2026-11-03T09:00:00",
        end_time="2026-11-03T10:00:00",
        attendees=["s-1"],
        time_zone="America/Los_Angeles",
        matter_id=M,
        description="Dept. 12",
    )


def test_a_clean_event_reads_back_on_its_local_clock(tenant: Tenant) -> None:
    out = _create_event()
    assert out["readback"] == "confirmed" and "status" not in out


def test_an_event_time_shifted_by_the_zone_offset_is_a_mismatch(tenant: Tenant) -> None:
    """The 9:00 a.m. hearing stored as 16:00 (a UTC conversion): the exact
    defect a clock-blind read-back would wave through."""
    tenant.plants["/events/e-1"] = lambda v: {**v, "startTime": "2026-11-03T16:00:00", "endTime": "2026-11-03T17:00:00"}
    out = _create_event()
    _shortfall(out, "readback_mismatch")
    assert "startTime" in out["readback_detail"] and "(local)" in out["readback_detail"]


def test_an_event_that_lost_its_matter_link_is_a_mismatch(tenant: Tenant) -> None:
    """The full-replace PUT that drops ``matterId`` (proven live 2026-09-24)."""
    eid = _create_event()["id"]
    # Only the post-PUT read loses the link; the pre-PUT read the merge uses is intact.
    tenant.plants[f"/events/{eid}"] = lambda v: {**v, "matter": None} if v["startTime"].startswith("2026-11-04") else v
    out = server.update_event(eid, start_time="2026-11-04T09:00:00", end_time="2026-11-04T10:00:00")
    _shortfall(out, "readback_mismatch")
    assert "matter link" in out["readback_detail"]


def test_a_clean_event_update_reads_back(tenant: Tenant) -> None:
    eid = _create_event()["id"]
    out = server.update_event(eid, start_time="2026-11-04T09:00:00", end_time="2026-11-04T10:00:00")
    assert out["readback"] == "confirmed"
    assert tenant.events[eid]["startTime"] == "2026-11-04T09:00:00"


def test_an_event_that_cannot_be_read_is_link_not_visible(tenant: Tenant) -> None:
    def missing(_v: Any) -> Any:
        raise SmokeballApiError("GET", "/events/e-1", 404, "")

    tenant.plants["/events/e-1"] = missing
    _shortfall(_create_event(), "link_not_visible")


def test_a_reminder_reads_back_and_a_missing_one_is_link_not_visible(tenant: Tenant) -> None:
    eid = _create_event()["id"]
    assert server.create_event_reminder(eid, offset=2, offset_type_id=3)["readback"] == "confirmed"
    tenant.plants[f"/events/{eid}/reminders"] = lambda _v: {"value": []}
    _shortfall(server.create_event_reminder(eid, offset=2, offset_type_id=3), "link_not_visible")


# ---- folders, webhooks, the Operator's matter -------------------------------------


def test_a_folder_reads_back_in_its_tree_and_an_absent_one_is_link_not_visible(tenant: Tenant) -> None:
    assert server.create_folder(M, "Discovery - Set One")["readback"] == "confirmed"
    tenant.plants[f"/matters/{M}/documents/folders"] = lambda _v: {"value": [{"folders": []}]}
    _shortfall(server.create_folder(M, "Discovery - Set Two"), "link_not_visible")


def test_a_webhook_reads_back_and_a_dropped_event_type_is_a_mismatch(
    tenant: Tenant, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.delenv("WEBHOOK_SECRET_SMOKEBALL", raising=False)
    # The callback is the seat's authored gate (webhook_target.py), never caller-chosen.
    cfg = tmp_path / "customer.yaml"
    cfg.write_text(
        "connectors:\n  PM:\n    backend: mcp:smokeball\n    enabled: true\n"
        "    webhook_url: https://gw.example/webhooks/smokeball\n"
    )
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(cfg))
    out = server.create_webhook_subscription("ops", ["matter.updated"])
    assert out["readback"] == "confirmed"
    tenant.plants["/webhooks"] = lambda v: {"value": [{**w, "eventTypes": []} for w in v["value"]]}
    out = server.create_webhook_subscription("ops2", ["task.created"])
    _shortfall(out, "readback_mismatch")


# ---- the medicals tab poll ----------------------------------------------------------


def test_a_failed_medicals_read_back_get_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    """``medicals_tools._poll`` raised on a failed GET after the write; it now
    keeps polling and hands the caller what it last read, so the caller
    reports ``readback_mismatch`` rather than crashing after a write."""
    monkeypatch.setattr(medicals_tools, "SLEEP", lambda _s: None)

    class _Down:
        def get(self, _path: str, **_p: Any) -> Any:
            raise RuntimeError("vendor down")

    assert medicals_tools._poll(_Down(), "/x", (1, 2), lambda _v: True) == {}


def test_base64_upload_is_proven_too(tenant: Tenant) -> None:
    payload = base64.b64encode(b"%PDF-1.4 binary").decode()
    assert server.add_file(M, "scan.pdf", content_base64=payload)["readback"] == "confirmed"

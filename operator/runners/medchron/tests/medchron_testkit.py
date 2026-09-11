"""Synthetic test data and helpers for the medchron runner tests.

Nothing here names a firm, a client, a provider, or a matter; every value is
obviously invented (and shaped so gitleaks has nothing to see: no DOB-like or
SSN-like strings beyond the schema's own MM/DD/YYYY).

This is a plain module, not conftest.py, because the CI step runs pytest from
`operator/`, where the name `tests` resolves to `operator/tests/` (a package)
and `from tests.conftest import ...` shadows. pytest puts this directory on
sys.path (no __init__.py, prepend import mode), so `import medchron_testkit`
resolves the same way in CI and on a laptop.
"""

from __future__ import annotations

import json
from pathlib import Path

# The synthetic firm config lives in the package so the seat probes can run
# without the firm's private tables (ss#2614); the tests reuse it verbatim.
from medchron.probes import SYNTHETIC_FIRM as FIRM_CONFIG  # noqa: F401 - after the path shim; re-exported to test_decisions_driver, which imports it from here

PRICING = {
    "_meta": {
        "version": "3",
        "units": "cents per million tokens",
        "multipliers": {"batch": 0.5, "cache_write_5m": 1.25, "cache_write_1h": 2.0, "cache_read": 0.10},
    },
    "models": {
        "claude-opus-5": {"input_per_million_cents": 500, "output_per_million_cents": 2500},
        "claude-sonnet-5": {"input_per_million_cents": 200, "output_per_million_cents": 1000},
        "claude-haiku-4-5-20251001": {"input_per_million_cents": 80, "output_per_million_cents": 400},
    },
}


def job_yaml(
    data_root: Path, *, joint: bool = False, cap: float | None = None, install_root: Path | None = None
) -> str:
    units = [
        {
            "unit": "alpha",
            "client_name": "Alpha Example",
            "name_token": "Alpha",
            "surname": "Example",
            "dob": "01/01/1970",
            **({"folder_prefix": "/Alpha_Example"} if joint else {}),
        }
    ]
    if joint:
        units.append(
            {
                "unit": "beta",
                "client_name": "Beta Example",
                "name_token": "Beta",
                "surname": "Example",
                "dob": "02/02/1980",
                "folder_prefix": "/Beta_Example",
            }
        )
    body = {
        "slug": "example-matter",
        "matter": {
            "number": "2099-EX-0001",
            "id": "00000000-0000-4000-8000-000000000001",
            "title": "Example v. Example",
        },
        "units": units,
        "incident": {"date": "2026-01-15", "source": "matter_layout"},
        "injuries": "example injury",
        "data_root": str(data_root),
    }
    if cap is not None:
        body["cap_usd"] = cap
    if install_root is not None:
        # The seat shape: controls and ICD tables live in an install-level tree
        # the daemon names, never under the per-job data_root.
        body["install_root"] = str(install_root)
    import yaml

    return yaml.safe_dump(body, sort_keys=False)


def seed_folders(data_root: Path, tops: list[str]) -> None:
    folders = [{"id": f"id-{i}", "name": t, "parentId": None, "path": f"/{t}"} for i, t in enumerate(tops)]
    (data_root / "example-matter" / "folders.json").write_text(json.dumps(folders), encoding="utf-8")


def seed_raw_manifest(data_root: Path, rows: list[dict]) -> None:
    p = data_root / "example-matter" / "raw_manifest.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def calls(data_root: Path) -> list[dict]:
    p = data_root / "calls.jsonl"
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def write_ledger(data_root: Path, unit: str, rows: list[dict]) -> Path:
    p = data_root / "example-matter" / "runs" / unit / "usage-ledger.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return p


# ---- a seat that never touches a tenant ----------------------------------------
class FakeSeat:
    """Implements medchron.seat.Seat over dicts. `docs` are manifest rows
    (id, name, size, ext, folderId, deleted), `folders` are tree rows
    (id, name, parentId, path), `blobs` map id -> bytes. Mint hands back an
    https URL that encodes the id; fetch writes the blob and honours the
    advertised size, exactly as the real backends do."""

    def __init__(
        self, docs: list[dict], folders: list[dict], blobs: dict[str, bytes], fail_mint: set[str] | None = None
    ) -> None:
        self.docs, self.folders, self.blobs = docs, folders, blobs
        self.fail_mint = fail_mint or set()
        self.mints: list[list[str]] = []
        # ss#2614 upload: folders created and files sent by the runner; a
        # sent file shows in list_files only after `lag` further list calls
        # (the vendor's index lag), and `crash_after` sends raise once the
        # count is reached (a kill mid-upload).
        self.created: list[dict] = []
        self.sent: list[dict] = []
        self.lag = 0
        self.crash_after: int | None = None
        self._pending: list[tuple[int, dict]] = []
        self._lists = 0

    def create_folder(self, matter_id: str, name: str) -> dict:
        fid = f"folder-{len(self.created) + 1}"
        row = {"id": fid, "name": name, "parentId": None, "path": name}
        self.created.append(row)
        self.folders.append(row)
        return {"id": fid}

    def add_file(self, matter_id: str, folder_id: str, name: str, data: bytes) -> dict:
        if self.crash_after is not None and len(self.sent) >= self.crash_after:
            raise RuntimeError("connection dropped mid-upload")
        self.sent.append({"folderId": folder_id, "name": name, "size": len(data)})
        self._pending.append(
            (
                self._lists + self.lag,
                {
                    "id": f"up-{len(self.sent)}",
                    "name": name,
                    "size": len(data),
                    "ext": name.rsplit(".", 1)[-1],
                    "folderId": folder_id,
                },
            )
        )
        return {"fileId": None}

    def _materialize(self) -> None:
        self._lists += 1
        keep = []
        for due, row in self._pending:
            (self.docs.append(row) if due <= self._lists else keep.append((due, row)))
        self._pending = keep

    def list_files(self, matter_id: str) -> list[dict]:
        self._materialize()
        return [dict(d) for d in self.docs]

    def folder_tree(self, matter_id: str) -> list[dict]:
        return [dict(f) for f in self.folders]

    def mint(self, matter_id: str, file_ids: list[str]) -> list[dict]:
        self.mints.append(list(file_ids))
        out = []
        for fid in file_ids:
            if fid in self.fail_mint or fid not in self.blobs:
                out.append({"id": fid, "error": "not found"})
            else:
                out.append({"id": fid, "url": f"https://fake.invalid/{fid}", "size": len(self.blobs[fid])})
        return out

    def fetch(self, url: str, dest: Path, expected_size: int | None) -> int:
        fid = url.rsplit("/", 1)[1]
        data = self.blobs[fid]
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if expected_size and len(data) != expected_size:
            dest.unlink()
            raise RuntimeError(f"size mismatch: got {len(data)}, expected {expected_size}")
        return len(data)


def make_pdf(pages: list[str]) -> bytes:
    """A real PDF with one text page per string (empty string = no text layer)."""
    import pymupdf

    doc = pymupdf.open()
    for text in pages:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def doc_row(fid: str, name: str, folder_id: str | None, size: int, ext: str | None = None) -> dict:
    """A listing row as the seat returns it: the NAME carries no extension,
    the extension is its own field (every stage joins them back as name+ext)."""
    if ext is None and "." in name:
        name, suffix = name.rsplit(".", 1)
        ext = "." + suffix
    return {
        "id": fid,
        "name": name,
        "size": size,
        "ext": ext or "",
        "folderId": folder_id,
        "created": None,
        "modified": None,
        "deleted": False,
    }


def seed_seat_files(data_root: Path, seat: "FakeSeat") -> None:
    """What list_matter would write, for tests that start after it."""
    sd = data_root / "example-matter"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "manifest.json").write_text(json.dumps({"documents": seat.docs}), encoding="utf-8")
    (sd / "folders.json").write_text(json.dumps(seat.folders), encoding="utf-8")

"""Decommission step 07: the real compliance evidence packet (2026-09-25 review).

Until this module, step 07 ran ``InMemoryComplianceArchiver``, which wrote a
JSON list of the files a packet *would* contain and reported the step
EXECUTED. The live-run refusal did not name it, so a fully credentialed
``--live`` run would destroy the Machine and then print a clean decommission
over a packet that was never built. This is the #1123 class the refusal was
built to end, on the offboarding evidence instead of on a deletion.

What this archiver does
-----------------------

1. Finds the machine snapshot step 02 wrote (``machine-snapshot-{date}.sqlite``,
   ``bin.lib.seam_pull.SeamAuditLogPreserver``): the audit ledger with its
   hash-chain link columns plus the ADR-0016 memory tables, pulled off the
   Machine BEFORE step 06 destroyed it. That snapshot is the evidence
   generator's ``--read-db`` input; the module docstring of
   ``bin/lib/decommission.py`` has named it so since #1355. It is opened
   read-only: the preserved copy is itself evidence and nothing here may write
   to it.
2. Runs :class:`adapter.evidence.EvidencePacketBuilder` over it for the whole
   ledger (``matter="all"``), with the chain head the console last recorded
   for this seat as the truncation pin when one exists.
3. Reads the packet back from disk and reports what it OBSERVED (Law 14): the
   path, the file count in the tarball, the bytes on disk, the manifest hash
   recomputed from the archived ``manifest.json``, and whether ``manifest.sig``
   verifies against the public half of the key that signed it. A packet the
   builder says it wrote but the disk does not show, or a signature that does
   not verify, raises: the step fails rather than report a packet it cannot
   see.

It is wired only from a staged ``EVIDENCE_PACKET_SIGNING_KEY_B64`` (see
``backends_from_env``), so a wired archiver always produces a signed packet;
an unsigned read-back from a wired archiver is a failure, not a variant.

Idempotency: a packet already archived today is re-read and reported
``skipped`` with ``reason="packet_already_archived"``, so a resumed run does
not build a second packet over the same snapshot.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tarfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from adapter.evidence import EvidencePacketBuilder, PacketActor, PacketRequest
from adapter.evidence.packet import SqliteReadExecutor
from adapter.evidence.signing import DETACHED_SIGNATURE_FILENAME, load_signer

#: The snapshot step 02 writes (seam_pull.SeamAuditLogPreserver).
SNAPSHOT_GLOB = "machine-snapshot-*.sqlite"
MANIFEST_ENTRY = "manifest.json"

PinSource = Callable[[str], Optional[str]]


def packet_path_for(archive_dir: Path, when: datetime) -> Path:
    return archive_dir / f"compliance-packet-{when.strftime('%Y-%m-%d')}.tar.gz"


def latest_snapshot(archive_dir: Path) -> Optional[Path]:
    """The newest step-02 snapshot in the archive dir. The name carries the
    UTC date, so lexical order is date order."""
    found = sorted(archive_dir.glob(SNAPSHOT_GLOB)) if archive_dir.is_dir() else []
    return found[-1] if found else None


def customer_yaml_for(customers_root: Path, slug: str) -> Optional[Path]:
    """The live customer.yaml, or the newest tombstone's copy on a resumed run
    (step 08 renames the dir but keeps the file)."""
    live = customers_root / slug / "customer.yaml"
    if live.is_file():
        return live
    tombs = sorted(p / "customer.yaml" for p in customers_root.glob(f"{slug}.decommissioned.*"))
    tombs = [p for p in tombs if p.is_file()]
    return tombs[-1] if tombs else None


def _utc_seconds(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parsed(ts: object) -> list[datetime]:
    """A ledger timestamp as an aware UTC datetime, or nothing. A naive stamp
    is UTC (the broker writes UTC); an unparseable one is not a bound."""
    if not isinstance(ts, str) or not ts:
        return []
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return []
    return [parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)]


def ledger_period(conn: sqlite3.Connection, now: datetime) -> tuple[str, str]:
    """A period that contains every row in the snapshot's ledger.

    The builder filters ``ts >= start AND ts <= end`` as TEXT, and ledger
    timestamps carry fractions and offsets (``...:05.123+00:00``) that sort
    BELOW ``...:05Z`` at the 20th character. So the start is the day before
    the oldest row at midnight, and the end is one second past the later of
    now and the newest row: both bounds sit strictly outside every row.
    """
    try:
        oldest, newest = conn.execute("SELECT MIN(ts), MAX(ts) FROM audit_log").fetchone()
    except sqlite3.OperationalError:
        oldest = newest = None
    start = min([now, *_parsed(oldest)])
    end = max([now, *_parsed(newest)])
    start_day = (start.astimezone(timezone.utc) - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return _utc_seconds(start_day), _utc_seconds(end + timedelta(seconds=1))


def read_back_packet(path: Path, signing_env: dict) -> dict:
    """What is on disk, observed: members, size, manifest hash, signature."""
    with tarfile.open(path, "r:gz") as tar:
        members = {m.name: m for m in tar.getmembers() if m.isfile()}
        manifest = tar.extractfile(members[MANIFEST_ENTRY]).read() if MANIFEST_ENTRY in members else None  # type: ignore[union-attr]
        sig_member = members.get(DETACHED_SIGNATURE_FILENAME)
        signature = tar.extractfile(sig_member).read() if sig_member is not None else None  # type: ignore[union-attr]
    signer = load_signer(signing_env) if manifest is not None and signature is not None else None
    verified = (
        signer is not None and manifest is not None and signature is not None and signer.verifies(manifest, signature)
    )
    return {
        "archive_path": str(path),
        "file_count": len(members),
        "bytes_on_disk": path.stat().st_size,
        "manifest_sha256": hashlib.sha256(manifest).hexdigest() if manifest is not None else None,
        "signed": signature is not None,
        "signature_verified": verified,
    }


@dataclass
class EvidencePacketArchiver:
    """Real :class:`bin.lib.decommission.ComplianceArchiver`."""

    customers_root: Path
    signing_env: dict
    audit_writer: Optional[object] = None  # the decommission trail; carries COMPLIANCE_PACKET_EXPORTED
    pin_source: Optional[PinSource] = None
    actor: str = "captain"
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(timezone.utc))

    async def archive(self, customer_slug: str, archive_dir: Path) -> dict:
        now = self.clock()
        packet = packet_path_for(archive_dir, now)
        if packet.exists():
            observed = self._checked(read_back_packet(packet, self.signing_env), expected_files=None)
            return {"skipped": True, "reason": "packet_already_archived", **observed}

        snapshot = latest_snapshot(archive_dir)
        if snapshot is None:
            raise RuntimeError(
                f"no {SNAPSHOT_GLOB} under {archive_dir}: step 02 did not preserve the "
                "Machine's ledger, so there is nothing to build the packet from"
            )
        yaml_path = customer_yaml_for(self.customers_root, customer_slug)
        if yaml_path is None:
            raise RuntimeError(f"no customer.yaml for {customer_slug!r} under {self.customers_root}")
        if self.audit_writer is None:
            raise RuntimeError("no audit writer for the packet's COMPLIANCE_PACKET_EXPORTED row")
        pinned_head = self.pin_source(customer_slug) if self.pin_source is not None else None

        import yaml  # the CLI runs under `uv run --with pyyaml`

        conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        try:
            period_start, period_end = ledger_period(conn, now)
            builder = EvidencePacketBuilder(
                reader=SqliteReadExecutor(conn),
                audit_writer=self.audit_writer,
                yaml_loader=yaml.safe_load,
                yaml_dumper=lambda data: yaml.safe_dump(data, sort_keys=True),
                signing_env=self.signing_env,
            )
            result = await builder.build(
                PacketRequest(
                    customer_slug=customer_slug,
                    matter="all",
                    period_start=period_start,
                    period_end=period_end,
                    output_path=packet,
                    customer_yaml_path=yaml_path,
                    actor=self.actor,
                    actor_role=PacketActor.CAPTAIN,
                    pinned_head=pinned_head,
                )
            )
        finally:
            conn.close()

        observed = self._checked(read_back_packet(packet, self.signing_env), expected_files=result.file_count)
        if observed["manifest_sha256"] != result.manifest_sha256:
            raise RuntimeError(
                f"packet at {packet} does not carry the manifest the builder reported "
                f"({observed['manifest_sha256']} != {result.manifest_sha256})"
            )
        return {
            "skipped": False,
            **observed,
            "snapshot_path": str(snapshot),
            "period_start": period_start,
            "period_end": period_end,
            "chain_pin_checked": result.chain_pin.was_checked,
            "audit_rows_unattributed": result.coverage.has_unattributed_rows,
        }

    @staticmethod
    def _checked(observed: dict, *, expected_files: Optional[int]) -> dict:
        if expected_files is not None and observed["file_count"] != expected_files:
            raise RuntimeError(
                f"packet at {observed['archive_path']} holds {observed['file_count']} files, "
                f"the builder reported {expected_files}"
            )
        if not observed["signature_verified"]:
            raise RuntimeError(
                f"packet at {observed['archive_path']} is not signed by the staged key "
                f"(signed={observed['signed']}); a wired archiver produces signed packets only"
            )
        return observed


__all__ = [
    "EvidencePacketArchiver",
    "customer_yaml_for",
    "latest_snapshot",
    "ledger_period",
    "packet_path_for",
    "read_back_packet",
]

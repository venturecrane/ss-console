#!/usr/bin/env python3
"""Ingest a curated voice corpus into the per-customer R2 vault (tracer).

Tracer Stage 3. Reads the reviewed corpus JSONL, runs each message through
the REAL differ (:func:`adapter.voice.extract_structural_diff`) and the
fail-closed leak invariant (:func:`bin.lib.voice_corpus.assert_style_only`),
and writes content-free structural-diff samples to the vault at the exact
key the runtime reader consumes:
``vaults/{slug}/voice/cohort/{cohort}/{id}.json``.

Two modes:
  --out-dir DIR  dry-run; write samples to a local mirror of the vault keys
  --r2           upload to R2 using the R2_* env the Machine bootstrap sets

The leak invariant runs in BOTH modes (it guards the emitted JSON, not the
transport). A leak aborts the whole run non-zero before anything is written.

Usage::

    cd operator
    python bin/voice-ingest-corpus.py --corpus /tmp/scott-corpus.jsonl --slug <slug> --out-dir /tmp/vault   # dry-run
    python bin/voice-ingest-corpus.py --corpus /tmp/scott-corpus.jsonl --slug <slug> --r2                    # upload
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # operator/ on sys.path

from bin.lib.voice_corpus import (  # noqa: E402 - the import needs the sys.path shim above it (packaging follow-up named in pyproject.toml)
    VoiceLeakError,
    build_sample,
    load_cohort_vocabulary,
)


def _load_corpus(path: str) -> list[str]:
    texts: list[str] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        text = obj.get("text") if isinstance(obj, dict) else None
        if isinstance(text, str) and text.strip():
            texts.append(text)
    return texts


def _upload_r2(key: str, body: bytes) -> None:
    import boto3  # lazy: only needed in --r2 mode

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["R2_ENDPOINT_URL"],
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
    )
    client.put_object(
        Bucket=os.environ["R2_BUCKET_CONFIG"],
        Key=key,
        Body=body,
        ContentType="application/json",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Ingest a curated voice corpus into the R2 vault.")
    p.add_argument("--corpus", required=True, help="Reviewed corpus JSONL.")
    p.add_argument("--slug", required=True, help="Seat customer slug the vault keys are written under.")
    p.add_argument("--cohort", default="unassigned")
    p.add_argument(
        "--customer-yaml",
        help="Seat customer.yaml; the cohort must be in its authored voice_cohorts vocabulary.",
    )
    p.add_argument(
        "--unvalidated-cohort",
        action="store_true",
        help="Skip the vocabulary gate (tracer/dev use only; says so loudly).",
    )
    p.add_argument("--out-dir", help="Dry-run: write samples under this dir using vault keys.")
    p.add_argument("--r2", action="store_true", help="Upload to R2 via R2_* env.")
    p.add_argument("--limit", type=int)
    args = p.parse_args(argv)

    if not args.out_dir and not args.r2:
        p.error("choose --out-dir (dry-run) or --r2 (upload)")

    # Cohort gate (2026-08-10, #2222). This script was the ONE ingestion path
    # with no vocabulary check, and an unauthorized cohort directory reached a
    # live vault through it — a vault path no profile loader on that seat ever
    # reads. Same posture as the fetch script: refuse before anything is
    # written. Fail-closed: no --customer-yaml means no vocabulary, which is a
    # refusal, not a pass. --unvalidated-cohort is the loud, explicit bypass
    # for a local tracer run against a slug that has no seat vocabulary yet
    # (--slug is always explicit; the script has no default slug).
    if args.unvalidated_cohort:
        print(
            f"WARNING: cohort '{args.cohort}' NOT validated against any seat vocabulary "
            "(--unvalidated-cohort). Never use this flag for a customer vault.",
            file=sys.stderr,
        )
    else:
        if not args.customer_yaml:
            p.error(
                "provide --customer-yaml so the cohort is validated against the seat's "
                "authored vocabulary, or pass --unvalidated-cohort (tracer/dev only)"
            )
        vocabulary = load_cohort_vocabulary(args.customer_yaml)
        if args.cohort not in vocabulary:
            print(
                f"REFUSED: cohort '{args.cohort}' is not in the seat's authored vocabulary "
                f"{sorted(vocabulary)} ({args.customer_yaml}). An unauthored cohort would "
                "mint a vault directory no profile loader reads. Author it first.",
                file=sys.stderr,
            )
            return 2

    texts = _load_corpus(args.corpus)
    if args.limit:
        texts = texts[: args.limit]

    written = 0
    for text in texts:
        try:
            sample = build_sample(text, slug=args.slug, cohort=args.cohort)
        except VoiceLeakError as e:
            print(f"LEAK BLOCKED — aborting, nothing further written: {e}", file=sys.stderr)
            return 3
        if args.out_dir:
            dest = Path(args.out_dir) / sample.r2_key
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(sample.diff_bytes)
        else:
            _upload_r2(sample.r2_key, sample.diff_bytes)
        written += 1

    target = f"R2 bucket {os.environ.get('R2_BUCKET_CONFIG', '?')}" if args.r2 else args.out_dir
    print(f"ingested {written} content-free samples -> {target} (slug={args.slug} cohort={args.cohort})")
    if args.r2 and written:
        # The Machine syncs the voice vault to its volume only at BOOT (bootstrap
        # Step 2a). A freshly-ingested corpus is therefore INVISIBLE to the
        # running agent until a restart — make that explicit, not a silent lag.
        print(
            "\nNOTE: the running agent only re-reads the voice vault at boot. "
            "Restart the Machine to activate this corpus:\n"
            f"  fly machine restart -a hermes-{args.slug}\n"
            "(or reprovision). Until then the agent uses the previously-synced samples.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Seed the firm-authored voice corpus into the rehearsal office (Smokeball
staging tenant) so the live-scan voice survey has something true to find.

WHY THIS EXISTS. The pilot tenant is ~90% received paper: zero firm letters,
zero firm folders. A correct survey therefore finds nothing there, which is the
right NEGATIVE test and a useless positive one. This script plants a known
firm-authored corpus with a known type/cohort distribution, so the survey's
recall and its authorship classifier can both be measured against ground truth
that we wrote down first.

WHAT IT PLANTS. The 13 fictional Brannock & Ferreira letters under ``voice/``,
re-signed into the live tenant's firm identity, plus three ADVERSARIAL
documents authored inline here that a naive classifier gets wrong:

  1. ``adversarial-received-prosser`` — an OPPOSING firm's letter, opposing
     letterhead and caption, whose ``cc:`` block names our own signer. A
     classifier that says "a roster name appears, therefore we wrote it" calls
     this firm-authored. It is received.
  2. ``adversarial-unsigned-memo`` — a firm-authored internal note with NO
     letterhead and NO signature block. Correct answer is ``unknown``. A
     classifier that treats absence-of-letterhead as evidence-of-receipt calls
     this received, which is the failure that quietly poisons a corpus.
  3. ``adversarial-no-text-layer`` — a graphics-only PDF with no text operators
     at all (a stand-in for a scan). Correct answer is ``unreadable``. "No text
     layer" is NOT "not the firm's writing".

ISOLATION. Every matter this script creates is NEW and dedicated. It never
writes into the seven ``manifest.json`` rehearsal matters, because their
routines' dedup keys are live state that a voice experiment must not disturb.
Bookkeeping goes to ``voice-rehearsal-manifest.json`` — a separate file.
``manifest.json`` is never opened here, for read or write.

FIRM IDENTITY IS NOT GUESSED. ``--firm-name`` and ``--signer`` are required and
have no defaults. The script refuses to run without them and refuses to upload
any document in which a fictional-firm token survives the swap.

Usage::

    cd operator/customers/pilot-smokeball/seed
    python3 seed_voice_letters.py --dry-run --firm-name "..." --signer "..."
    infisical run --env=prod --path=/ss -- python3 seed_voice_letters.py \\
        --firm-name "..." --signer "..." [--signer-2 "..."]
    infisical run --env=prod --path=/ss -- python3 seed_voice_letters.py --remove

Required env (injected by infisical, never echoed) — identical to
``seed_staging.py``, and read by the ``Api`` client imported from it:

    SMOKEBALL_SEED_CLIENT_ID / SMOKEBALL_SEED_CLIENT_SECRET  (App 1)
    SMOKEBALL_STAGING_API_KEY                                (account-scoped)

Idempotent: every created resource is recorded under a stable key and skipped
on re-run. ``--remove`` reverses what the API permits and records the rest as
residue rather than pretending it is gone.

Pure stdlib, like ``seed_staging.py``: runs anywhere without the operator venv.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time

from seed_data import MVA_PLAINTIFF_CA, PI_PLAINTIFF_CA
from seed_staging import Api, text_pdf

HERE = os.path.dirname(os.path.abspath(__file__))
VOICE_DIR = os.path.join(HERE, "voice")
MANIFEST = os.path.join(HERE, "voice-rehearsal-manifest.json")

# Page geometry of seed_staging.text_pdf: 10pt Helvetica at x=54 on a 612pt
# page. ~95 characters is the widest line that stays on the page; anything
# wider runs off the right edge and is lost to a text extractor.
WRAP_WIDTH = 95

# ------------------------------------------------------- fictional identity --

# Every token of the fictional firm that must not survive into the tenant.
# Ordered longest-first: the swap applies them in sequence, so a shorter form
# must never run before a longer one that contains it.
FICTIONAL_FIRM_UPPER = "BRANNOCK & FERREIRA LLP"
FICTIONAL_FIRM_TITLE = "Brannock & Ferreira LLP"
FICTIONAL_FIRM_PROSE = "Brannock and Ferreira"  # 10-engagement-cover-vaught.md:28
FICTIONAL_DOMAIN = "brannockferreira.com"
FICTIONAL_SIGNER_1 = "Dean Brannock"
FICTIONAL_SIGNER_2 = "Luisa Ferreira"
FICTIONAL_EMAIL_1 = "dbrannock@brannockferreira.com"
FICTIONAL_EMAIL_2 = "lferreira@brannockferreira.com"

# The post-swap guard. If any of these survives, the document is not uploaded.
RESIDUE_PATTERN = re.compile(r"brannock|ferreira", re.IGNORECASE)


# ------------------------------------------------------------ the rehearsal --

# One dedicated matter per distinct matter named in the letters' frontmatter.
# Parties are the letters' own fictional parties, so the composed caption a
# survey reads stays coherent with the document bodies inside it.
CONTACTS: dict[str, dict] = {
    "vr-duarte-marisol": {"person": {"firstName": "Marisol", "lastName": "Duarte"}},
    "vr-brennan-kyle": {"person": {"firstName": "Kyle", "lastName": "Brennan"}},
    "vr-copperline": {"company": {"name": "Copperline Logistics, Inc."}},
    "vr-nakashima-errol": {"person": {"firstName": "Errol", "lastName": "Nakashima"}},
    "vr-cornerstone": {"company": {"name": "Cornerstone Market Holdings, LLC"}},
    "vr-tolliver-micah": {"person": {"firstName": "Micah", "lastName": "Tolliver"}},
    "vr-barre-gwendolyn": {"person": {"firstName": "Gwendolyn", "lastName": "Barre"}},
    "vr-boyle-jeanette": {"person": {"firstName": "Jeanette", "lastName": "Boyle"}},
    "vr-trammell": {"company": {"name": "Trammell Logistics, Inc."}},
    "vr-vaught-terrence": {"person": {"firstName": "Terrence", "lastName": "Vaught"}},
    "vr-sentinel-valley": {"company": {"name": "Sentinel Valley Insurance Company"}},
    "vr-ansel-harold": {"person": {"firstName": "Harold", "lastName": "Ansel"}},
}

_REHEARSAL_NOTE = (
    "Synthetic voice-corpus rehearsal matter created by seed_voice_letters.py. "
    "Not a real client matter. Safe to delete."
)

MATTERS: dict[str, dict] = {
    "duarte": {
        "surname": "Duarte",
        "matter_type_id": MVA_PLAINTIFF_CA,
        "clients": ["vr-duarte-marisol"],
        "other_side": ["vr-brennan-kyle", "vr-copperline"],
        "number": "2026-VR-201",
        "opened": "2025-09-02T17:00:00Z",
    },
    "nakashima": {
        "surname": "Nakashima",
        "matter_type_id": PI_PLAINTIFF_CA,
        "clients": ["vr-nakashima-errol"],
        "other_side": ["vr-cornerstone"],
        "number": "2026-VR-202",
        "opened": "2025-12-15T17:00:00Z",
    },
    "tolliver": {
        "surname": "Tolliver",
        "matter_type_id": PI_PLAINTIFF_CA,
        "clients": ["vr-tolliver-micah"],
        "other_side": ["vr-barre-gwendolyn"],
        "number": "2026-VR-203",
        "opened": "2025-10-14T17:00:00Z",
    },
    "boyle": {
        "surname": "Boyle",
        "matter_type_id": MVA_PLAINTIFF_CA,
        "clients": ["vr-boyle-jeanette"],
        "other_side": ["vr-trammell"],
        "number": "2026-VR-204",
        "opened": "2025-07-21T17:00:00Z",
    },
    "vaught": {
        "surname": "Vaught",
        "matter_type_id": MVA_PLAINTIFF_CA,
        "clients": ["vr-vaught-terrence"],
        "other_side": ["vr-sentinel-valley"],
        "number": "2026-VR-205",
        "opened": "2026-04-27T17:00:00Z",
    },
    "ansel": {
        "surname": "Ansel",
        "matter_type_id": PI_PLAINTIFF_CA,
        "clients": ["vr-ansel-harold"],
        "other_side": [],
        "number": "2026-VR-206",
        "opened": "2025-06-09T17:00:00Z",
    },
}

# Frontmatter ``matter:`` strings are prose, not keys. This table is the only
# place they are resolved, and an unmapped letter is a hard error rather than a
# silent skip — a letter added to voice/ without a home must fail loudly.
MATTER_BY_FRONTMATTER: dict[str, str] = {
    "Duarte v. Brennan": "duarte",
    "Nakashima v. Cornerstone": "nakashima",
    "Tolliver, a minor": "tolliver",
    "Boyle v. Trammell": "boyle",
    "Vaught v. unknown driver": "vaught",
    "Ansel, potential claim": "ansel",
}

# ``audience:`` prose -> the cohort vocabulary pilot-smokeball actually
# authors. ``None`` means the audience has NO authored cohort on this seat: the
# mediator/neutral class is the open item in Captain decision #5. Recording it
# as null is the point. Inventing a cohort here would manufacture the very
# incoherence the survey is supposed to surface.
COHORT_BY_AUDIENCE_PREFIX: dict[str, str | None] = {
    "claims adjuster": "adjuster",
    "client": "client",
    "prospective client": "client",
    "opposing counsel": "opposing-counsel",
    "neutral": None,
}

# The distribution the mining proof depends on. Checked before any upload, so a
# corpus that cannot support the proof is refused rather than half-planted.
# mediation_brief_excerpt is pinned at exactly 2 as the below-threshold
# falsifier: a miner that proposes a template for it is over-fitting.
TYPE_FLOOR: dict[str, tuple[int, int]] = {  # doc_type -> (min docs, min matters)
    "demand_letter": (3, 3),
    "client_status_letter": (4, 4),
}
TYPE_EXACT: dict[str, int] = {"mediation_brief_excerpt": 2}


# ------------------------------------------------------------- text helpers --


def read_letter(path: str) -> tuple[dict[str, str], str]:
    """Split a voice letter into (frontmatter, body). Frontmatter is the block
    between the first two ``---`` fences; the body is everything after."""
    with open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if not raw.startswith("---\n"):
        raise ValueError(f"{os.path.basename(path)}: no frontmatter fence")
    end = raw.index("\n---\n", 3)
    meta: dict[str, str] = {}
    for line in raw[4:end].splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta, raw[end + 5 :].lstrip("\n")


def normalize_doc_type(raw: str) -> str:
    """``mediation_brief_excerpt (statement of facts)`` -> ``mediation_brief_excerpt``."""
    return raw.split(" (", 1)[0].strip()


def resolve(table: dict, value: str, what: str):
    for prefix, resolved in table.items():
        if value.startswith(prefix):
            return resolved
    raise ValueError(f"unmapped {what}: {value!r} — add it to the table in this script")


def swap_identity(text: str, ident: dict[str, str]) -> str:
    """Replace every fictional-firm token with the live tenant's identity.

    Applied longest-first. The two fictional signatories map to two live
    signers so the corpus keeps its two-author signal, which is what lets the
    survey's ``signature in get_staff roster`` test mean anything."""
    pairs = [
        (FICTIONAL_EMAIL_1, ident["email_1"]),
        (FICTIONAL_EMAIL_2, ident["email_2"]),
        (FICTIONAL_DOMAIN, ident["domain"]),
        (FICTIONAL_FIRM_UPPER, ident["firm_upper"]),
        (FICTIONAL_FIRM_TITLE, ident["firm"]),
        (FICTIONAL_FIRM_PROSE, ident["firm"]),
        (FICTIONAL_SIGNER_1, ident["signer_1"]),
        (FICTIONAL_SIGNER_2, ident["signer_2"]),
    ]
    for old, new in pairs:
        text = text.replace(old, new)
    return text


def assert_no_residue(text: str, label: str) -> None:
    """Refuse to upload a document still carrying the fictional firm. This is
    the falsifier for the swap: it can fail, and a bare surname somewhere in a
    letter body is exactly how it would."""
    hit = RESIDUE_PATTERN.search(text)
    if hit:
        line = text[: hit.start()].count("\n") + 1
        raise RuntimeError(
            f"{label}: fictional-firm token {hit.group(0)!r} survived the identity "
            f"swap at body line {line} — refusing to upload. Extend the swap table."
        )


def md_to_pdf_lines(body: str) -> list[str]:
    """Flatten letter markdown to plain lines that fit the page.

    Bold markers are dropped, table rows pass through intact (wrapping them
    would shred the columns), and prose is wrapped at the page width so no
    text runs off the right edge where an extractor cannot reach it."""
    out: list[str] = []
    for line in body.replace("\r\n", "\n").split("\n"):
        flat = line.replace("**", "").replace("__", "").rstrip()
        if not flat.strip():
            out.append("")
        elif flat.lstrip().startswith("|"):
            out.append(flat)
        else:
            out.extend(textwrap.wrap(flat, width=WRAP_WIDTH) or [""])
    return out


def image_like_pdf() -> bytes:
    """A one-page PDF with NO text operators and no font resource — grey blocks
    only, the shape a scanned page has to a text extractor. Deliberately not
    ``text_pdf([])``, which still emits a text object and would let a classifier
    pass by finding an empty string where it should find nothing at all."""
    blocks = ["0.85 g"]
    y = 700
    for _ in range(14):
        blocks.append(f"72 {y} {380 + (y % 7) * 12} 9 re f")
        y -= 22
    stream = "\n".join(blocks).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


# ------------------------------------------------------- adversarial bodies --


def adversarial_received(ident: dict[str, str]) -> list[str]:
    """Trap 1: an OPPOSING firm's letter whose cc block names our own signer.
    Correct classification is ``received``. A classifier keyed on "a roster
    name appears in the document" answers firm_authored and is wrong."""
    return [
        "PROSSER, NAKAGAWA & BELL LLP",
        "601 S. Figueroa Street, 28th Floor",
        "Los Angeles, California 90017",
        "(213) 555-0144",
        "",
        "July 20, 2026",
        "",
        "VIA EMAIL",
        "",
        f"{ident['signer_1']}",
        f"{ident['firm']}",
        "",
        "RE: Boyle v. Trammell Logistics, Inc., LASC Case No. 25STCV41182",
        "    Response to your meet and confer correspondence of July 6, 2026",
        "",
        f"Dear {ident['signer_1'].split()[0]}:",
        "",
        "I have your letter of July 6 regarding Trammell's responses to Form",
        "Interrogatories, Set One, Special Interrogatories, Set One, and Requests",
        "for Production, Set One. My client disagrees with your characterization",
        "of the responses as boilerplate, and I want to set out why before either",
        "of us spends a motion on this.",
        "",
        "As to Form Interrogatory 20.2, the traffic collision report is the factual",
        "basis of my client's contention, and a party may adopt a document as its",
        "response where the document states the facts. As to 20.10, we will",
        "supplement with the vehicle maintenance file within thirty days.",
        "",
        "We do not intend to supplement the responses to Requests for Production",
        "Nos. 4 through 9. The objection on privilege grounds stands, and a",
        "privilege log will follow under separate cover.",
        "",
        "I am available Thursday afternoon if a call would move this along.",
        "",
        "Very truly yours,",
        "",
        "Alan Prosser",
        "PROSSER, NAKAGAWA & BELL LLP",
        "aprosser@prossernakagawabell.example",
        "",
        f"cc: {ident['signer_1']}, {ident['firm']}",
        f"cc: {ident['signer_2']}, {ident['firm']}",
        "cc: Claims file, Trammell Logistics, Inc.",
    ]


def adversarial_unsigned_memo() -> list[str]:
    """Trap 2: firm-authored, but no letterhead and no signature block. The
    correct answer is ``unknown`` — there is no authorship evidence either way.
    A classifier that reads absence-of-letterhead as receipt answers received,
    which is how received paper quietly contaminates a voice corpus."""
    return [
        "CASE POSTURE NOTE",
        "Nakashima / Cornerstone Market",
        "Updated after the June 8 client call",
        "",
        "Where the file stands",
        "",
        "Demand went out May 12. Bayline has it and has not responded. The",
        "thirty day window in the letter has run. Nothing about that is unusual",
        "on a premises file of this size.",
        "",
        "The four video stills are the strongest evidence in the file. Bottle",
        "breaks at 5:41. Employee passes at 5:49. Same employee passes again at",
        "6:02. Client falls at 6:15. That is twenty four minutes of notice with",
        "two walk-bys, and it is the whole liability case.",
        "",
        "Open items",
        "",
        "1. Lien itemization from the health plan. Requested; not received.",
        "   Do not open lien negotiation until a settlement number exists.",
        "2. Employment records for the 2027 shop assignment, if the district",
        "   puts anything in writing.",
        "3. Annual knee follow-up with the treating orthopedist. Permanent",
        "   extensor lag; the contralateral knee is now load-bearing.",
        "",
        "Decision point",
        "",
        "If July 15 arrives with silence, file. The alternative is being strung",
        "through the summer while the statute ages. A first offer between",
        "fifteen and thirty percent of demand is the expected outcome and is not",
        "a reason to reconsider filing.",
        "",
        "Do not take a fast number to hit a September date. The knee is",
        "permanent and the offer has to reflect that.",
    ]


_NOT_A_CANDIDATE = "n/a (not a voice-corpus candidate)"


def adversarial_docs(ident: dict[str, str]) -> dict[str, dict]:
    """The three synthetic traps, keyed like the letters so the manifest and
    the teardown treat them identically.

    Their cohort is ``None`` for a DIFFERENT reason than a mediation brief's:
    these are not corpus candidates at all, so there is no cohort to author.
    ``cohort_status`` keeps the two cases from reading alike in the manifest."""
    return {
        "adversarial-received-prosser": {
            "matter": "boyle",
            "file_name": "2026-07-20 Letter from Prosser re meet and confer - Boyle.pdf",
            "doc_type": "received_correspondence",
            "audience": "n/a (inbound)",
            "cohort": None,
            "cohort_status": _NOT_A_CANDIDATE,
            "expected_classification": "received",
            "source": "synthetic (authored in seed_voice_letters.py)",
            "trap": "cc block names our own signer; letterhead and signature are the opposing firm's",
            "blob": lambda: text_pdf(adversarial_received(ident)),
        },
        "adversarial-unsigned-memo": {
            "matter": "nakashima",
            "file_name": "2026-06-09 Case posture note - Nakashima.pdf",
            "doc_type": "internal_note",
            "audience": "internal",
            "cohort": None,
            "cohort_status": _NOT_A_CANDIDATE,
            "expected_classification": "unknown",
            "source": "synthetic (authored in seed_voice_letters.py)",
            "trap": "firm-authored but carries no letterhead and no signature block",
            "blob": lambda: text_pdf(adversarial_unsigned_memo()),
        },
        "adversarial-no-text-layer": {
            "matter": "tolliver",
            "file_name": "2025-10-02 Animal Care incident report (scan) - Tolliver.pdf",
            "doc_type": "scanned_record",
            "audience": "n/a (scan)",
            "cohort": None,
            "cohort_status": _NOT_A_CANDIDATE,
            "expected_classification": "unreadable",
            "source": "synthetic (authored in seed_voice_letters.py)",
            "trap": "graphics-only PDF: no text operators, no font resource",
            "blob": image_like_pdf,
        },
    }


# ------------------------------------------------------------------ the plan --


def build_plan(ident: dict[str, str]) -> dict[str, dict]:
    """doc_key -> everything needed to upload it and to write it down.

    Reads and transforms every letter up front so a bad swap fails before the
    first network call rather than halfway through the tenant."""
    plan: dict[str, dict] = {}
    names = sorted(f for f in os.listdir(VOICE_DIR) if re.match(r"^\d\d-.*\.md$", f))
    if not names:
        sys.exit(f"no voice letters found under {VOICE_DIR}")
    for name in names:
        meta, body = read_letter(os.path.join(VOICE_DIR, name))
        key = os.path.splitext(name)[0]
        matter_key = resolve(MATTER_BY_FRONTMATTER, meta["matter"], "matter")
        cohort = resolve(COHORT_BY_AUDIENCE_PREFIX, meta["audience"], "audience")
        swapped = swap_identity(body, ident)
        assert_no_residue(swapped, key)
        plan[key] = {
            "matter": matter_key,
            "file_name": f"{key}.pdf",
            "doc_type": normalize_doc_type(meta["doc_type"]),
            "audience": meta["audience"],
            "cohort": cohort,
            "cohort_status": (
                "authored" if cohort else "UNAUTHORED on this seat (audience has no cohort; Captain decision #5)"
            ),
            "expected_classification": "firm_authored",
            "source": f"voice/{name}",
            "trap": None,
            "blob": (lambda b=swapped: text_pdf(md_to_pdf_lines(b))),
        }
    plan.update(adversarial_docs(ident))
    return plan


def distribution(plan: dict[str, dict]) -> dict[str, dict]:
    """doc_type -> {count, matters:set} over the firm-authored planted docs."""
    out: dict[str, dict] = {}
    for spec in plan.values():
        if spec["expected_classification"] != "firm_authored":
            continue
        row = out.setdefault(spec["doc_type"], {"count": 0, "matters": set()})
        row["count"] += 1
        row["matters"].add(spec["matter"])
    return out


def check_distribution(dist: dict[str, dict]) -> list[str]:
    """The mining proof's preconditions, as failures rather than assumptions."""
    problems = []
    for doc_type, (min_docs, min_matters) in TYPE_FLOOR.items():
        row = dist.get(doc_type, {"count": 0, "matters": set()})
        if row["count"] < min_docs or len(row["matters"]) < min_matters:
            problems.append(
                f"{doc_type}: {row['count']} docs across {len(row['matters'])} matters, "
                f"need >={min_docs} across >={min_matters}"
            )
    for doc_type, exact in TYPE_EXACT.items():
        row = dist.get(doc_type, {"count": 0, "matters": set()})
        if row["count"] != exact:
            problems.append(f"{doc_type}: {row['count']} docs, need exactly {exact} (the below-threshold falsifier)")
    return problems


def print_distribution(plan: dict[str, dict]) -> list[str]:
    dist = distribution(plan)
    print("\n  planted firm-authored distribution")
    print(f"  {'doc_type':<28} {'docs':>5} {'matters':>8}  matter keys")
    for doc_type in sorted(dist):
        row = dist[doc_type]
        keys = ",".join(sorted(row["matters"]))
        print(f"  {doc_type:<28} {row['count']:>5} {len(row['matters']):>8}  {keys}")
    other = [k for k, v in plan.items() if v["expected_classification"] != "firm_authored"]
    for key in sorted(other):
        spec = plan[key]
        print(f"  [adversarial] {key} -> {spec['matter']} (expect {spec['expected_classification']})")
    problems = check_distribution(dist)
    for problem in problems:
        print(f"  FAIL {problem}")
    return problems


# ----------------------------------------------------------------- manifest --


def load_manifest() -> dict:
    if os.path.exists(MANIFEST):
        with open(MANIFEST) as fh:
            return json.load(fh)
    return {
        "created_at": None,
        "firm_identity": {},
        "contacts": {},
        "matters": {},
        "documents": {},
        "removal": {},
    }


def save_manifest(m: dict) -> None:
    with open(MANIFEST, "w") as fh:
        json.dump(m, fh, indent=2, sort_keys=True)
        fh.write("\n")


# --------------------------------------------------------------------- seed --


def do_seed(api: Api, plan: dict[str, dict], ident: dict[str, str]) -> None:
    manifest = load_manifest()
    manifest["created_at"] = manifest.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["firm_identity"] = {
        "firm_name": ident["firm"],
        "signer_1": ident["signer_1"],
        "signer_2": ident["signer_2"],
        "domain": ident["domain"],
        "note": (
            "Letterhead and signatures were swapped to this identity. Street "
            "addresses and telephone numbers in the bodies remain the FICTIONAL "
            "ones from the corpus — inventing real ones would be fabrication."
        ),
    }

    for key, spec in CONTACTS.items():
        if key in manifest["contacts"]:
            print(f"contact {key}: exists ({manifest['contacts'][key]})")
            continue
        created = api.create_async("/contacts", {**spec, "externalSystemId": f"smd-voice-{key}"}, f"contact {key}")
        manifest["contacts"][key] = created["id"]
        save_manifest(manifest)
        print(f"contact {key}: created {created['id']}")

    for key, spec in MATTERS.items():
        if key in manifest["matters"]:
            print(f"matter {key}: exists ({manifest['matters'][key]})")
            continue
        body = {
            "matterTypeId": spec["matter_type_id"],
            "clientIds": [manifest["contacts"][c] for c in spec["clients"]],
            "otherSideIds": [manifest["contacts"][c] for c in spec["other_side"]],
            "status": "Open",
            "number": spec["number"],
            "description": f"SMD Voice Rehearsal — {spec['surname']}. {_REHEARSAL_NOTE}",
            "openedDate": spec["opened"],
        }
        created = api.create_async("/matters", body, f"matter {key}")
        manifest["matters"][key] = created["id"]
        save_manifest(manifest)
        print(f"matter {key}: created {created['id']} (number {spec['number']})")

    for key in sorted(plan):
        spec = plan[key]
        if key in manifest["documents"]:
            print(f"document {key}: exists ({manifest['documents'][key]['file_id']})")
            continue
        matter_id = manifest["matters"][spec["matter"]]
        file_id = api.upload_document(matter_id, spec["file_name"], spec["blob"]())
        manifest["documents"][key] = {
            "matter": spec["matter"],
            "matter_id": matter_id,
            "file_id": file_id,
            "file_name": spec["file_name"],
            "doc_type": spec["doc_type"],
            "audience": spec["audience"],
            "cohort": spec["cohort"],
            "cohort_status": spec["cohort_status"],
            "expected_classification": spec["expected_classification"],
            "source": spec["source"],
            "trap": spec["trap"],
        }
        save_manifest(manifest)
        print(f"document {key}: uploaded {spec['file_name']} ({file_id})")

    print(
        f"\nDONE: {len(manifest['contacts'])} contacts, {len(manifest['matters'])} matters, "
        f"{len(manifest['documents'])} documents (voice-rehearsal-manifest.json updated)"
    )


# ------------------------------------------------------------------- remove --


def retire_matter(api: Api, matter_id: str, try_delete: bool) -> tuple[bool, list[str]]:
    """Best effort, honestly reported.

    ``delete_file`` is a documented connector verb (client.py:486). There is NO
    matter-delete verb anywhere in this repo, and none has been exercised
    against this tenant. What IS in the vendor's own vocabulary is a ``Deleted``
    matter status (server.py:386 lists Open|Pending|Closed|Deleted|Cancelled),
    so the ladder starts with a status write. ``DELETE /matters/{id}`` is an
    unverified destructive verb and stays behind --try-matter-delete."""
    trail: list[str] = []
    for method in ("PATCH", "PUT"):
        code, _ = api.call(method, f"/matters/{matter_id}", {"status": "Deleted"})
        trail.append(f"{method} /matters/{{id}} status=Deleted -> {code}")
        if code in (200, 202, 204):
            return True, trail
    if try_delete:
        code, _ = api.call("DELETE", f"/matters/{matter_id}")
        trail.append(f"DELETE /matters/{{id}} -> {code}")
        if code in (200, 202, 204):
            return True, trail
    return False, trail


def do_remove(keep_matters: bool, try_delete: bool) -> None:
    # Manifest first: no point spending a token exchange to discover there is
    # nothing recorded to tear down.
    if not os.path.exists(MANIFEST):
        sys.exit(f"nothing to remove: {MANIFEST} does not exist")
    manifest = load_manifest()
    if not manifest.get("documents") and not manifest.get("matters"):
        sys.exit(f"nothing to remove: {MANIFEST} records no documents or matters")
    api = Api()
    removal = {
        "removed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "files_deleted": [],
        "files_failed": [],
        "matters_retired": [],
        "matters_residual": [],
        "contacts_residual": [],
    }

    for key in sorted(manifest.get("documents", {})):
        row = manifest["documents"][key]
        code, _ = api.call("DELETE", f"/matters/{row['matter_id']}/documents/files/{row['file_id']}")
        # 404 counts as gone: the goal is absence, not the privilege of causing it.
        if code in (200, 202, 204, 404):
            removal["files_deleted"].append({"key": key, "file_id": row["file_id"], "http": code})
            print(f"document {key}: deleted ({code})")
        else:
            removal["files_failed"].append({"key": key, "file_id": row["file_id"], "http": code})
            print(f"document {key}: DELETE returned {code} — left in place")

    for key in sorted(manifest.get("matters", {})):
        matter_id = manifest["matters"][key]
        if keep_matters:
            removal["matters_residual"].append({"key": key, "id": matter_id, "reason": "--keep-matters was passed"})
            continue
        retired, trail = retire_matter(api, matter_id, try_delete)
        if retired:
            removal["matters_retired"].append({"key": key, "id": matter_id, "trail": trail})
            print(f"matter {key}: retired ({trail[-1]})")
        else:
            removal["matters_residual"].append(
                {
                    "key": key,
                    "id": matter_id,
                    "reason": "no matter-removal verb succeeded",
                    "trail": trail,
                    "state": (
                        "emptied of seeded documents; still present on the tenant, "
                        f"labelled 'SMD Voice Rehearsal — {MATTERS[key]['surname']}'"
                    ),
                }
            )
            print(f"matter {key}: NOT removable ({'; '.join(trail)}) — left, emptied and labelled")

    for key, contact_id in sorted(manifest.get("contacts", {}).items()):
        removal["contacts_residual"].append(
            {
                "key": key,
                "id": contact_id,
                "reason": (
                    "contacts are not deleted: they are referenced by the matters and no "
                    "contact-removal verb is exercised anywhere in this repo. Marked with "
                    f"externalSystemId smd-voice-{key}."
                ),
            }
        )

    manifest["removal"] = removal
    save_manifest(manifest)
    residue = len(removal["matters_residual"]) + len(removal["contacts_residual"])
    print(
        f"\nREMOVE: {len(removal['files_deleted'])} files deleted, "
        f"{len(removal['files_failed'])} failed, "
        f"{len(removal['matters_retired'])} matters retired, "
        f"{residue} residual records written to voice-rehearsal-manifest.json"
    )
    if removal["matters_residual"] or removal["files_failed"]:
        print("Residue is real. Read the manifest before claiming this tenant is clean.")


# --------------------------------------------------------------------- main --


def build_identity(args: argparse.Namespace) -> dict[str, str]:
    signer_1 = args.signer.strip()
    signer_2 = (args.signer_2 or args.signer).strip()
    firm = args.firm_name.strip()
    domain = (args.firm_domain or re.sub(r"[^a-z0-9]", "", firm.lower()) + ".com").strip()

    def email(name: str) -> str:
        parts = name.split()
        first = parts[0][0].lower() if parts else "x"
        last = re.sub(r"[^a-z]", "", parts[-1].lower()) if parts else "staff"
        return f"{first}{last}@{domain}"

    return {
        "firm": firm,
        "firm_upper": firm.upper(),
        "signer_1": signer_1,
        "signer_2": signer_2,
        "domain": domain,
        "email_1": email(signer_1),
        "email_2": email(signer_2),
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--firm-name", help="live tenant's firm name; REQUIRED to seed, never guessed")
    p.add_argument("--signer", help="real staff member who signs; REQUIRED to seed")
    p.add_argument("--signer-2", help="second real staff member (the corpus has two signatories)")
    p.add_argument("--firm-domain", help="email domain; derived from --firm-name if omitted")
    p.add_argument("--dry-run", action="store_true", help="print the plan; no network calls")
    p.add_argument("--remove", action="store_true", help="tear down what the manifest records")
    p.add_argument("--keep-matters", action="store_true", help="--remove: delete files only")
    p.add_argument(
        "--try-matter-delete",
        action="store_true",
        help="--remove: escalate to DELETE /matters/{id}, an unverified destructive verb",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if args.remove:
        if args.dry_run:
            sys.exit("--remove and --dry-run are mutually exclusive")
        do_remove(args.keep_matters, args.try_matter_delete)
        return

    if not (args.firm_name and args.signer):
        sys.exit(
            "--firm-name and --signer are required and have no defaults. The corpus "
            "carries a fictional firm's letterhead and signatures; seeding it without "
            "the live tenant's real identity would plant a firm that does not exist. "
            "Never guess these."
        )

    ident = build_identity(args)
    plan = build_plan(ident)

    print(f"firm identity: {ident['firm']} / {ident['signer_1']} + {ident['signer_2']} / {ident['domain']}")
    if not args.signer_2:
        print(
            "  NOTE: --signer-2 not given, so both fictional signatories collapse onto "
            f"{ident['signer_1']}. The corpus is two-authored; planting it single-authored "
            "removes the two-signatory signal the survey's roster test relies on. Pass a "
            "second real staff name unless you mean to flatten it."
        )
    print(f"matters to create: {len(MATTERS)} ({', '.join(MATTERS)})")
    print(f"contacts to create: {len(CONTACTS)}")
    print(f"documents to upload: {len(plan)}")
    problems = print_distribution(plan)

    if args.dry_run:
        print("\n  transforms applied to each letter:")
        print("    1. frontmatter stripped (body only)")
        print("    2. firm identity swapped (letterhead, signature, prose mention, emails)")
        print("    3. post-swap residue guard (upload refused if a fictional token survives)")
        print("    4. markdown flattened and wrapped to the page width")
        print("\n  per-document plan:")
        for key in sorted(plan):
            spec = plan[key]
            print(
                f"    {key:<34} -> {spec['matter']:<10} {spec['doc_type']:<26} "
                f"{spec['expected_classification']:<14} "
                f"cohort={spec['cohort'] or spec['cohort_status']}"
            )
        print("\nDRY RUN: no network calls made, nothing created.")
        if problems:
            sys.exit("distribution preconditions FAIL (see above)")
        return

    if problems:
        sys.exit(
            "\nrefusing to seed: the planted distribution cannot support the mining "
            "proof (see FAIL lines above). Fix the corpus or the thresholds first."
        )
    do_seed(Api(), plan, ident)


if __name__ == "__main__":
    main()

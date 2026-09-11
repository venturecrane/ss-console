"""Tests for bin/voice-survey-corpus.py — the tenant scan that proposes a corpus.

The survey's whole job is to separate the letters a firm WROTE from the far
larger pile it RECEIVED, using nothing but document text (there is no
authorship metadata on the connector's surface). The failure that matters is
the false POSITIVE: a received letter admitted to the corpus teaches the model
an opposing firm's or a carrier's register, and it does that silently. So the
fixture tenant here is built out of traps rather than happy paths:

* a cc'd RECEIVED letter whose tail names one of our own attorneys — the
  commonest false positive, and the reason letterhead outranks the signature
  block. The test asserts the trap is LIVE (the roster matcher really does find
  her in that tail) before asserting the classifier resists it, because a trap
  that never springs proves nothing.
* a demand letter to a claims adjuster, which must NOT land in the `client`
  cohort even though its body is full of the client's name.
* a document with no text layer, which must come back `unknown` and never
  `received` — an unreadable scan is not evidence about who wrote it.
* a firm letter whose letterhead did not survive extraction, which must come
  back `unknown` rather than be guessed either way.

No network: the fake client exposes the two methods the survey uses (`get`,
`download_file`), the same double shape as test_voice_fetch_corpus.py.

Run::

    cd operator && python3 -m pytest bin/tests/test_voice_survey_corpus.py -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_OPERATOR = _HERE.parents[2]
sys.path.insert(0, str(_OPERATOR))

_spec = importlib.util.spec_from_file_location("voice_survey_corpus", _OPERATOR / "bin" / "voice-survey-corpus.py")
assert _spec and _spec.loader
vsc = importlib.util.module_from_spec(_spec)
# Register before exec: @dataclass resolves its class's module out of
# sys.modules (AttributeError at import time on 3.12+ otherwise).
sys.modules["voice_survey_corpus"] = vsc
_spec.loader.exec_module(vsc)


FIRM = "Brannock & Ferreira LLP"
VOCAB = frozenset({"client", "adjuster", "opposing-counsel"})
STAFF = [
    {"id": "s-1", "firstName": "Dean", "lastName": "Brannock"},
    {"id": "s-2", "firstName": "Luisa", "lastName": "Ferreira"},
]

_LETTERHEAD = """**BRANNOCK & FERREIRA LLP**
Trial Lawyers
3400 E. Broadway, Suite 610
Long Beach, California 90803
(562) 555-0170
"""

# --- firm-authored: demand letter to a claims adjuster ----------------------
DEMAND = (
    _LETTERHEAD
    + """
March 3, 2026

**VIA EMAIL AND CERTIFIED MAIL**

Denise Whitcomb
Senior Claims Representative
Meridian Pacific Casualty
P.O. Box 74119
Ontario, CA 91761

RE: Your insureds: Kyle Brennan and Copperline Logistics, Inc.
Claim No.: MPC-2025-448216
Our client: Marisol Duarte

Dear Ms. Whitcomb:

Your driver ran a red light and hit Marisol Duarte in the driver's door.

We represent Ms. Duarte. This is her demand.

This demand is open until April 2, 2026. On April 3 we file.

Very truly yours,

**Dean Brannock**
BRANNOCK & FERREIRA LLP
dbrannock@brannockferreira.com
"""
)

# --- firm-authored: client status letter ------------------------------------
CLIENT_STATUS = (
    _LETTERHEAD
    + """
June 8, 2026

Errol Nakashima
2277 Ximeno Avenue, Apt. 3
Long Beach, CA 90815

RE: Your case against Cornerstone Market

Dear Errol,

The demand went out May 12. Here is where things actually stand.

Yours,

**Luisa Ferreira**
BRANNOCK & FERREIRA LLP
lferreira@brannockferreira.com
"""
)

# --- firm-authored, letterhead lost in extraction (scan of page 2 onward) ----
CLIENT_STATUS_NO_LETTERHEAD = """June 12, 2026

Marguerite Boyle
910 Termino Avenue
Long Beach, CA 90804

RE: Your case against Trammell Logistics

Dear Marguerite,

Here is where things stand after the deposition.

Yours,

Luisa Ferreira
lferreira@brannockferreira.com
"""

# --- firm-authored: mediation brief addressed to a neutral ------------------
# Carries a caption line whose defendant is an LLC. A caption names PARTIES; if
# that corporate suffix were read as the addressee's, this brief would be filed
# as a letter to opposing counsel instead of surfacing as an unmapped cohort.
MEDIATION_BRIEF = (
    _LETTERHEAD
    + """
July 1, 2026

Hon. Rita Salgado (Ret.), Mediator
Pacific Dispute Resolution

PLAINTIFF'S CONFIDENTIAL MEDIATION BRIEF

Nakashima v. Cornerstone Market Holdings, LLC

STATEMENT OF FACTS

The floor had been mopped and left unmarked for nineteen minutes.

Respectfully submitted,

**Dean Brannock**
BRANNOCK & FERREIRA LLP
"""
)

# --- received: served discovery under another declarant's proof of service ---
SERVED_DISCOVERY = """SUPERIOR COURT OF CALIFORNIA, COUNTY OF LOS ANGELES

MARIA ALVAREZ,
                    Plaintiff,
        vs.
KENNETH DRAPER,
                    Defendant(s).

Case No.: 24STCV18223

                DEFENDANT'S REQUESTS FOR PRODUCTION OF DOCUMENTS, SET ONE

PROPOUNDING PARTY: Defendant
RESPONDING PARTY:  Plaintiff

REQUEST FOR PRODUCTION NO. 1:
    All DOCUMENTS relating to the INCIDENT described in the complaint.

                     PROOF OF SERVICE

I, the undersigned, declare that I am over the age of eighteen years
and not a party to the within action.

[X] BY MAIL: I deposited the sealed envelope with the United States
Postal Service, postage fully prepaid.

Executed on June 20, 2026, at Los Angeles, California.

                              /s/ D. Whitmore
                              D. Whitmore
"""

# --- received: THE CC TRAP --------------------------------------------------
# Another firm's letter. Its tail names one of OUR attorneys (she was copied),
# which is exactly the signal that would otherwise mark it firm-authored.
CC_TRAP = """TRAMMELL & VOSS LLP
Attorneys at Law
800 Wilshire Boulevard, Suite 1200
Los Angeles, California 90017
(213) 555-0142

June 12, 2026

Kevin Ancelet
Claims Counsel
Bayline Insurance Group

RE: Boyle v. Trammell Logistics

Dear Mr. Ancelet:

We write to confirm our position on the deposition schedule.

Very truly yours,

Marcus Voss
TRAMMELL & VOSS LLP

cc: Luisa Ferreira, Brannock & Ferreira LLP
"""

# --- received: carrier paper ------------------------------------------------
CARRIER_OFFER = """WESTERN MUTUAL INSURANCE - CLAIMS DEPARTMENT
P.O. Box 3300
Sacramento, CA 95812

Re: Claim 26-44812-K / Sofia Ramirez (minor) v. Daniel Ortiz

Dear Counsel:

On behalf of our insured, we extend an offer of $45,000.00 in full
and final settlement of all claims of the minor.
"""


def _doc(matter, matter_name, file_id, name, body, **advisory):
    row = {
        "matter_id": matter,
        "matter_name": matter_name,
        "file_id": file_id,
        "name": name,
        "body": body,
    }
    row.update(advisory)
    return row


# matter_id -> matter name
MATTERS = {
    "m-1": "Duarte v. Brennan and Copperline Logistics, Inc.",
    "m-2": "Nakashima v. Cornerstone Market Holdings, LLC",
    "m-3": "Boyle v. Trammell Logistics, Inc.",
    "m-4": "Ramirez v. Ortiz",
}

DOCS = [
    _doc(
        "m-1",
        MATTERS["m-1"],
        "f-1",
        "2026-03-03 Demand - Duarte.pdf",
        DEMAND,
        ownerId="S2S_hermes",
        isUploaded=False,
        dateCreated="2026-03-03T09:00:00Z",
    ),
    _doc(
        "m-1",
        MATTERS["m-1"],
        "f-2",
        "2026-07-01 Mediation Brief - Nakashima.pdf",
        MEDIATION_BRIEF,
        ownerId="u-77",
        isUploaded=True,
    ),
    _doc(
        "m-2",
        MATTERS["m-2"],
        "f-3",
        "2026-06-08 Client status - Nakashima.pdf",
        CLIENT_STATUS,
        ownerId="u-77",
        isUploaded=False,
    ),
    _doc(
        "m-2",
        MATTERS["m-2"],
        "f-4",
        "2026-06-20 RFP Set One - Draper to Alvarez.pdf",
        SERVED_DISCOVERY,
        ownerId="u-12",
        isUploaded=True,
    ),
    _doc(
        "m-3",
        MATTERS["m-3"],
        "f-5",
        "2026-06-12 Letter from Trammell & Voss.pdf",
        CC_TRAP,
        ownerId="u-12",
        isUploaded=True,
    ),
    _doc(
        "m-3",
        MATTERS["m-3"],
        "f-6",
        "2026-06-12 Client status - Boyle.pdf",
        CLIENT_STATUS_NO_LETTERHEAD,
        ownerId="u-77",
        isUploaded=False,
    ),
    _doc(
        "m-4",
        MATTERS["m-4"],
        "f-7",
        "2026-06-26 Settlement Offer - carrier.pdf",
        CARRIER_OFFER,
        ownerId="u-12",
        isUploaded=True,
    ),
    # Image-only scan: extracts to nothing.
    _doc(
        "m-4",
        MATTERS["m-4"],
        "f-8",
        "2026-06-10 Medical Records (scan).pdf",
        "   \n  ",
        ownerId="u-12",
        isUploaded=True,
    ),
]


class FakeClient:
    """Stands in for SmokeballClient: the two methods the survey calls."""

    def __init__(self, docs=DOCS, matters=None, staff=STAFF):
        self._docs = docs
        self._matters = matters or [{"id": mid, "name": name} for mid, name in MATTERS.items()]
        self._staff = staff
        self.downloaded: list[tuple[str, str]] = []

    @staticmethod
    def _envelope(rows, limit, offset):
        page = rows[offset : offset + limit]
        return {"value": page, "offset": offset, "limit": limit, "size": len(rows)}

    def get(self, path, **params):
        limit = int(params.get("Limit", 500))
        offset = int(params.get("Offset", 0))
        if path == "/matters":
            return self._envelope(self._matters, limit, offset)
        if path == "/staff":
            return self._envelope(self._staff, limit, offset)
        if path.startswith("/matters/") and path.endswith("/documents/files"):
            matter_id = path.split("/")[2]
            rows = [
                {
                    "id": d["file_id"],
                    "name": d["name"],
                    "fileExtension": "txt",
                    "ownerId": d.get("ownerId"),
                    "isUploaded": d.get("isUploaded"),
                    "dateCreated": d.get("dateCreated"),
                    "dateModified": d.get("dateModified"),
                    "sizeBytes": len(d["body"]),
                }
                for d in self._docs
                if d["matter_id"] == matter_id
            ]
            return self._envelope(rows, limit, offset)
        raise AssertionError(f"unexpected GET {path}")

    def download_file(self, matter_id, file_id):
        self.downloaded.append((matter_id, file_id))
        for d in self._docs:
            if d["file_id"] == file_id:
                return (
                    {"name": d["name"], "fileExtension": "txt"},
                    d["body"].encode("utf-8"),
                )
        raise AssertionError(f"unknown file {file_id}")


@pytest.fixture()
def report_and_entries():
    report, entries = vsc.survey(
        FakeClient(),
        firm_name=FIRM,
        vocabulary=VOCAB,
        generated_at="2026-08-10T00:00:00+00:00",
    )
    return report, entries


def _row(report, file_id):
    for r in report["documents"]:
        if r["file_id"] == file_id:
            return r
    raise AssertionError(f"no row for {file_id}")


# ---------------------------------------------------------------------------
# Firm identity + roster
# ---------------------------------------------------------------------------


def test_firm_tokens_drop_generic_words():
    assert vsc.firm_tokens(FIRM) == ["brannock", "ferreira"]


def test_zone_names_firm_requires_every_distinctive_token():
    """A shared surname is not identification — that is how the cc trap works."""
    assert vsc.zone_names_firm("BRANNOCK & FERREIRA LLP", ["brannock", "ferreira"])
    assert not vsc.zone_names_firm("FERREIRA & SONS LLP", ["brannock", "ferreira"])


def test_roster_never_matches_on_a_bare_surname():
    roster = vsc.Roster.from_items(STAFF)
    assert roster.match("Very truly yours,\nDean Brannock") == "Dean Brannock"
    assert roster.match("D. Whitmore") is None  # wrong surname, right initial
    assert roster.match("Kenneth Draper, Defendant") is None


def test_roster_reads_a_single_name_field_when_first_last_are_absent():
    roster = vsc.Roster.from_items([{"displayName": "Luisa Ferreira"}])
    assert roster.match("cc: Luisa Ferreira") == "Luisa Ferreira"


# ---------------------------------------------------------------------------
# Zones — the bound that keeps a RECIPIENT from being read as an AUTHOR
# ---------------------------------------------------------------------------


def test_letterhead_zone_stops_at_the_date():
    """A letter with no letterhead starts at its date, so its zone is EMPTY —
    which is what stops the addressee's company being read as the author."""
    assert vsc.letterhead_zone(CLIENT_STATUS_NO_LETTERHEAD) == ""
    assert "BRANNOCK & FERREIRA LLP" in vsc.letterhead_zone(DEMAND)
    assert "Denise Whitcomb" not in vsc.letterhead_zone(DEMAND)


def test_recipient_zone_drops_our_own_letterhead_and_caption_lines():
    zone = vsc.recipient_zone(vsc.Windows.of(MEDIATION_BRIEF).head, vsc.firm_tokens(FIRM))
    assert "BRANNOCK & FERREIRA LLP" not in zone  # ours, not the recipient's
    assert "Cornerstone Market Holdings, LLC" not in zone  # a party, not the addressee
    assert "Mediator" in zone


# ---------------------------------------------------------------------------
# Authorship classification
# ---------------------------------------------------------------------------


def test_firm_letterhead_marks_firm_authored(report_and_entries):
    report, _ = report_and_entries
    row = _row(report, "f-1")
    assert row["firm_authored"] is True
    assert "letterhead" in row["authorship_evidence"]


def test_signature_block_rescues_a_letter_whose_letterhead_was_lost(report_and_entries):
    report, _ = report_and_entries
    row = _row(report, "f-6")
    assert row["firm_authored"] is True
    assert "Luisa Ferreira" in row["authorship_evidence"]


def test_served_discovery_is_received_and_names_the_declarant(report_and_entries):
    report, _ = report_and_entries
    row = _row(report, "f-4")
    assert row["firm_authored"] is False
    assert "D. Whitmore" in row["authorship_evidence"]


def test_carrier_paper_is_received(report_and_entries):
    report, _ = report_and_entries
    assert _row(report, "f-7")["firm_authored"] is False


def test_cc_trap_is_live_before_we_claim_the_classifier_resists_it():
    """A trap that cannot spring measures nothing. The roster matcher MUST find
    our attorney in this received letter's tail — that is the false signal the
    ordering exists to beat."""
    roster = vsc.Roster.from_items(STAFF)
    assert roster.match(vsc.Windows.of(CC_TRAP).tail) == "Luisa Ferreira"


def test_cc_trap_classifies_as_received_despite_our_name_in_its_tail(report_and_entries):
    report, _ = report_and_entries
    row = _row(report, "f-5")
    assert row["firm_authored"] is False
    assert "TRAMMELL & VOSS LLP" in row["authorship_evidence"]


def test_no_text_layer_is_unknown_and_never_received(report_and_entries):
    report, _ = report_and_entries
    row = _row(report, "f-8")
    assert row["firm_authored"] == vsc.UNKNOWN
    assert row["authorship_evidence"].startswith("no text layer")
    assert row["firm_authored"] is not False


def test_a_document_with_no_signal_at_all_is_unknown():
    windows = vsc.Windows.of("June 1, 2026\n\nA note with no letterhead and no name.\n")
    got = vsc.classify_authorship(windows, firm_name=FIRM, roster=vsc.Roster.from_items(STAFF))
    assert got.firm_authored == vsc.UNKNOWN


def test_litigation_caption_without_a_firm_signal_is_unknown_not_received():
    """Which side of a caption we are on is not determinable from content, so
    the honest answer is `unknown` — not a received row we cannot support."""
    caption_only = SERVED_DISCOVERY.split("PROOF OF SERVICE")[0]
    got = vsc.classify_authorship(vsc.Windows.of(caption_only), firm_name=FIRM, roster=vsc.Roster.from_items(STAFF))
    assert got.firm_authored == vsc.UNKNOWN
    assert "caption" in got.evidence


# ---------------------------------------------------------------------------
# AC: zero received-paper rows are marked firm_authored
# ---------------------------------------------------------------------------


def test_no_received_document_is_ever_marked_firm_authored(report_and_entries):
    report, entries = report_and_entries
    received_ids = {"f-4", "f-5", "f-7"}
    for fid in received_ids:
        assert _row(report, fid)["firm_authored"] is False, fid
    assert not received_ids & {e["file"] for e in entries}


# ---------------------------------------------------------------------------
# AC: advisory signals are recorded, never decisive
# ---------------------------------------------------------------------------


def test_advisory_signals_are_recorded(report_and_entries):
    report, _ = report_and_entries
    assert _row(report, "f-1")["advisory"]["ownerId"] == "S2S_hermes"
    assert _row(report, "f-4")["advisory"]["isUploaded"] is True


def test_flipping_owner_and_upload_flags_changes_no_classification():
    """ownerId identifies the creating user or app, not the author. If it ever
    moves a verdict, the module docstring is lying."""
    flipped = [{**d, "ownerId": "someone-else", "isUploaded": not d.get("isUploaded")} for d in DOCS]
    base, _ = vsc.survey(FakeClient(), firm_name=FIRM, vocabulary=VOCAB)
    other, _ = vsc.survey(FakeClient(docs=flipped), firm_name=FIRM, vocabulary=VOCAB)
    verdicts = lambda rep: {  # noqa: E731 - test-local shorthand over a report shape; a def adds only a name
        r["file_id"]: (r["firm_authored"], r["cohort_proposal"], r["doc_type"]) for r in rep["documents"]
    }
    assert verdicts(base) == verdicts(other)


# ---------------------------------------------------------------------------
# Cohort proposal (adversarial)
# ---------------------------------------------------------------------------


def test_a_demand_to_an_adjuster_is_not_the_client_cohort(report_and_entries):
    """The body of a demand is full of the client's name. The recipient block is
    what decides, and it names a claims role."""
    report, _ = report_and_entries
    row = _row(report, "f-1")
    assert row["cohort_proposal"] == "adjuster"
    assert row["cohort_proposal"] != "client"


def test_a_status_letter_to_the_named_plaintiff_is_the_client_cohort(report_and_entries):
    report, _ = report_and_entries
    assert _row(report, "f-3")["cohort_proposal"] == "client"
    assert _row(report, "f-6")["cohort_proposal"] == "client"


def test_a_letter_to_another_firm_proposes_opposing_counsel():
    windows = vsc.Windows.of(
        "March 2, 2026\n\nGarrett Prosser, Esq.\nProsser & Kim LLP\n\n"
        "RE: Boyle v. Trammell Logistics\n\nDear Mr. Prosser:\n\n"
        "This letter is sent to meet and confer regarding your responses.\n"
    )
    proposed, _ = vsc.propose_audience(windows, firm_name=FIRM)
    assert proposed == "opposing-counsel"


def test_an_unauthored_cohort_is_left_null_with_its_reason(report_and_entries):
    """The pilot authors {client, adjuster, opposing-counsel}. A brief to a
    neutral proposes `court`, which has no home — so it stays null and says why
    rather than being coerced into the nearest authored cohort."""
    report, _ = report_and_entries
    row = _row(report, "f-2")
    assert row["firm_authored"] is True
    assert row["cohort_proposal"] is None
    assert "court" in row["cohort_reason"] and "vocabulary" in row["cohort_reason"]


def test_map_cohort_passes_an_authored_cohort_through():
    assert vsc.map_cohort("adjuster", "why", VOCAB) == ("adjuster", "why")
    assert vsc.map_cohort(None, "no signal", VOCAB) == (None, "no signal")


# ---------------------------------------------------------------------------
# Document types
# ---------------------------------------------------------------------------


def test_doc_types_use_the_closed_vocabulary(report_and_entries):
    report, _ = report_and_entries
    assert _row(report, "f-1")["doc_type"] == "demand_letter"
    assert _row(report, "f-3")["doc_type"] == "client_status_letter"
    assert _row(report, "f-2")["doc_type"] == "mediation_brief"


def test_an_unrecognized_shape_is_unclassified_and_never_ranked():
    windows = vsc.Windows.of("A one-line internal note about the parking validation.")
    assert vsc.classify_doc_type(windows) == vsc.UNCLASSIFIED
    rows = [
        {"firm_authored": True, "doc_type": vsc.UNCLASSIFIED, "matter_id": "m-1", "file_name": "a"},
        {"firm_authored": True, "doc_type": vsc.UNCLASSIFIED, "matter_id": "m-2", "file_name": "b"},
    ]
    assert vsc.rank_doc_types(rows) == []


def test_ranking_requires_two_matters(report_and_entries):
    """One letter on one matter is one lawyer's one letter, not a document type
    the firm produces."""
    report, _ = report_and_entries
    ranked = {d["type"]: d for d in report["doc_types"]}
    assert "client_status_letter" in ranked  # m-2 and m-3
    assert ranked["client_status_letter"]["count"] == 2
    assert ranked["client_status_letter"]["matters"] == 2
    assert "demand_letter" not in ranked  # m-1 only
    assert "mediation_brief" not in ranked


def test_received_documents_never_contribute_to_the_ranking(report_and_entries):
    report, _ = report_and_entries
    for entry in report["doc_types"]:
        for example in entry["examples"]:
            assert "Trammell & Voss" not in example
            assert "RFP Set One" not in example


# ---------------------------------------------------------------------------
# Manifest projection
# ---------------------------------------------------------------------------


def test_manifest_projects_only_firm_authored_rows_with_a_mapped_cohort(report_and_entries):
    _, entries = report_and_entries
    assert {e["file"] for e in entries} == {"f-1", "f-3", "f-6"}
    assert {e["cohort"] for e in entries} == {"adjuster", "client"}


def test_manifest_uses_ids_not_names(report_and_entries):
    _, entries = report_and_entries
    for e in entries:
        assert e["matter"] in MATTERS  # a matter id, not "Duarte v. Brennan"
        assert e["file"].startswith("f-")


def test_everything_not_projected_is_excluded_with_a_reason(report_and_entries):
    report, entries = report_and_entries
    projected = {e["file"] for e in entries}
    scanned = {r["file_id"] for r in report["documents"]}
    excluded = {x["file_id"] for x in report["excluded"]}
    assert excluded == scanned - projected
    assert all(x["reason"] for x in report["excluded"])


def test_rendered_manifest_is_what_the_fetch_bridge_parses(tmp_path: Path, report_and_entries):
    """The end of this script is the start of voice-fetch-corpus.py: its REAL
    manifest loader must accept what we emit, resolved by id."""
    yaml = pytest.importorskip("yaml")
    _, entries = report_and_entries
    dest = tmp_path / "exemplars.yaml"
    vsc.write_manifest(entries, str(dest), firm_name=FIRM, generated_at="2026-08-10T00:00:00+00:00")

    bridge = sys.modules["voice_fetch_corpus"]
    parsed = bridge.load_manifest(str(dest))
    assert {e.file for e in parsed} == {"f-1", "f-3", "f-6"}
    bridge.validate_cohorts(parsed, VOCAB)  # no raise: cohorts are seat-authored


def test_manifest_json_form_round_trips(tmp_path: Path, report_and_entries):
    _, entries = report_and_entries
    dest = tmp_path / "exemplars.json"
    vsc.write_manifest(entries, str(dest), firm_name=FIRM, generated_at="x")
    data = json.loads(dest.read_text())
    assert [e["file"] for e in data["entries"]] == ["f-1", "f-3", "f-6"]
    assert all(not k.startswith("_") for e in data["entries"] for k in e)


# ---------------------------------------------------------------------------
# Report shape + counts
# ---------------------------------------------------------------------------


def test_report_header_and_counts(report_and_entries):
    report, _ = report_and_entries
    assert report["generated_at"] == "2026-08-10T00:00:00+00:00"
    assert report["tenant"]["firm_name"] == FIRM
    assert report["tenant"]["matters"] == 4
    c = report["classification"]
    assert c["scanned"] == len(DOCS)
    assert c["firm_authored"] == 4  # f-1, f-2, f-3, f-6
    assert c["received"] == 3  # f-4, f-5, f-7
    assert c["unreadable"] == 1  # f-8
    assert c["inconclusive"] == 0
    assert c["firm_authored"] + c["received"] + c["unreadable"] + c["inconclusive"] == c["scanned"]


def test_unreadable_is_counted_apart_from_received(report_and_entries):
    report, _ = report_and_entries
    c = report["classification"]
    assert c["unreadable"] == 1
    # the unreadable scan is NOT in the received tally
    assert _row(report, "f-8")["firm_authored"] is not False


def test_report_records_the_classification_method_and_the_advisory_caveat(report_and_entries):
    report, _ = report_and_entries
    method = report["classification"]["method"]
    assert "content-based" in method
    assert "never pass conditions" in method
    assert "--firm-name" in report["tenant"]["firm_name_source"]


def test_report_is_json_serializable(tmp_path: Path, report_and_entries):
    report, _ = report_and_entries
    dest = tmp_path / "survey.json"
    vsc.write_report(report, str(dest))
    assert json.loads(dest.read_text())["tenant"]["matters"] == 4


# ---------------------------------------------------------------------------
# Budgets — enforced AND reported (a silent cap is a lie about the tenant)
# ---------------------------------------------------------------------------


def test_a_complete_scan_says_so(report_and_entries):
    report, _ = report_and_entries
    b = report["budgets"]
    assert b["complete"] is True
    assert b["unread_candidates"] == []
    assert b["matters_truncated"] is False


def test_matter_cap_is_enforced_and_reported():
    report, _ = vsc.survey(
        FakeClient(),
        firm_name=FIRM,
        vocabulary=VOCAB,
        budget=vsc.Budget(max_matters=1),
    )
    b = report["budgets"]
    assert report["tenant"]["matters"] == 1
    assert b["matters_truncated"] is True
    assert b["matters_reported_total"] == 4  # the tenant told us the real total
    assert b["complete"] is False


def test_per_matter_document_cap_is_enforced_and_reported():
    report, _ = vsc.survey(
        FakeClient(),
        firm_name=FIRM,
        vocabulary=VOCAB,
        budget=vsc.Budget(max_docs_per_matter=1),
    )
    b = report["budgets"]
    assert set(b["docs_truncated_by_matter"]) == {"m-1", "m-2", "m-3", "m-4"}
    assert report["classification"]["scanned"] == 4
    assert b["complete"] is False


def test_read_budget_names_every_candidate_it_never_opened():
    report, entries = vsc.survey(
        FakeClient(),
        firm_name=FIRM,
        vocabulary=VOCAB,
        budget=vsc.Budget(max_reads=2),
    )
    b = report["budgets"]
    assert b["reads_used"] == 2
    assert b["reads_budget_exhausted"] is True
    assert len(b["unread_candidates"]) == len(DOCS) - 2
    assert all(u["file_name"] for u in b["unread_candidates"])
    assert report["classification"]["scanned"] == 2
    # and the manifest cannot silently contain a document that was never read
    assert len(entries) <= 2


def test_a_read_that_fails_does_not_end_the_survey():
    class Flaky(FakeClient):
        def download_file(self, matter_id, file_id):
            if file_id == "f-1":
                raise RuntimeError("presigned download GET failed: 503")
            return super().download_file(matter_id, file_id)

    report, entries = vsc.survey(Flaky(), firm_name=FIRM, vocabulary=VOCAB)
    row = _row(report, "f-1")
    assert row["firm_authored"] == vsc.UNKNOWN
    assert "download failed" in row["authorship_evidence"]
    assert report["classification"]["scanned"] == len(DOCS)  # the rest still ran
    assert "f-1" not in {e["file"] for e in entries}


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def test_cohort_vocabulary_comes_from_the_shared_loader(tmp_path: Path):
    """One parser, shared with the fetch bridge — two copies would drift, and
    the drift shows up as a manifest the bridge refuses."""
    yaml_path = tmp_path / "customer.yaml"
    yaml_path.write_text(
        "customer_id: pilot\nvoice_cohorts:\n  cohorts:\n    - client\n"
        "    - adjuster\n    - opposing-counsel\n  min_samples_per_cohort: 5\n",
        encoding="utf-8",
    )
    assert vsc.load_cohort_vocabulary(str(yaml_path)) == VOCAB


def test_empty_firm_name_is_refused(capsys):
    rc = vsc.main(["--firm-name", "   ", "--out-report", "/dev/null"])
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().err

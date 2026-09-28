"""Overdue tasks for the task-list-keeper (Job 1), and the documents that
prove two of them done. Added 2026-09-28; seeded by ``seed_staging.py``
alongside ``seed_data.py`` (its own module because seed_data.py sits at its
module-size baseline).

WHY. The rehearsal tenant had no overdue open task, so the keeper's weekly
review sent nothing and its review -> approve-by-reply -> close loop had never
run live. These four are past due on purpose, on existing seeded matters, and
each is chosen so the keeper's own rules (``operator/skills/task-list-keeper/
references/classification.md``) sort it into a different class:

* DONE by content (two): a document of the task's kind, filed on or after the
  task, whose name's distinctive words the subject also names, and whose own
  text names the matter's client. A name alone proves nothing (PR #2960).
* STILL NEEDED (one): nothing on the matter shows it done.
* AT STAKE (one): no money or court word in the subject, but the matter's
  Final Status Conference (Oct 2, on the tenant calendar) falls inside the
  escalation window, so the keeper never offers it for closing.

``test_task_list_keeper.py::test_the_pilot_overdue_seed_sorts_into_four_classes``
pins those outcomes. Standing synthetic seed data, the same class as
``seed_data.TASKS``: deliberately NOT ``[SMD-PROBE`` stamped, because a probe
subject is fenced out of the keeper by design. Every name here is fictional.
"""

from __future__ import annotations

from seed_data import _ALVAREZ, _BELL, _caption

KEEPER_TASKS: dict[str, dict] = {
    "overdue-verification-alvarez": {
        "matter": "mva-alvarez",
        "subject": "Get client verification signed for RFP Set Two responses - Alvarez",
        "note": "Responses drafted; verification sent to client for signature. Serve once signed.",
        "due": "2026-09-11",
    },
    "overdue-service-bell": {
        "matter": "multidef-bell",
        "subject": "Serve Bell responses to Halverson RFP Set One",
        "note": "Responses finalized after meet and confer; serve on all counsel of record.",
        "due": "2026-09-04",
    },
    "overdue-preservation-chen": {
        "matter": "premises-chen",
        "subject": "Send preservation letter to Sunrise Plaza for incident-date surveillance video - Chen",
        "note": "Plaza management confirmed cameras cover the entrance; footage overwrites on a rolling cycle.",
        "due": "2026-08-21",
    },
    "overdue-exhibits-okafor": {
        "matter": "trial-okafor",
        "subject": "Update exhibit list with Grand Valley incident photos - Okafor",
        "note": "Add the six incident-scene photos produced by Grand Valley; renumber the list after.",
        "due": "2026-09-18",
    },
}


def _verification_alvarez() -> list[str]:
    court, p, d, no = _ALVAREZ
    return _caption(
        court,
        p,
        d,
        no,
        "VERIFICATION OF PLAINTIFF'S RESPONSES TO REQUESTS FOR PRODUCTION, SET TWO",
        propounding="Defendant KENNETH DRAPER",
        responding="Plaintiff MARIA ALVAREZ",
    ) + [
        "                          VERIFICATION",
        "",
        "I, MARIA ALVAREZ, am the plaintiff in this action. I have read the",
        "foregoing PLAINTIFF'S RESPONSES TO DEFENDANT'S REQUESTS FOR PRODUCTION,",
        "SET TWO, and know their contents. The matters stated in the responses",
        "are true of my own knowledge, except as to those matters stated on",
        "information and belief, and as to those matters I believe them to be true.",
        "",
        "I declare under penalty of perjury under the laws of the State of",
        "California that the foregoing is true and correct.",
        "",
        "Executed on September 18, 2026, at Burbank, California.",
        "",
        "                              /s/ Maria Alvarez",
        "                              MARIA ALVAREZ",
    ]


def _served_responses_bell() -> list[str]:
    court, p, _d, no = _BELL
    lines = _caption(
        court,
        p,
        "HALVERSON PROPERTY GROUP LLC",
        no,
        "PLAINTIFF THOMAS BELL'S RESPONSES TO DEFENDANT HALVERSON'S REQUESTS FOR PRODUCTION, SET ONE",
        propounding="Defendant HALVERSON PROPERTY GROUP LLC",
        responding="Plaintiff THOMAS BELL",
    )
    for i, response in enumerate(
        [
            "Plaintiff will produce all responsive documents relating to his employment at the project site.",
            "Plaintiff will produce the certificates of safety training in his possession.",
            "Plaintiff will produce all photographs of the location of the incident in his possession.",
            "Plaintiff will produce the medical records relating to the injuries claimed in this action.",
        ],
        start=1,
    ):
        lines += [f"RESPONSE TO REQUEST FOR PRODUCTION NO. {i}:", f"    {response}", ""]
    # The firm's own proof of service: what was served, when, and how.
    return lines + [
        "",
        "                     PROOF OF SERVICE",
        "",
        "I, the undersigned, declare that I am over the age of eighteen years",
        "and not a party to the within action. My business address is",
        "3500 West Olive Avenue, Suite 300, Burbank, CA 91505.",
        "",
        "I served the foregoing document described as PLAINTIFF THOMAS BELL'S",
        "RESPONSES TO DEFENDANT HALVERSON PROPERTY GROUP LLC'S REQUESTS FOR",
        "PRODUCTION, SET ONE on the interested parties in this action as follows:",
        "",
        "[X] BY ELECTRONIC SERVICE: I transmitted the document to the electronic service addresses of record.",
        "",
        "I declare under penalty of perjury under the laws of the State of",
        "California that the foregoing is true and correct.",
        "",
        "Executed on September 15, 2026, at Burbank, California.",
        "",
        "                              /s/ L. Moreno",
        "                              L. Moreno",
    ]


def build_keeper_documents() -> dict[str, tuple[str, str, list[str]]]:
    """doc_key -> (matter_key, file_name, text lines), the seed_data shape."""
    return {
        "verification-rfp-two-alvarez": (
            "mva-alvarez",
            "2026-09-18 Verification - RFP Set Two Responses - Alvarez.pdf",
            _verification_alvarez(),
        ),
        "pos-bell-responses-halverson": (
            "multidef-bell",
            "2026-09-15 Proof of Service - Responses to Halverson RFP Set One - Bell.pdf",
            _served_responses_bell(),
        ),
    }

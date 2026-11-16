"""A client champion on the guide seat: everything rep-only is walled by code.

The relay's wall is ``tests/test_champion_wall.py``. This file drives the rest,
each through the seat's real pieces (the plugins loaded as Hermes loads them,
``CustomerConfig`` over a real customer.yaml, the broker's enrolled list, the
real session-origin register and the provenance resolver on real threads):

* ``pre_llm_call`` states the champion's role and marks the turn in flight; the
  turn's end clears it;
* the read fence narrows the skills tree for a champion's turn only, and a
  representative's turn running beside it still reads pitch-practice;
* the training and desk tools refuse a champion with the fixed sentence;
* the digest, library-change and training-report recipients skip champions;
* a champion is held to 20 emails a day whatever the seat authors;
* a champion code's redeem sends the champion welcome, starts no training and
  tells SMD the label;
* the kind of code comes from the administrator's own words, never the model.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from shared import champion, inbound, person_digest, provenance, training_report
from shared import sender_enrollment as se
from shared.customer_config import CustomerConfig
from tests import test_sender_enrollment as te
from tests.conftest import load_plugin
from tests.test_champion_wall import write_library
from tests.test_record_store_partition import _email_turn
from tests.test_rep_desk import desk  # noqa: F401 - fixture reuse
from tests.test_sender_enrollment import enrolled_file, router, tool  # noqa: F401 - fixture reuse
from tests.test_training import _email, seat  # noqa: F401 - fixture reuse

CHAMPION = "dana@example-brokerage.test"
REP = "rep-a@example.test"
ADMIN = "admin@example.test"


def _enroll(path: Path, *rows: tuple[str, str]) -> None:
    entries = [
        {"address": a, "label": label, "code_id": "c_1", "enrolled_at": "x"} for a, label in rows
    ]
    path.write_text(json.dumps({"schema": 1, "enrolled": entries}), encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean():
    champion.IN_FLIGHT._reset_for_tests()
    yield
    champion.IN_FLIGHT._reset_for_tests()
    provenance._reset_for_tests()


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    skill = write_library(tmp_path / "lib")
    monkeypatch.setenv(champion.DIR_ENV, str(skill))
    return skill


# ---------------------------------------------------------------------------
# pre_llm_call: the role line and the in-flight mark
# ---------------------------------------------------------------------------

GUIDE = f"""
customer_id: smd-guide
seat:
  kind: internal
  product: operator
  role: guide
sender_enrollment:
  enabled: true
scope:
  inbound_allow_from: [{ADMIN}]
  admins: [{ADMIN}]
"""


@pytest.fixture
def guide(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "customer.yaml"
    path.write_text(dedent(GUIDE).strip(), encoding="utf-8")
    enrolled = tmp_path / "enrolled.json"
    _enroll(enrolled, (CHAMPION, se.CHAMPION_LABEL), (REP, "admin-issued"))
    monkeypatch.setenv("SMD_CUSTOMER_YAML_PATH", str(path))
    monkeypatch.setenv(se.ENROLLED_ENV, str(enrolled))
    return enrolled


def test_a_champions_turn_states_the_role_and_is_marked_in_flight(guide) -> None:
    """Keyed on the session's verified email origin, so it holds whatever
    channel sender id the gateway passed."""
    mod = load_plugin("hermes-smd-initiation")
    for session, sender in (("s-champ", CHAMPION), ("s-rep", REP)):
        inbound.SESSION_INBOUND_ORIGIN.record(
            session, inbound.InboundOrigin(sender_address=sender, message_id=f"m-{session}")
        )
    out = mod.champion_fence.on_turn_start(session_id="s-champ", sender_id="webhook:agentmail")
    assert out is not None and champion.ROLE_LINE in out["context"]
    assert champion.IN_FLIGHT.has("s-champ")
    assert mod.champion_fence.on_turn_start(session_id="s-rep") is None
    assert not champion.IN_FLIGHT.has("s-rep")
    assert mod.champion_fence.on_turn_start(session_id="s-nobody") is None
    # The authority statement still names nothing about a champion.
    rep = mod.on_pre_llm_call(session_id="s-rep", sender_id=REP)
    assert rep is not None and champion.ROLE_LINE not in rep["context"]
    mod.champion_fence.on_turn_end(session_id="s-champ")
    assert not champion.IN_FLIGHT.any()
    inbound.SESSION_INBOUND_ORIGIN._reset_for_tests()


def test_the_plugin_registers_the_fence_and_the_turn_end_only_on_a_guide_seat(
    guide, tmp_path
) -> None:
    class Ctx:
        def __init__(self) -> None:
            self.hooks: list[tuple[str, object]] = []

        def register_hook(self, name, fn) -> None:
            self.hooks.append((name, fn))

        def register_tool(self, **_kw) -> None:
            pass

    mod = load_plugin("hermes-smd-initiation")
    ctx = Ctx()
    mod.register(ctx)
    assert ("pre_llm_call", mod.champion_fence.on_turn_start) in ctx.hooks
    assert ("pre_tool_call", mod.champion_fence.on_pre_tool_call) in ctx.hooks
    assert ("post_llm_call", mod.champion_fence.on_turn_end) in ctx.hooks
    demo = (
        (tmp_path / "customer.yaml")
        .read_text()
        .replace("  role: guide\n", "")
        .replace("kind: internal", "kind: demo")
    )
    (tmp_path / "customer.yaml").write_text(demo)
    other = Ctx()
    mod.register(other)
    assert ("post_llm_call", mod.champion_fence.on_turn_end) not in other.hooks


def test_a_mark_expires_when_the_turn_end_never_fires() -> None:
    flight = champion.InFlight(ttl_s=10.0)
    flight.mark("s", now=100.0)
    assert flight.has("s", now=105.0) and not flight.has("s", now=111.0)


# ---------------------------------------------------------------------------
# the read fence
# ---------------------------------------------------------------------------


def _fence(tool_name: str, args: dict) -> dict | None:
    return load_plugin("hermes-smd-initiation").champion_fence.on_pre_tool_call(
        tool_name=tool_name, args=args
    )


def _on_new_thread(fn, *args):
    box: dict = {}

    def run() -> None:
        box["v"] = fn(*args)

    t = threading.Thread(target=run)
    t.start()
    t.join()
    return box["v"]


PITCH = "/app/skills/pitch-practice/SKILL.md"
GUIDE_SKILL = "/app/skills/product-guide/SKILL.md"
JOBS = "/app/skills/product-guide/references/jobs.md"
DEMO = "/app/skills/product-guide/references/demo-meetings.md"


def test_with_no_champion_in_flight_nothing_changes(library) -> None:
    provenance.note_session("s-rep")
    for path in (PITCH, DEMO):
        assert _fence("read_file", {"path": path}) is None
    assert _fence("search_files", {"pattern": "x", "path": "/app/skills"}) is None


@pytest.mark.parametrize(
    ("tool_name", "args", "allowed"),
    [
        ("read_file", {"path": GUIDE_SKILL}, True),
        ("read_file", {"path": JOBS}, True),
        ("read_file", {"path": "/opt/data/skills/product-guide/references/jobs.md"}, True),
        ("read_file", {"path": DEMO}, False),
        ("read_file", {"path": PITCH}, False),
        ("read_file", {"path": "/app/skills/rep-training/references/curriculum.yaml"}, False),
        ("read_file", {"path": "/opt/data/profiles/guide/skills/product-guide/SKILL.md"}, True),
        (
            "read_file",
            {"path": "/opt/data/profiles/guide/skills/product-guide/references/demo-meetings.md"},
            False,
        ),
        ("read_file", {"path": "/tmp/somewhere-else.txt"}, True),
        ("skill_view", {"name": "product-guide"}, True),
        ("skill_view", {"name": "product-guide", "file_path": "references/jobs.md"}, True),
        (
            "skill_view",
            {"name": "product-guide", "file_path": "references/demo-meetings.md"},
            False,
        ),
        ("skill_view", {"name": "product-guide", "file_path": "references/../x.md"}, False),
        ("skill_view", {"name": "pitch-practice"}, False),
        (
            "search_files",
            {"pattern": "demo", "path": "/app/skills/product-guide/references"},
            False,
        ),
        ("search_files", {"pattern": "x", "path": "/opt/data/profiles/guide/skills"}, False),
        ("terminal", {"command": f"cat {JOBS}"}, False),
        (
            "execute_code",
            {"code": "open('/opt/data/skills/pitch-practice/SKILL.md').read()"},
            False,
        ),
    ],
)
def test_a_champions_turn_reads_only_product_guide_and_its_open_documents(
    library, tool_name, args, allowed
) -> None:
    champion.IN_FLIGHT.mark("s-champ")
    provenance.note_session("s-champ")
    verdict = _fence(tool_name, args)
    if allowed:
        assert verdict is None, (tool_name, args)
    else:
        assert verdict is not None and verdict["action"] == "block", (tool_name, args)


def test_a_representatives_turn_beside_a_champions_still_reads_pitch_practice(library) -> None:
    """Two turns in flight at once, each on its own thread, as on the seat."""
    champion.IN_FLIGHT.mark("s-champ")

    def champ_reads() -> dict | None:
        provenance.note_session("s-champ")
        return _fence("read_file", {"path": PITCH})

    def rep_reads() -> dict | None:
        provenance.note_session("s-rep")
        return _fence("read_file", {"path": PITCH})

    assert _on_new_thread(champ_reads)["action"] == "block"
    assert _on_new_thread(rep_reads) is None
    # A thread that cannot say whose turn it is fails closed while a champion runs.
    assert _on_new_thread(lambda: _fence("read_file", {"path": PITCH}))["action"] == "block"


def test_a_fence_that_cannot_decide_refuses(library, monkeypatch) -> None:
    champion.IN_FLIGHT.mark("s-champ")
    provenance.note_session("s-champ")

    def boom(*_a, **_k):
        raise OSError("index gone")

    monkeypatch.setattr(champion, "allowlist", boom)
    assert _fence("read_file", {"path": JOBS})["action"] == "block"


# ---------------------------------------------------------------------------
# the representative tools refuse a champion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tool_name", ["training_record", "tone_check", "material_check"])
def test_the_training_tools_refuse_a_champion(seat, tool_name) -> None:  # noqa: F811 - fixture reuse
    _enroll(seat["enrolled"], (CHAMPION, se.CHAMPION_LABEL), (REP, "t"))
    session = _email(seat, CHAMPION, "start training please")
    handler = seat["tools"][tool_name]["handler"]
    args = {"action": "status"} if tool_name == "training_record" else {}
    out = json.loads(handler(args, session_id=session, task_id="t"))
    assert out["ok"] is False and out["say"] == champion.SENTENCE
    # The representative beside them is served as before.
    rep_session = _email(seat, REP, "start training please")
    rep = json.loads(
        seat["tools"]["training_record"]["handler"]({"action": "status"}, session_id=rep_session)
    )
    assert rep.get("say") != champion.SENTENCE and "next_lesson" in rep


def test_the_reminder_tool_refuses_a_champion(desk) -> None:  # noqa: F811 - fixture reuse
    _enroll(desk["enrolled"], (CHAMPION, se.CHAMPION_LABEL), (REP, "t"))
    _email_turn(desk, CHAMPION, "s-desk-champ", "<c-1@example.test>", "remind me Thursday")
    handler = desk["tools"]["reminder_set"]["handler"]
    out = json.loads(
        handler({"when": "Thursday at 2pm", "what": "call"}, session_id="s-desk-champ")
    )
    assert out["say"] == champion.SENTENCE
    store = desk["volume"] / "rep" / "reminders"
    assert not store.exists() or not any(store.rglob("*.json"))


def test_a_marked_champion_session_is_refused_without_an_origin(desk) -> None:  # noqa: F811 - fixture reuse
    champion.IN_FLIGHT.mark("s-marked")
    handler = desk["tools"]["reminder_set"]["handler"]
    assert json.loads(handler({"when": "x", "what": "y"}, session_id="s-marked"))["say"] == (
        champion.SENTENCE
    )


# ---------------------------------------------------------------------------
# recipients
# ---------------------------------------------------------------------------


def test_the_digest_and_library_notice_recipients_skip_champions(guide, tmp_path) -> None:
    cfg = CustomerConfig.from_volume(str(tmp_path / "customer.yaml"))
    assert CHAMPION in cfg.inbound_roster, "a champion is answered like anyone enrolled"
    everyone = person_digest.people(cfg)
    assert REP in everyone and CHAMPION not in everyone


def test_the_training_report_lists_no_champion(seat) -> None:  # noqa: F811 - fixture reuse
    from shared import training

    _enroll(seat["enrolled"], (CHAMPION, se.CHAMPION_LABEL), (REP, "t"))
    cur = training.load_curriculum(
        training.skill_dir("rep-training"), training.skill_dir("pitch-practice")
    )
    cfg = CustomerConfig.from_volume(str(seat["yaml"]))
    labels = [label for label, _p in training_report.gather(cur, config=cfg)]
    assert labels == [REP]


# ---------------------------------------------------------------------------
# the daily cap
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("authored", "cap"), [(60, 20), (None, 20), (5, 5)])
def test_a_champion_is_held_to_twenty_a_day(guide, tmp_path, authored, cap) -> None:
    mod = load_plugin("hermes-smd-webhook-router")
    raw = yaml.safe_load((tmp_path / "customer.yaml").read_text())
    if authored is not None:
        raw["sender_enrollment"]["daily_limit"] = authored
    assert mod.daily_limit.limit_for(raw, CHAMPION) == cap
    assert mod.daily_limit.limit_for(raw, REP) == authored
    raw["sender_enrollment"]["enabled"] = False
    assert mod.daily_limit.limit_for(raw, CHAMPION) is None


def test_the_kind_of_code_comes_from_words() -> None:
    assert se.kind_from_words("I need a Champion code for Dana Reyes") == se.KIND_CHAMPION
    assert se.kind_from_words("champions: Dana Reyes") == se.KIND_CHAMPION
    assert se.kind_from_words("I need a code for Dana Reyes", "") == se.KIND_REPRESENTATIVE
    assert se.kind_from_words("the championship is over") == se.KIND_REPRESENTATIVE
    assert se.kind_from_words(None) == se.KIND_REPRESENTATIVE


def test_the_seat_session_register_is_the_real_one() -> None:
    assert champion.champion_turn("") is False
    assert inbound.SESSION_INBOUND_ORIGIN.bound_this_turn("nobody") is None


# ---------------------------------------------------------------------------
# issuing: the kind comes from the administrator's own words
# ---------------------------------------------------------------------------


def _admin_origin(session: str, words: str, *, subject: str = "codes") -> None:
    inbound.SESSION_INBOUND_ORIGIN.record(
        session,
        inbound.InboundOrigin(
            sender_address=te.ADMIN,
            message_id=f"m-{session}",
            content_digest="d",
            reply_text=words,
            subject=subject,
        ),
    )


def test_a_champion_code_is_asked_for_in_the_administrators_words(tool) -> None:  # noqa: F811 - fixture reuse
    mod, issued, _ = tool
    _admin_origin("s-champ-code", "I need a champion code for Dana Reyes, please.")
    out = mod.issue_handler({"name": "Dana Reyes"}, session_id="s-champ-code")
    assert out.startswith("DONE: champion code c_9 for Dana Reyes")
    assert issued[-1]["kind"] == se.KIND_CHAMPION


def test_the_model_cannot_choose_the_kind(tool) -> None:  # noqa: F811 - fixture reuse
    mod, issued, _ = tool
    _admin_origin("s-rep-code", "I need a code for Dana Reyes.")
    out = mod.issue_handler({"name": "Dana Reyes", "kind": "champion"}, session_id="s-rep-code")
    assert out.startswith("DONE: representative code c_9 for Dana Reyes")
    assert issued[-1]["kind"] == se.KIND_REPRESENTATIVE


def test_the_demo_seat_issues_representative_codes_only(tool) -> None:  # noqa: F811 - fixture reuse
    mod, issued, path = tool
    path.write_text(te.DEMO)
    _admin_origin("s-demo-champ", "I need a champion code for Dana Reyes.")
    out = mod.issue_handler({"name": "Dana Reyes"}, session_id="s-demo-champ")
    assert out.startswith("DONE: representative code c_9")
    assert issued[-1]["kind"] == se.KIND_REPRESENTATIVE


# ---------------------------------------------------------------------------
# redeeming a champion code
# ---------------------------------------------------------------------------


def test_a_champion_redeem_sends_the_champion_welcome_and_starts_no_training(
    router,  # noqa: F811 - fixture reuse
    monkeypatch,
) -> None:
    mod, _fake, broker, _tmp = router
    started: list[str] = []
    monkeypatch.setattr(mod, "_start_training", lambda _cfg, addr: started.append(addr) or True)
    real = broker.redeem

    def redeem(**kw):
        out = real(**kw)
        out["label"] = se.CHAMPION_LABEL
        return out

    monkeypatch.setattr(se, "redeem", redeem)
    broker.name = "Dana Reyes"
    verdict = te._dispatch(mod, te._mail(CHAMPION, f"my code {te.CODE}"))
    assert verdict == {"action": "skip"}
    [(_mid, text, _anchor)] = broker.replies
    assert text == se.welcome_text("Dana Reyes", champion=True)
    assert started == [], "no progress record and no library mark for a champion"
    [notice] = broker.sends
    assert "The code's label is champion." in notice["text"]
    assert "client champion" in notice["text"]


def test_a_representative_redeem_still_starts_training(router, monkeypatch) -> None:  # noqa: F811 - fixture reuse
    mod, _fake, broker, _tmp = router
    started: list[str] = []
    monkeypatch.setattr(mod, "_start_training", lambda _cfg, addr: started.append(addr) or False)
    te._dispatch(mod, te._mail(te.REP, f"my code {te.CODE}"))
    assert started == [te.REP]
    assert "The code's label is x." in broker.sends[0]["text"]

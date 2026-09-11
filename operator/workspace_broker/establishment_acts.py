"""The act lifecycle: one authored tool call is proposed to an administrator and committed.

Split out of ``establishment_store.py`` (2026-09-11); see
:mod:`.establishment_lifecycle` for the shape. The seat config is read
through the store, per call, never cached.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

from .establishment_constants import (
    ACT_COMMITTED_ACTION_TYPE,
    ACT_CONFIG_KEYS,
    ACT_NAME_KEYS,
    ACT_PROPOSED_ACTION_TYPE,
    ACT_TOOLS,
    _MAX_SHORT_TEXT,
)
from .establishment_lifecycle import ProposalLifecycle
from .establishment_validation import (
    EstablishmentValidationError,
    _bounded_str,
    _hash_text,
    _require_display_name,
    _require_text,
    act_readback_text,
    normalize_rule_text,
    readback_for,
    require_address,
)


class ActProposals(ProposalLifecycle):
    """act_propose / act_commit (ss-console#2536)."""

    # ------------------------------------------------------------------
    # act_propose / act_commit  (ss-console#2536)
    # ------------------------------------------------------------------

    def _seat_config(self) -> dict[str, Any]:
        """The seat's own customer.yaml, re-read per call.

        Never cached: the file is root-owned and can be re-applied under a
        running broker, and a cached copy would let a config the firm has
        already changed keep authorizing acts.
        """
        if self._store.customer_path is None:
            raise EstablishmentValidationError("this broker has no customer.yaml handle; no act can be proposed")
        try:
            import yaml

            data = yaml.safe_load(self._store.customer_path.read_text(encoding="utf-8")) or {}
        except OSError as exc:
            raise EstablishmentValidationError(
                f"the seat config is not readable ({exc.__class__.__name__}); no act can be proposed"
            ) from exc
        except Exception as exc:
            raise EstablishmentValidationError(
                f"the seat config is not parseable ({exc.__class__.__name__}); no act can be proposed"
            ) from exc
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _require_act_exposure(data: dict[str, Any], tool: str) -> None:
        """Refuse unless SOME persona on this seat authors ``commitment: confirm``.

        WHAT THIS IS AND IS NOT. The persona-aware gate is the seat's
        enforcement hook, which knows which persona is running this turn and
        clamps the act against that persona's authored exposure. This check
        cannot: the broker is not told the persona. What it CAN say is whether
        the seat authorizes confirmable acts at all, and a seat that authorizes
        none must not be able to write an act row through any path, including a
        hook with a bug in it. So it is deliberately the weaker of the two
        checks and it fails in the safe direction: a seat with no
        ``commitment: confirm`` anywhere proposes nothing.
        """
        personas = data.get("personas")
        if not isinstance(personas, list):
            personas = []
        for persona in personas:
            if not isinstance(persona, dict):
                continue
            entitlements = persona.get("entitlements")
            if not isinstance(entitlements, dict):
                continue
            exposure = entitlements.get("exposure")
            if isinstance(exposure, dict) and exposure.get("commitment") == "confirm":
                return
        raise EstablishmentValidationError(
            f"this seat authors no persona with exposure.commitment set to 'confirm', so "
            f"{tool} cannot be proposed to anybody. Authoring it is a config change the "
            "firm agrees to, not something this turn can do"
        )

    def _authored_act_payload(self, tool: str) -> dict[str, Any]:
        """The one payload this seat may propose for this tool, from its own
        authored config. Refused by name when there is none.

        THE MODEL'S ONLY ROLE IN AN ACT IS TO ASK FOR IT. Every value the act
        carries is read here, out of the file the firm authored and a PR
        changed, so "the Operator created a different matter than the one we
        agreed" has no route into the system: a payload that is not byte-equal
        to this is refused, and a seat with nothing authored can propose
        nothing at all.
        """
        if tool != "mcp_smokeball_create_matter":
            raise EstablishmentValidationError(f"no authored act payload is defined for {tool!r}")
        data = self._seat_config()
        self._require_act_exposure(data, tool)
        block: Any = data
        for key in ACT_CONFIG_KEYS:
            block = block.get(key) if isinstance(block, dict) else None
        if not isinstance(block, dict):
            raise EstablishmentValidationError(
                "this seat has no authored "
                + ".".join(ACT_CONFIG_KEYS)
                + " block; the firm authors the matter to create, and until it "
                "does there is nothing to propose"
            )
        fields = ACT_TOOLS[tool]
        missing = [f for f in fields if not isinstance(block.get(f), str) or not block[f].strip()]
        if missing:
            raise EstablishmentValidationError(
                "the authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block is missing {sorted(missing)}; every field the readback "
                "names has to be authored before the act can be proposed"
            )
        names = ACT_NAME_KEYS.get(tool, ())
        extra = sorted(set(block) - set(fields) - set(names))
        if extra:
            raise EstablishmentValidationError(
                "the authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block carries unknown keys {extra}; the act carries exactly "
                f"{sorted(fields)} plus the display names {sorted(names)} and nothing else"
            )
        return {f: block[f].strip() for f in fields}

    def _authored_act_names(self, tool: str) -> dict[str, str]:
        """The authored display names beside the act payload, if the block
        carries them. Empty when it does not; the caller then needs them from
        the request, and refuses by name when nobody supplied them."""
        data = self._seat_config()
        block: Any = data
        for key in ACT_CONFIG_KEYS:
            block = block.get(key) if isinstance(block, dict) else None
        if not isinstance(block, dict):
            return {}
        out: dict[str, str] = {}
        for key in ACT_NAME_KEYS.get(tool, ()):
            value = block.get(key)
            if isinstance(value, str) and value.strip():
                out[key] = value.strip()
        return out

    @staticmethod
    def _require_act_tool(value: Any) -> str:
        tool = _require_text(value, "tool", _MAX_SHORT_TEXT)
        if tool not in ACT_TOOLS:
            raise EstablishmentValidationError(
                f"{tool!r} is not an act this broker can propose; the closed vocabulary is {sorted(ACT_TOOLS)}"
            )
        return tool

    @staticmethod
    def _require_act_payload(value: Any, tool: str) -> dict[str, Any]:
        """The caller's payload, shape-checked before it is compared.

        Shape first, then equality, so an oversize or wrong-typed field is a
        named refusal rather than a mismatch that reads like a config problem.
        """
        if not isinstance(value, dict):
            raise EstablishmentValidationError("payload must be an object")
        fields = ACT_TOOLS[tool]
        names = ACT_NAME_KEYS.get(tool, ())
        unknown = sorted(set(value) - set(fields) - set(names))
        if unknown:
            raise EstablishmentValidationError(
                f"payload carries fields {unknown} that {tool} does not take; "
                f"it takes exactly {sorted(fields)} (plus the display names {sorted(names)})"
            )
        return {f: _require_text(value.get(f), f"payload.{f}", _MAX_SHORT_TEXT) for f in fields}

    @staticmethod
    def _payload_names(value: Any, tool: str) -> dict[str, str]:
        """The display names riding in a payload, if any (the hook sends the
        authored block whole). Shape-checked like every other caller string."""
        if not isinstance(value, dict):
            return {}
        out: dict[str, str] = {}
        for key in ACT_NAME_KEYS.get(tool, ()):
            if value.get(key) is not None:
                out[key] = _require_text(value.get(key), f"payload.{key}", _MAX_SHORT_TEXT)
        return out

    def act_propose(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record one TOOL CALL as pending and return the line to send.

        Nothing is created here. What comes back is the exact sentence to put in
        front of an administrator, naming every value the act will carry, and
        the tag they quote when they answer. The act happens on the confirming
        turn, through the tool, under the overlay's own gate.

        NOT A TOOL. This verb is reachable only from the seat's enforcement hook
        (overlay PR 2); no agent-callable tool maps to it. The model can ask for
        the authored matter and cannot compose one.
        """
        pending = self._require_pending()
        tool = self._require_act_tool(request.get("tool"))
        payload = self._require_act_payload(request.get("payload"), tool)
        instructed_by = require_address(request.get("instructed_by"), "instructed_by")
        source_ref = _require_text(request.get("source_ref"), "source_ref", _MAX_SHORT_TEXT)
        authored = self._authored_act_payload(tool)
        authored_names = self._authored_act_names(tool)
        payload_names = self._payload_names(request.get("payload"), tool)
        # Names in the payload must be the authored names: the read-back the
        # administrator says yes to is rendered from them, so a caller-composed
        # name is the one fabrication this verb exists to refuse.
        for key, value in payload_names.items():
            if key in authored_names and value != authored_names[key]:
                raise EstablishmentValidationError(
                    f"the proposed payload's {key} does not match the authored "
                    + ".".join(ACT_CONFIG_KEYS)
                    + " block; the read-back carries the authored name"
                )
        contact_name = _require_display_name(
            request.get("contact_name")
            or payload_names.get("client_contact_name")
            or authored_names.get("client_contact_name"),
            "contact_name (or an authored client_contact_name)",
        )
        matter_type_name = _require_display_name(
            request.get("matter_type_name")
            or payload_names.get("matter_type_name")
            or authored_names.get("matter_type_name"),
            "matter_type_name (or an authored matter_type_name)",
        )
        if payload != authored:
            # The refusal names the FIELDS, never the two values: a refusal that
            # printed both sides would put a caller-supplied string into the
            # ledger and the reply, which is the one thing this comparison
            # exists to keep out.
            differing = sorted(f for f in authored if payload.get(f) != authored[f])
            raise EstablishmentValidationError(
                f"the proposed {tool} payload does not match this seat's authored "
                + ".".join(ACT_CONFIG_KEYS)
                + f" block on {differing}; the act carries the authored values, "
                "and changing them is a config change the firm makes"
            )
        # The hook may pass the block it read as well. It has to agree with the
        # copy THIS uid read, or the two are looking at different files.
        supplied_authored = request.get("authored")
        if supplied_authored is not None:
            if not isinstance(supplied_authored, dict) or {k: v for k, v in supplied_authored.items()} != authored:
                raise EstablishmentValidationError(
                    "the authored block supplied with this proposal disagrees with the "
                    "one the broker read from the seat config; refusing to choose "
                    "between two configs"
                )

        text = normalize_rule_text(
            act_readback_text(tool, authored, contact_name=contact_name, matter_type_name=matter_type_name)
        )
        payload_sha256 = _hash_text(json.dumps(authored, sort_keys=True, separators=(",", ":")))
        row = pending.create(
            scope="act",
            subject={"tool": tool, "payload_sha256": payload_sha256},
            text=text,
            instructed_by=instructed_by,
            # ALWAYS for an admin. An act is the firm's own record changing;
            # the person who may bless it is a Named Administrator, and the
            # seat-side gate decides who that is.
            for_admin=True,
            kind="tool_call",
            payload=authored,
        )
        self.ledger.append(
            {
                "action_type": ACT_PROPOSED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(
                    {
                        "proposal_id": row["proposal_id"],
                        "kind": "tool_call",
                        "tool": tool,
                        "instructed_by": instructed_by,
                        "source_ref": source_ref,
                        # The payload's DIGEST, not the payload. The commit row
                        # carries the same digest, so the two together prove the
                        # act performed is the act proposed without either row
                        # holding a second copy of the firm's values.
                        "payload_sha256": payload_sha256,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        return {
            "ok": True,
            "proposal_id": row["proposal_id"],
            "kind": "tool_call",
            "tool": tool,
            "payload": authored,
            "payload_sha256": payload_sha256,
            "for_admin": True,
            "expires_at": row["expires_at"],
            "readback": readback_for(row["proposal_id"], text, "tool_call"),
        }

    def act_commit(self, request: dict[str, Any]) -> dict[str, Any]:
        """Record that a proposed act was performed. Consumes the row exactly once.

        Called AFTER the tool succeeded, by the seat's post-tool hook. A failed
        tool call does not reach here: the row stays open for its TTL so the
        person's "yes" survives one transport failure rather than being spent by
        it.
        """
        pending = self._require_pending()
        row = self.claim_proposal(request, "act")
        tool = self._require_act_tool(request.get("tool"))
        stored_tool = row["subject"].get("tool")
        if tool != stored_tool:
            raise EstablishmentValidationError(
                f"act {row['proposal_id']} was proposed for {stored_tool!r}, not {tool!r}; "
                "refusing to commit a different act under a confirmation for this one"
            )
        stored_payload = row["payload"]
        if not isinstance(stored_payload, dict):
            raise EstablishmentValidationError(
                f"act {row['proposal_id']} holds no payload; nothing can be committed under it"
            )
        supplied = request.get("payload")
        if supplied is not None:
            if self._require_act_payload(supplied, tool) != stored_payload:
                raise EstablishmentValidationError(
                    f"payload does not match act {row['proposal_id']} as it was proposed "
                    "and confirmed; the act carries the proposal's values, not this "
                    "request's"
                )
        confirmed_by = require_address(request.get("confirmed_by"), "confirmed_by")
        confirmed_message_id = _require_text(
            request.get("confirmed_message_id"), "confirmed_message_id", _MAX_SHORT_TEXT
        )
        outcome_raw = request.get("outcome")
        if outcome_raw is not None and not isinstance(outcome_raw, dict):
            raise EstablishmentValidationError("outcome must be an object when present")
        outcome = outcome_raw or {}

        run_id = secrets.token_hex(16)
        if not pending.consume(row["proposal_id"], run_id):
            raise EstablishmentValidationError(f"act {row['proposal_id']} was already committed; it has been done")

        payload_sha256 = _hash_text(json.dumps(stored_payload, sort_keys=True, separators=(",", ":")))
        metadata = {
            "proposal_id": row["proposal_id"],
            "run_id": run_id,
            "kind": "tool_call",
            "tool": tool,
            "instructed_by": row["instructed_by"],
            # WHO said yes and IN WHICH MESSAGE. The pair is what makes the
            # confirmation joinable to the inbound row that carried it
            # (ss#2497), so "an admin approved this" is checkable rather than
            # asserted.
            "confirmed_by": confirmed_by,
            "confirmed_message_id": confirmed_message_id,
            "payload_sha256": payload_sha256,
            # A bounded rebuild of the tool's own result. Three fields, each
            # read for its type: what the vendor returned is not forwarded.
            "created": bool(outcome.get("created")),
            "pending": bool(outcome.get("pending")),
            "matter_id": _bounded_str(outcome.get("matter_id")),
        }
        self.ledger.append(
            {
                "action_type": ACT_COMMITTED_ACTION_TYPE,
                "actor": "operator",
                "actor_role": "agent",
                "metadata": json.dumps(metadata, sort_keys=True, separators=(",", ":")),
            }
        )
        return {
            "ok": True,
            "proposal_id": row["proposal_id"],
            "run_id": run_id,
            "tool": tool,
            "payload_sha256": payload_sha256,
        }

# Author-built MCP connectors

When the Operator must connect to a system that has **no acceptable vendor or
community MCP server**, we author one. This directory is where those connectors
live. See **ADR 0053** for the doctrine.

A connector here is a **Python stdio MCP server** that is baked into the shared
Operator image, registered once in the overlay
(`hermes-smd-overlay` `MCP_CONNECTOR_REGISTRY`), and **activated per-customer**
only when a `customer.yaml` binds it (`backend: mcp:<name>`). A connector that no
customer binds is inert — it is never launched, surfaces no tools, and receives
no secrets, exactly like a skill in the catalog that no persona enables. There is
no separate repo, release pipeline, or runtime fetch.

## Layout

```
_sdk/         the shared contract every connector is built on (NOT published):
              ConnectorServer (non-empty inputSchema), the manifest schema, and
              the conformance harness with the runtime-name oracle.
_reference/   a SYNTHETIC self-test connector (echo / record / surprise). It is
              not a vendor integration — it proves every rail of the platform,
              including fail-closed refusal of an unclassified tool.
```

## Adding a connector (the declarative contract)

1. **Write the package** under `connectors/<name>/` — a stdio MCP server built on
   `ConnectorServer`, exposing a `<name>-mcp` console-script
   (`[project.scripts]` in its `pyproject.toml`).
2. **Ship a `manifest.toml`** — capability, required-secret _names_, `auth_model`,
   static launch env, and `tool_classes` (the conformance **oracle**, keyed by
   bare tool name).
3. **Add a conformance test** under `connectors/<name>/tests/` that runs
   `operator_connector_sdk.conformance` against the server.
4. **Register it** in the overlay: one `McpConnectorSpec` (with the absolute venv
   `command` and `auth_model`) **and** the `mcp_<server>_<tool>: ActionClass`
   literal lines in `shared/action_classes.py`. The literal map is the enforced
   authority; the manifest is checked _against_ it. This is the deliberate
   two-repo review gate for any tool that can take an action.
5. **Bind it** in a `customer.yaml` (`backend: mcp:<name>`).

The image install (one isolated venv per connector), the secret staging, and the
fail-closed governance are built once and apply to every connector — adding #N is
declarative, not bespoke wiring.

## Writes on a matter the model chose (the resolution gate)

A write tool whose target matter comes from the model, not from an object it
already names, is declared once at registration:
`server.tool(requires_resolution=matter_arg())` (`_sdk/operator_connector_sdk/resolution.py`).
`matter_arg("<argument>")` reads the target from that argument; any callable
taking the call's arguments and returning a matter id or None also works. The
registered MCP tool then gains a required `matter_resolution` argument and one
sentence in its description, and the SDK claims the token for that matter before
the body runs, puts it back if the body raises, and spends it when the body
returns. Python callers of the decorated name are unchanged. A connector supplies
the gate with `server.install_resolution_gate(...)` (the token store, the mode,
where an observation is recorded) and names its minting tools in
`ConnectorServer(name, resolvers=(...))`. In `observe` mode a call the gate would
refuse is recorded and dispatched; in `enforce` mode it is refused. Conformance
(`check_resolution_schemas`, part of `run_all`) fails a gated tool whose schema
does not list `matter_resolution` as required. A tool that checks a resolution in
its own body must not also declare one; the SDK refuses the registration.

The Smokeball connector's gate is `smokeball/smokeball_connector/resolution_gate.py`;
its switch is `matter_resolution_mode: observe | enforce` on the `mcp:smokeball`
connector entry in `customer.yaml` (absent is `observe`), and a refusal it would
have made is a `SMOKEBALL_WRITE` audit row with phase `would_refuse` (tool,
matter, reason code). It mints from `resolve_invoice_matter` (a letter's facts)
and `resolve_matter_by_id` (a matter id and the number read with it).

## A check that can refuse ships switched off

A merge reaches a client seat without a person, so a check that can refuse an act
or change what a firm sees reads its switch from the seat's `logic_in_code` block
on every call (`docs/specs/operator/customer-yaml-schema.md`, ADR 0104). In the
Smokeball connector every such check asks `smokeball_connector/logic_gate.py`:
under `enforce` it refuses; under `observe`, the default, it writes an observation
row under `$SMD_POST_JOURNAL_DIR/observations/` and the call goes on as if the
check were not there. A check that only observes never pays for a model read.

**What a model reads ships with the skills, not the code.** A tool's required
arguments, its docstring and a new tool's listing can change a seat's behaviour
with no skill changing. So the code PR adds only optional arguments and keeps the
words, and the words that tell the model to fill them ride the skills PR.
`tests/test_logic_switches.py` pins the listing of each changed tool.

## What the platform generalizes — and does not

The connector **lifecycle** (author → install → activate → govern → verify) is
general. The tool **vocabulary** is not — skills stay connector-native by design.

# Research: exemplar product and architecture explainers

Researched 2026-11-09. Purpose: find out what "professional, accurate, detailed" looks like for
product and architecture explainers that technical sellers and technical buyers use, so the
visual briefs in this kit can copy the conventions of the genre.

## How to read this file

- Every URL below was loaded during this research. Pages that failed to load are listed in
  "Not cited" at the end and are not used as evidence.
- "Seen" means the diagram image itself was rendered and looked at. "Text only" means only the
  page text, captions and alt text were read; visual claims about those are marked as inference.
- Anything that is my reading rather than something the source states is marked **(inference)**.
- No vendor here is a model for our positioning. They are models for diagram craft only.

## 1. Exemplars examined

Sixteen pages were examined. Five are selected (section 2). The table is the full field.

| #   | Exemplar                                                                   | URL                                                                                                                 | Evidence                                | Verdict                                                      |
| --- | -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------ |
| 1   | Cloudflare, "Enterprise AI agent workspace" reference architecture diagram | https://developers.cloudflare.com/reference-architecture/diagrams/ai/enterprise-ai-agent-workspace/                 | Seen (both diagrams)                    | Selected                                                     |
| 2   | Microsoft, "Data protection and auditing for Microsoft Copilot"            | https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-architecture-data-protection-auditing | Seen (2 of 3 diagrams)                  | Selected                                                     |
| 3   | Tailscale, "How Tailscale works"                                           | https://tailscale.com/blog/how-tailscale-works                                                                      | Seen (2 of 9 figures), captions for all | Selected                                                     |
| 4   | HashiCorp Vault, "Architecture"                                            | https://developer.hashicorp.com/vault/docs/internals/architecture                                                   | Seen                                    | Selected                                                     |
| 5   | Palantir, "AIP architecture"                                               | https://www.palantir.com/docs/foundry/architecture-center/aip-architecture                                          | Seen                                    | Selected (as the dense "component map" model, with cautions) |
| 6   | Microsoft Copilot Studio, "Define your solution architecture"              | https://learn.microsoft.com/en-us/microsoft-copilot-studio/guidance/architecture-overview                           | Seen (architecture overview)            | Strong, not selected (too dense for a seller)                |
| 7   | Anthropic, "Building effective agents"                                     | https://www.anthropic.com/engineering/building-effective-agents (redirects to /research/building-effective-agents)  | Seen (1 of 8), captions for all         | Strong for the sequence view                                 |
| 8   | Glean, "Query flow" (security docs)                                        | https://docs.glean.com/security/architecture/data-flow/query                                                        | Seen (1 of 4)                           | Mixed: excellent content, weaker craft                       |
| 9   | Stripe, "How Payment Intents and Setup Intents work"                       | https://docs.stripe.com/payments/paymentintents/lifecycle                                                           | Text only                               | Strong for the state / lifecycle table                       |
| 10  | Temporal, "Workflow Execution"                                             | https://docs.temporal.io/workflow-execution                                                                         | Text only                               | Useful for the status diagram idea                           |
| 11  | Fly.io, "The Fly.io Architecture"                                          | https://docs.fly.io/reference/architecture                                                                          | Text only (page has no diagrams)        | Useful for prose with concrete numbers                       |
| 12  | Snowflake, "Key concepts and architecture"                                 | https://docs.snowflake.com/en/user-guide/intro-key-concepts                                                         | Seen                                    | Weak visual, used as an anti-pattern                         |
| 13  | Salesforce, Atlas reasoning engine page                                    | https://www.salesforce.com/agentforce/what-is-a-reasoning-engine/atlas/                                             | Text only                               | Weak, used as an anti-pattern                                |
| 14  | Glean, security marketing page                                             | https://www.glean.com/security                                                                                      | Text only                               | Weak, used as an anti-pattern                                |
| 15  | Harvey, security page                                                      | https://www.harvey.ai/security                                                                                      | Text only (no diagrams)                 | Good claims list, no architecture view                       |
| 16  | Sierra product page; Decagon product overview                              | https://sierra.ai/product and https://decagon.ai/product/overview                                                   | Text only                               | Marketing surface, no architecture view                      |

Guidance sources (not exemplars, used for the quality bar):

- C4 model: https://c4model.com/ , https://c4model.com/diagrams/notation ,
  https://c4model.com/diagrams/checklist , https://c4model.com/diagrams/system-context ,
  https://c4model.com/diagrams/dynamic , https://c4model.com/diagrams/deployment
- Microsoft Azure Well-Architected, "Create architecture design diagrams":
  https://learn.microsoft.com/en-us/azure/well-architected/architect-role/design-diagrams
- Cloudflare docs style guide, reference architecture diagram content type:
  https://developers.cloudflare.com/style-guide/documentation-content-strategy/content-types/reference-architecture-diagram/
  and reference diagram component:
  https://developers.cloudflare.com/style-guide/documentation-content-strategy/component-attributes/reference-diagram
- Cloudflare, how the reference architecture section is organized:
  https://developers.cloudflare.com/reference-architecture/how-to-use/
- Product Marketing Alliance, "Build your best marketecture":
  https://www.productmarketingalliance.com/build-your-best-marketecture
- AWS Builder Center, "How to design better AWS architecture diagrams" (community article):
  https://builder.aws.com/content/3DttzYU07FWj09CqNczGbfSnYF4/how-to-design-better-aws-architecture-diagrams
- AWS Architecture Icons: https://aws.amazon.com/architecture/icons/
- Stripe, "How Stripe builds interactive docs with Markdoc": https://stripe.dev/blog/markdoc

## 2. The five selected exemplars

### 2.1 Cloudflare: Enterprise AI agent workspace (the closest structural match to our product)

URL: https://developers.cloudflare.com/reference-architecture/diagrams/ai/enterprise-ai-agent-workspace/

Why it matters to us: it explains a governed AI agent with per-workspace isolation, verified
access, governed model and tool access, curated knowledge and durable outputs. That is nearly
the same shape as a per-customer governed AI worker.

**Views on the page**

| View                                | Altitude                                              | What it shows                                                                                                                                                                        |
| ----------------------------------- | ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Core architecture                   | System context plus request lifecycle, in one picture | Five dashed groups laid out left to right: ways to invoke, access check, agent workspace, secure access to models and tools, outputs. Four numbered black markers sit on the arrows. |
| Workspace isolation and state model | Component / deployment                                | One stateless router fans out to one object per user and one per workspace, which in turn own an isolated code runner, an on-demand container and file storage.                      |

**What makes it credible**

- Every box has a name and a one-line job in smaller type under it. Example pattern: component
  name in bold, then a phrase such as "one container per workspace for coding workflows".
  Cardinality ("one per user", "one per agent workspace", "per call") is written on the box.
- Four numbered steps on the diagram match four numbered paragraphs under it: invoke work,
  reach the agent workspace, use governed models and tools, save an output. Each paragraph says
  what is checked at that step (for example that asynchronous channels validate sender,
  signature or token before a request is accepted).
- The text states what stays outside the workspace: provider routing, credentials, policy and
  logging. It states that generated code and model context never receive model or tool
  credentials. That is a "what crosses which line" statement, which is the detail technical
  buyers probe for.
- A "Security model" section lists seven control areas, and an audit paragraph lists what is
  recorded (model usage, tool calls, consent decisions, output sharing, lifecycle events).
- A scope table says what the architecture is and is not.
- Honest gap: the page gives no quantitative limits.

**Visual conventions (seen)**

- Strict left to right flow. One row, with two satellite groups above and below.
- Two colors with fixed meanings: blue dashed outline and pale blue fill for things outside the
  platform (entry channels, model providers, tool servers, outputs); orange dashed outline and
  pale orange fill for the vendor's own components. No third color.
- Group labels sit in the top border of each dashed box, in the group color.
- One icon per vendor component, all from one line-icon family, all the same size and color.
  Generic items (web UI, chat and email, documents) get no icon at all, only a label.
- Black filled circles with white numerals for steps. Plain black arrows, single headed.
- Title in the bottom left corner in small caps, vendor logo bottom right. Faint dot grid
  background. Same frame on both diagrams, so they read as a set.
- Roughly 20 labeled elements. Readable at slide size without zooming.

**Industry neutrality:** fully neutral. Outputs are named generically (documents, apps,
workflows). Entry channels are generic. No vertical example appears in the diagram; the prose
mentions kinds of work (research, spreadsheets, source code).

**Accompanying text:** introduction, numbered step prose, state and isolation model, governed
access, skills and context, security model, related resources. The style guide requires that
every diagram has a caption, numbered callouts, and a prose equivalent for each callout.

### 2.2 Microsoft: Data protection and auditing for Copilot (the model for the trust view)

URL: https://learn.microsoft.com/en-us/copilot/microsoft-365/microsoft-365-copilot-architecture-data-protection-auditing
(the page reports its canonical address as learn.microsoft.com/en-us/copilot/plan/architecture-data-protection-auditing)

Why it matters to us: governance is the center of our product, and this page is a full
explainer made only of trust diagrams. Each diagram answers one buyer question.

**Views on the page**

| View                                                        | Altitude                     | The question it answers                                             |
| ----------------------------------------------------------- | ---------------------------- | ------------------------------------------------------------------- |
| How the assistant works with labels and encryption (seen)   | Data flow and trust boundary | What can it read, and what protections does it honor?               |
| How access controls affect what it can discover (text only) | Permission model             | What limits what it can find, without changing user permissions?    |
| Usage data and tools for auditing (seen)                    | Audit and record map         | Where is every interaction stored, and which tool reads each store? |

**What makes it credible**

- The trust boundary is drawn and named. A rounded outline is labeled as the service boundary,
  a nested one as "your tenant", and a separate box as "external data" with a small note saying
  what that means (local, network or other cloud storage).
- The audit diagram is a two-column map: on the left, five named storage locations each with a
  named product and a one-line description of what is in it ("hidden folder with user prompts
  and responses"); on the right, three named tools. Numbered lines connect each tool to each
  store. Numbers 1 to 7 match a numbered list in the text.
- The text states limits honestly. It says the audit record shows who, when, where and which
  resources were accessed, and that it does not contain the prompt or response text, and names
  the tool to use for that text. It lists three protection configurations the assistant cannot
  access at all.
- Each diagram is followed by bullets that begin with a bold rule ("Permissions come first").

**Visual conventions (seen)**

- One title sentence at the top of each diagram that says exactly what it shows.
- Containment (boxes inside boxes) carries the boundary meaning. Arrows are few.
- Solid lines versus dashed lines distinguish two tools' reach. This is the one weakness: there
  is no legend for solid versus dashed.
- Official product icons only, one per row, with the product name beside each.
- Soft gradient background, white cards, dark text. Low saturation everywhere except icons.
- About 12 to 15 elements per diagram.

**Industry neutrality:** fully neutral. No vertical examples. The "user" is "User A".

**Accompanying text:** an opening paragraph that says who the article is for and what it
explains, then one section per diagram, then "to learn more" links.

### 2.3 Tailscale: How Tailscale works (the model for progressive build-up)

URL: https://tailscale.com/blog/how-tailscale-works

Why it matters to us: it is the best known example of teaching a product to a technical
audience by building the picture one idea at a time. A visual learner follows nine small
diagrams far more easily than one large one.

**Views on the page (nine figures, captions read for all; figures 4 and 8 seen)**

| Figure    | Altitude             | Role in the argument                                                  |
| --------- | -------------------- | --------------------------------------------------------------------- |
| 1, 2a, 2b | Executive concept    | The old way and why it hurts (hub and spoke, inefficient routing)     |
| 3         | Executive concept    | The new shape (point to point mesh)                                   |
| 4         | System context       | The one central service and what it does and does not carry           |
| 5         | Request lifecycle    | Login flow through an outside identity provider                       |
| 6, 7      | Deployment / network | The hard cases (both ends behind firewalls, relay fallback)           |
| 8         | Permission model     | Central policy, enforced at each node, with allowed and blocked paths |

Audit logs and incremental deployment are covered in closing prose sections.

**What makes it credible**

- It starts from the problem the buyer already has, then changes one thing per figure.
- Concrete arithmetic in the prose: a 10 node mesh needs 90 tunnel endpoint configurations.
- The permission figure (seen) shows three example users with example addresses, a green path
  with a check mark, a red path with a cross and a "Blocked by ACL" tag, and beside it the
  actual policy text that produces that result. The reader sees the rule and its effect in one
  frame. This is the single most transferable idea for customer-authored permission limits.
- It says plainly what the central server does not do: it carries keys and metadata, not data.
- Each connection is logged at both ends, stated as a number (two logs per connection).

**Visual conventions (seen)**

- Identical canvas size for every figure, warm off-white background, grey rounded boxes.
- One dark box for the vendor's own component. Everything else is grey.
- Color only for meaning: blue for control connections, green for allowed, red for blocked.
- No icons except one small device glyph. No decoration.
- Six to twelve elements per figure.
- Captions are full sentences that state the takeaway, not just the subject.

**Industry neutrality:** fully neutral. One everyday example (a worker in one city reaching a
server in the same city via a head office elsewhere).

**Accompanying text:** long form prose under two headings (data plane, control plane), with
"bonus" sections for policy, audit logs and incremental deployment.

### 2.4 HashiCorp Vault: Architecture (the model for a plain component diagram with a boundary)

URL: https://developer.hashicorp.com/vault/docs/internals/architecture

Why it matters to us: it shows that a credible component view can be black boxes on white, with
one boundary and an audit component drawn as a first-class part.

**Views on the page**

| View                         | Altitude                      | What it shows                                                                                                                                                             |
| ---------------------------- | ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Architecture overview (seen) | Component with trust boundary | An API bar on top, an untrusted storage bar at the bottom, and between them a frame labeled "Barrier" containing ten named components and the routing arrows between them |
| Unseal keys (text only)      | Mechanism detail              | How key shares reconstruct the key that unlocks the store                                                                                                                 |

**What makes it credible**

- Twelve boxes, every one a real, named part: token store, policy store, core, path routing,
  secret engine, auth method, expiration manager, rollback manager, audit broker, two audit
  devices. Nothing generic.
- The trust boundary is a thick black bar with one word on it. What is inside is trusted; the
  storage bar outside it is described in the text as untrusted, holding only encrypted data.
- The audit path is in the picture: one broker fanning out to multiple audit devices.
- The prose walks one request end to end: authenticate, get a token tied to policies, present
  the token, policy check with deny by default, route to an engine, attach a lease, send
  request and response to the audit broker.

**Visual conventions (seen)**

- Top to bottom: caller interface, protected interior, storage.
- Black and white only. No icons. No color. Uniform box style, one typeface.
- Arrows only where routing actually happens (five arrows in total).
- No legend and no numbered steps on the picture; the numbering lives in the prose.

**Industry neutrality:** fully neutral, no examples in the diagram.

**Accompanying text:** short overview, then a paragraph per phase of a request.

### 2.5 Palantir: AIP architecture (the model for the dense "everything on one page" map)

URL: https://www.palantir.com/docs/foundry/architecture-center/aip-architecture

Why it matters to us: it is the reference a technical seller keeps open during a deep
conversation. It shows how to keep a dense map navigable. It is also a warning about density.

**Views on the page**

| View                         | Altitude                       | What it shows                                                                                                                                                                        |
| ---------------------------- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Architecture overview (seen) | Component map / capability map | Twelve numbered capability groups, each a labeled panel with sub-boxes, connected by arrows, plus a panel on the right naming four kinds of teams and "automations" as the consumers |

**What makes it credible**

- A numbered index card on the left lists the twelve groups; each panel carries the same
  number; the text below the diagram has twelve numbered sections in the same order. The
  number is the navigation system.
- Security and governance is drawn as a horizontal band between the data layer and the agent
  layer, naming the control types (role, marking and purpose based controls, approvals,
  checkpoints). The text says every human and agent operation passes those controls and is
  recorded in audit logging.
- Model access is its own group with three named sub-functions (moderation, infrastructure,
  validation and oversight) and the text says transmitted data is not retained.
- Users are drawn: four named team types sit on the diagram as the people the system serves.

**Visual conventions (seen)**

- Grey panels on a white field, small uppercase panel titles, numbered pill tags.
- One accent color reserved for the core of the system. Icons are small and uniform.
- Solid arrows for primary flows, dashed for supporting ones (no legend).
- Around 70 labeled elements. It needs zooming. This is a reference sheet, not a slide.

**Industry neutrality:** fully neutral; consumers are roles (operational, developer, analyst,
governance teams), not industries.

**Accompanying text:** a two sentence introduction, then twelve numbered paragraphs.

**Caution:** copy the numbered index and the governance band. Do not copy the density for
anything shown in a first conversation.

## 3. Notes on the examined but not selected

- **Microsoft Copilot Studio architecture overview** (seen). Three-zone layout: clients on the
  left, the product in a tinted center panel split into nine named groups (runtime,
  integrations, lifecycle, dialog management, language understanding, generative answers,
  security, triggers, analytics), outside services on the right. Grey means outside the
  product, tint means inside. The security group lists concrete items (end user authentication
  modes, data policies, audit logs, network firewall). The accompanying text walks the diagram
  left, center, right. Very complete, about 90 elements, and too dense for a seller's first
  three views. The left / center / right zoning is worth copying.
- **Anthropic, building effective agents** (1 of 8 seen). Eight diagrams, all the same wide
  canvas, each named for one pattern. The one seen is a sequence diagram: four lanes (human,
  interface, model, environment), labeled arrows with a verb on each, and two dashed loop
  boxes titled with their exit condition ("until tests pass"). Color identifies the lane and
  nothing else. This is the cleanest model found for a request lifecycle with a retry loop and
  a human checkpoint.
- **Glean query flow** (seen). Strong content: real hostnames, a per-tenant endpoint with a
  uniquely assigned address, a numbered three-step login, a boundary labeled as the customer's
  deployment. Weaker craft: screenshots pasted into the diagram, heavy arrows, generic cylinder
  shapes with no labels. Lesson: specificity makes it credible even when the drawing is rough.
- **Stripe lifecycle** (text only). The lifecycle is carried by a table: one row per named
  state, with what causes the state and what the reader should do. A state table is a
  legitimate "view" and costs nothing to produce. Stripe states that its docs diagrams are
  built in code from reusable elements, so they restyle together.
- **Temporal workflow execution** (text only). Includes a status diagram with two top-level
  groups (open, closed) and named sub-statuses. Useful precedent for showing the states a piece
  of work can be in.
- **Fly.io architecture** (text only, no diagrams). Shows that plain prose with hard numbers
  (core counts, memory ranges) and a named example path between two cities reads as credible
  without any picture.
- **Harvey security** (text only, no diagrams). A well ordered claims list: definitions of
  data categories, training stance, residency options, retention, access controls, named
  third-party testers, certifications. Good model for the text panel beside a trust diagram.
  It does not explain architecture.

## 4. The canonical set of views

Ordered the way a seller would use them. Each view answers one question. Views 1 to 4 are the
core; a seller who has only those can run a first technical conversation. Views 5 to 8 are the
proof layer for security, IT and operations people.

| #   | View                                                       | The question it answers                                                                          | Altitude                   | Detail expected                                                                                                                                                                                                                                                       | Closest exemplar                                                                                  |
| --- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1   | **Before and after** (the shape of the idea)               | What is this, compared with what the buyer does today?                                           | Executive concept          | Two or three small frames, 6 to 10 elements each. One change per frame. A takeaway sentence as caption. No component names beyond the product itself.                                                                                                                 | Tailscale figures 1 to 3                                                                          |
| 2   | **System context** (the worker in the customer's world)    | What does it touch, who talks to it, and who runs what?                                          | System context             | 10 to 15 elements. People (named roles), the worker as one box, the customer's existing systems as generic named categories, the vendor's managed service, the model provider. Three zones: customer, per-customer instance, vendor. Every arrow labeled with a verb. | Cloudflare core architecture; Copilot Studio zoning                                               |
| 3   | **Request lifecycle** (one piece of work, start to finish) | What actually happens when someone asks it to do something?                                      | Dynamic / sequence         | 5 to 8 numbered steps on the context picture, or a lane diagram. Must include the permission check, the action on a customer system, the audit write, and the point where a human approves or is told. Loops and stops shown with their exit condition.               | Cloudflare numbered steps; Anthropic sequence diagram                                             |
| 4   | **Permission model** (limits the customer writes)          | What can it never do, who decides, and how is that enforced?                                     | Trust / policy             | One rule shown next to its effect: an allowed path and a blocked path, with the rule text or table that produces them. State who authors the rule, where it is stored, where it is enforced, and the default when no rule matches.                                    | Tailscale figure 8; Vault deny-by-default prose                                                   |
| 5   | **Isolation and deployment** (one instance per customer)   | Is my data mixed with anyone else's? What is mine alone and what is shared?                      | Deployment                 | Two or three customer instances side by side with identical internals; a clearly separate shared layer. Cardinality written on every box ("one per customer"). A short list of what is never shared.                                                                  | Cloudflare isolation and state model; Glean per-tenant endpoint                                   |
| 6   | **Data flow and trust boundaries**                         | What data crosses which line, in what form, and what never leaves?                               | Data flow / trust boundary | Named boundaries drawn as outlines. Each crossing arrow labeled with what crosses and how it is protected. A stated list of what does not cross (credentials, for example). Retention stated per store.                                                               | Microsoft labels and encryption diagram; Vault barrier                                            |
| 7   | **Audit and record map**                                   | What is recorded, where does each record live, who can read it, can it be changed?               | Audit / data map           | Two columns: stores on the left (each with one line on what it holds), readers and tools on the right, numbered lines between. State what the record contains and what it does not.                                                                                   | Microsoft auditing diagram                                                                        |
| 8   | **Memory and state**                                       | What does it remember about my business, where, for how long, and how do I correct or remove it? | Component / state          | A small diagram or a table: each kind of memory, where it lives, what writes it, what reads it, how it is removed. A state table for a piece of work (queued, running, held for a person, delivered, failed).                                                         | Stripe state table; Temporal status diagram                                                       |
| 9   | **Managed service and responsibilities**                   | Who does what after go-live? What does the vendor watch and fix?                                 | Operating model            | A responsibility table (customer, vendor, shared) and one loop diagram: monitor, detect, fix, release, report. Named cadences only where they are contractually true.                                                                                                 | Copilot Studio lifecycle diagram; Glean shared responsibility page (named in its index, not read) |
| 10  | **Component reference map** (the leave-behind)             | Where does every part live?                                                                      | Component map              | One dense page, numbered groups with a numbered index and matching numbered text. For deep dives only, never a first slide.                                                                                                                                           | Palantir AIP overview                                                                             |

**The three altitudes.** The C4 model says a context diagram is for everyone, technical or
not, and that context plus one level down is enough for most audiences; deeper levels only
where they add value. Azure's guidance says the same thing in different words: start broad,
narrow progressively, then add scenario and cross-cutting views, and layer rather than
overload. Mapped to the list above: altitude one is views 1 and 2 (anyone), altitude two is
views 3 to 5 (technical buyer), altitude three is views 6 to 10 (security, IT, operations).
A seller should be able to stop at any altitude and still have told a complete story.

**Where vertical examples go (inference from all five selected exemplars).** None of the five
puts an industry into the architecture diagram. Systems are named by category (identity
provider, document store, tool server, model provider), users by role. Vertical content, where
vendors have it, lives in separate pages, case studies or navigation (Sierra and Decagon both
do this). The convention to copy: keep every architecture view neutral, and add vertical
flavor only in view 3, as an interchangeable example of "one piece of work" told in a caption
or a side panel, so the same diagram serves any industry by swapping one sentence.

## 5. Quality bar checklist

Each item is checkable by looking at the artifact. Sources in brackets.

**Frame**

1. The diagram has a title that states its type and scope in plain words. [C4 notation]
2. It answers exactly one question, and the question could be written above it. [Azure: layer, do not overload]
3. Every diagram in the set uses the same canvas, type, box style and color meanings, so they read as one family. [Azure: maintain consistency; seen in Cloudflare, Tailscale, Anthropic]
4. It carries a date or version, and someone owns keeping it true. [Azure: include metadata]

**Elements**

5. Every box has a specific name and a one-line responsibility. No box says only "AI", "platform", "data" or "security". [C4 notation; seen in Cloudflare, Vault]
6. Cardinality is written where it matters: "one per customer", "one per request", "shared". [seen in Cloudflare]
7. People appear as named roles, and it is clear who owns each zone. [C4 system context; seen in Palantir]
8. Things outside the product are visibly different from things inside it, by one consistent treatment. [seen in Cloudflare (blue versus orange), Copilot Studio (grey versus tint)]
9. Generic categories get generic shapes; vendor logos are used only for that vendor's actual product. [Azure: do not substitute logos for concepts]

**Relationships**

10. Every arrow has one head and a label that says what moves or what is done. No bare "uses". [C4 notation; Azure: avoid bidirectional arrows]
11. Where order matters, steps are numbered on the picture and the same numbers appear in the text beside it. [Cloudflare style guide; seen in Cloudflare, Microsoft, Palantir, Glean]
12. Line styles (solid, dashed) mean something, and the meaning is stated in a legend. [C4 checklist; Azure: provide a legend]

**Trust**

13. Trust boundaries are drawn as named outlines, not implied. [Azure data-flow diagram guidance; seen in Microsoft, Vault, Glean]
14. For each boundary crossing, the artifact says what crosses and how it is protected. [Azure]
15. The artifact states at least one thing that does not happen: what never crosses, what is not recorded, what the system cannot reach. [seen in Cloudflare, Microsoft, Tailscale]
16. The default is stated (what happens when no rule matches). [seen in Vault and Tailscale prose]
17. The audit path is in the picture as a component with a destination, not a badge. [seen in Vault, Microsoft]

**Truth**

18. Every element corresponds to a real, shipped part. Nothing aspirational is drawn as present. [Azure: be accurate]
19. Where numbers exist they are given (counts, sizes, durations, retention). Where they do not, the artifact does not invent them. [seen in Fly.io, Tailscale]
20. A technical person at the vendor has reviewed it against the running system. [inference from Azure "be accurate" and the marketecture guidance about alignment]

**Readability**

21. Color is used for meaning only, with at most three meanings, and no meaning depends on color alone. [Azure: accessibility; seen in Tailscale]
22. One icon family, one size, and icons only where they identify a specific product. [seen in Cloudflare; Azure: official icons]
23. First-conversation views hold roughly 8 to 20 elements and are legible on a shared screen without zooming. Reference maps may be denser but carry a numbered index. [inference from element counts in the exemplars]
24. Flow runs in one direction, left to right or top to bottom, and does not double back. [seen in Cloudflare, Vault; AWS community article]
25. The picture works without its text, and the text works without the picture. [C4 notation; Cloudflare style guide]

## 6. Anti-patterns seen in weak examples

| Anti-pattern                                                                                                                                    | Where seen                                                                                                                                                                              | Why it fails                                                                                                                                         |
| ----------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Illustration in place of mechanism.** Cartoon characters, people at screens, floating chips, shields and gears beside a "how it works" claim. | Salesforce Atlas page: no diagram at all; four illustrated cards and a mascot (text only, from image descriptions).                                                                     | Nothing can be pointed at. A technical buyer learns nothing they can check.                                                                          |
| **Icon soup.** A row of generic icons, each with a one-word label, grouped in a dotted cloud.                                                   | Snowflake overview diagram (seen): three dotted bands holding icons labeled security, management, metadata, optimization.                                                               | The labels are categories, not parts. There are no relationships, so there is no architecture, only a list with pictures.                            |
| **The layer cake with no arrows.** Stacked bands that imply "built on" but show no request, no data movement and no boundary.                   | Snowflake (seen).                                                                                                                                                                       | Says what exists, not how it works. Fine as view 1, wrong as the only view.                                                                          |
| **Security as a badge wall.** Certification logos, statistics and a shield graphic standing in for a trust architecture.                        | Glean security marketing page (text only): six "control points" all showing the same reused image; a section still holding placeholder text. Harvey: claims list with no boundary view. | Badges prove an audit happened. They do not show where data goes. Reused or placeholder art signals that nobody checked.                             |
| **Unlabeled shapes.** Cylinders, clouds or boxes with no name.                                                                                  | Glean query flow (seen): six unlabeled cylinders inside the tenant boundary.                                                                                                            | The reader cannot tell what is being isolated.                                                                                                       |
| **Screenshots inside diagrams.**                                                                                                                | Glean query flow (seen).                                                                                                                                                                | Dates quickly, breaks scale, and mixes two visual languages.                                                                                         |
| **Line styles with no legend.**                                                                                                                 | Microsoft auditing diagram and Palantir map (both seen).                                                                                                                                | Even strong diagrams lose a point here; the reader has to guess what dashed means.                                                                   |
| **Everything on one page, shown first.**                                                                                                        | Palantir (about 70 elements) and Copilot Studio (about 90), if used as an opening slide.                                                                                                | Accurate but untellable. The marketecture guidance warns that a diagram with every capability is hard for sales to explain and for buyers to follow. |
| **All shine, no substance.** A single polished shape that hides the product.                                                                    | Described in the Product Marketing Alliance guidance as the opposite failure.                                                                                                           | Leaves the seller unable to answer the second question.                                                                                              |
| **Vertical lock-in inside the architecture.** Industry-specific systems or documents named in the core diagram.                                 | Not seen in any strong exemplar; this is the failure of our first attempt, named in the brief.                                                                                          | Every strong exemplar keeps the architecture neutral and puts industry in examples.                                                                  |
| **Metaphor as explanation.** "The brain", "the nervous system", a factory, an iceberg.                                                          | Salesforce text calls the engine the brain; no mechanism is drawn.                                                                                                                      | A metaphor names a role. It cannot be audited. Use at most one, in a caption, never as the picture.                                                  |

## 7. Recommended production approach for a small firm

### What the evidence says about how the exemplars are made

| Vendor          | What is known                                                                                                                                                                                         | Source                                                                                  |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Cloudflare      | Diagrams are published as SVG. The docs style guide says to use SVG where possible, prefers Mermaid (diagram as code) when it fits, and requires a caption, numbered callouts and a prose equivalent. | Cloudflare style guide pages (stated)                                                   |
| Cloudflare      | The reference diagrams share one frame, one icon family and one two-color scheme. They look like they come from a shared design template maintained by a design or solutions team.                    | **Inference** from the two diagrams seen. The tool is not stated anywhere I could load. |
| Stripe          | Docs diagrams are built in code from reusable elements using a diagramming library, so they can link, localize and restyle across the site at once.                                                   | Stripe engineering post (stated)                                                        |
| Tailscale       | Figures are SVG at one fixed canvas size, served from a content system. They appear to be exported from a vector design tool by a designer working to a small house style.                            | **Inference** from file names, dimensions and the figures seen.                         |
| HashiCorp Vault | A single black and white raster image, plain boxes. Could have been made in any basic diagram tool.                                                                                                   | **Inference** from the image seen.                                                      |
| Microsoft       | Raster images at high resolution with official product icons and a shared gradient style across the article. Microsoft publishes official icon sets and tells authors to use them unmodified.         | Azure guidance (stated for icons); house template is **inference**                      |
| Palantir        | One very large raster image with a consistent custom style. Almost certainly drawn by a designer in a vector tool.                                                                                    | **Inference**                                                                           |
| AWS             | Publishes an official icon package and slide toolkits, updated on a regular cadence, and lists third-party drawing tools that carry the icons.                                                        | AWS icons page (stated)                                                                 |

What this adds up to **(inference)**: exemplar-grade output does not come from a particular
tool. It comes from three things that every strong exemplar shares: (a) a small fixed visual
system (one canvas, one type size ladder, two or three colors with fixed meanings, one icon
family), (b) content written by someone who knows the real system, down to names and
cardinalities, and (c) numbered prose that pairs with the picture. The Vault diagram proves the
point: it is the plainest drawing in the set and one of the most trusted.

### Recommended approach

1. **Write the content model first, in text.** For each of the ten views: the question, the
   element list (name, one-line job, owner zone, cardinality), the arrow list (from, to, verb,
   what crosses), the numbered steps, and the "does not" statements. This is the part an
   outside designer cannot supply and the part our first attempt lacked. It should be checked
   against the running system by whoever knows it best.
2. **Draft every view as diagram-as-code** (Mermaid, D2 or a C4 tool such as Structurizr).
   Purpose: force every box and arrow to be named, keep the views consistent with each other,
   keep the source in the repository next to the product so it changes in the same pull request
   as the thing it describes. Azure's guidance asks for diagram sources to be version
   controlled with the workload; Cloudflare prefers code diagrams for searchability and ease of
   change. Honest limit: default layouts from these tools look like engineering documentation,
   not sales material. They are the accurate skeleton, not the finished artifact.
3. **Define a one-page visual system before drawing anything polished.** Canvas size, type
   ladder (title, box name, box description, annotation), box style, the color meanings
   (suggested: neutral for customer-owned, one accent for the product, one muted tone for
   vendor-run, plus green and red reserved for allowed and blocked), the numbered-step marker,
   boundary outline style, and a legend block. Copying the Cloudflare and Tailscale
   conventions directly is reasonable; both are simple enough to reproduce.
4. **Redraw the first-conversation views (1 to 5) in a vector design tool** such as Figma,
   exporting SVG. Two workable staffing options:
   - A freelance information designer or technical illustrator for a short engagement,
     handed the content model, the code drafts and the visual system. Best when polish
     matters and nobody in house has design craft. The brief must say that every label is
     fixed content and may not be reworded or dropped.
   - In house, by whoever built the code drafts, working strictly inside the visual system.
     Cheaper, slower to reach polish, and the Vault example shows it can still be credible.
5. **Leave the proof-layer views (6 to 10) closer to the code drafts**, restyled to the same
   colors and type. Security and IT readers reward precision over finish, and these views
   change most often.
6. **Review each view against the checklist in section 5** and have it checked for truth by
   someone who can compare it to the running system. The marketecture guidance suggests
   settling the diagram's purpose with stakeholders early and treating later feedback as a
   filter, so the story survives review.

Tools that suit the less important jobs **(inference, general practice, not from the
exemplars)**: a whiteboard-style tool such as Excalidraw or FigJam is good for working out
view 1 and for a seller sketching live in a call, and its hand-drawn look signals "draft", so
it should not be the finished artifact. A general diagram tool such as Lucidchart or draw.io
can produce Vault-grade component views if the visual system is applied strictly. Slide
software is where these end up, but building them there tends to produce the inconsistent
spacing and ad hoc color that read as amateur.

### What I could not establish

- No vendor examined publishes which design tool or which team produces its architecture
  diagrams, apart from Stripe (code) and Cloudflare (SVG and Mermaid as formats). Everything
  else about production in this section is inference from the artifacts.
- I found no single authoritative published standard for what a "first-call deck" or a
  "solution brief" must contain at enterprise vendors. The view set in section 4 is my
  synthesis from the exemplars plus the C4 and Azure guidance, not a citation of one.
- The one practitioner source on marketecture found and loaded (Product Marketing Alliance) is
  marketing guidance, not a technical standard. Its usable points: one main idea, one slide, a
  clear visual hierarchy, show where each audience fits, balance polish with substance, and
  treat it as a living document.

## 8. Not cited

These were tried and are not used as evidence.

- Salesforce Trust Layer help article: the page returned an error shell with no content. Only
  second-hand descriptions of its diagram were found, so it is not cited.
- Martin Fowler's marketecture article (PDF): the file loaded but its text could not be
  extracted, so its content is not cited.
- Two guessed Microsoft and Glean documentation addresses returned "not found"; the working
  addresses are the ones in section 1.
- The AWS tenant isolation whitepaper address returned a different document (the SaaS lens
  introduction) and an AWS prescriptive guidance address returned "not found"; neither is used.
- Sierra and Decagon were loaded as text only. The Decagon platform graphic was not seen, so
  nothing is claimed about how it looks.

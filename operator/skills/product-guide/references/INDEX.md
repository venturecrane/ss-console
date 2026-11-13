# Operator Product Library: Index

This is the index of the Operator product library. Every answer the guide gives comes from one of the documents below and names the document and the `##` section it came from. A question that no document covers is not answered from anywhere else.

## The explainer page

The reviewed explainer page shows the Operator in seven views, each with its own figure, followed by the service around it, what runs today and what SMD sets up, and one industry example. The page is at:

Explainer page: https://claude.ai/artifact/RPLJDHJduemvFrAvv9QQ1i

Each view, and each of the three closing sections, has its own anchor. A link to one view is the page address followed by the anchor, for example the page address followed by `#context` for the system context view. The anchors, and the section of [how-the-operator-works.md](how-the-operator-works.md) that carries each view's text:

| Anchor     | View                   | Section                                            |
| ---------- | ---------------------- | -------------------------------------------------- |
| #idea      | Before and after       | Before and after: what changes for the business    |
| #context   | System context         | System context: what it touches, and who runs what |
| #lifecycle | The life of a request  | The life of a request                              |
| #limits    | The permission model   | The permission model: the client writes the limits |
| #jobs      | Queued jobs            | Queued jobs: big jobs run as queued work           |
| #isolation | Isolation              | Isolation: one seat per client, and what is shared |
| #trust     | Data and audit record  | Data and audit record: where the data lives        |
| #service   | The managed service    | What managed means                                 |
| #edges     | Proven, built, planned | What runs today, and what SMD sets up              |
| #example   | One industry example   | The same model, in one industry                    |

## The documents

| Document                                                       | What it covers                                                                                                                                                 | Questions it answers                                                                                                                                   |
| -------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [how-the-operator-works.md](how-the-operator-works.md)         | The reviewed explainer, in seven views plus the service, the edges and one industry example, with each figure described as a numbered list                     | What is it, in one picture? How does a request flow? What does SMD run? What runs for a client today, and what does SMD set up?                        |
| [what-it-is.md](what-it-is.md)                                 | The definition, the five authored surfaces, industry packs, one example, a guide not a vendor, building it yourself, the self-serve Operator                   | What is the Operator? What is a seat? What does a client author? What is an industry pack, and which exist?                                            |
| [anatomy-of-a-seat.md](anatomy-of-a-seat.md)                   | The machine, the software image, system users, the control layer, the broker, the audit record, where data and configuration live, what SMD authors per client | What runs on a seat? How is it configured and changed? What is the routine grid? Where are secrets kept?                                               |
| [request-lifecycle.md](request-lifecycle.md)                   | How a request arrives, the 15 ordered checkpoints, the taint rule, outbound content gates, delivery paths                                                      | What happens to an email from arrival to reply? What stops a bad send? What happens with an unknown sender?                                            |
| [jobs.md](jobs.md)                                             | Queued jobs: job versus chat turn, kinds of queued job, the stages from request to delivery, states, holds, rehearsals and proof runs                          | How is long or costly work run? How is delivery proven? What is a proof run? What does the client hear when a job stops?                               |
| [permission-model.md](permission-model.md)                     | Kinds of action, the banned list, limits and ceilings, rules that hold everywhere, what is in use, pause and safety                                            | How are permissions set? What can never be turned on? What does "asks first" mean? How is the Operator stopped?                                        |
| [memory.md](memory.md)                                         | The memory lanes, who writes each, what is built and what is not, ownership and export                                                                         | What does it remember? Who owns that memory? Can the client see or delete it?                                                                          |
| [connections-and-channels.md](connections-and-channels.md)     | How connections are built, connections by client system type, email and other channels                                                                         | Which systems does it connect to? How do people reach it? Can it text, or work in team chat?                                                           |
| [isolation-and-data-custody.md](isolation-and-data-custody.md) | Isolation between clients, what SMD's console and agents can see, credential custody, the audit record, retention, model provider posture                      | How are clients kept apart? What can SMD see? Where are connection credentials? Is the record tamper-evident? Is client data used for training?        |
| [managed-service.md](managed-service.md)                       | Onboarding phases, releases, monitoring, incidents, change requests, obligations, offboarding, who does what                                                   | What does "managed" mean? How is it onboarded, released and watched? Who decides what?                                                                 |
| [client-controls.md](client-controls.md)                       | The Client portal: what it shows, what a client can change, what it leaves out                                                                                 | What can the client see and change? Can the client pause it? Does the client see cost or memory?                                                       |
| [industry-packs.md](industry-packs.md)                         | Each mapped industry: what the Operator takes on there and the line it never crosses, and the licensed-work rule in every industry                             | What does it do for an accounting firm, a dental practice or an insurance agency? What will it never do in that industry? Who keeps the licensed work? |
| [built-versus-planned.md](built-versus-planned.md)             | Every capability, with where it stands for clients today, and the numbers worth knowing                                                                        | Can it do this? Is a client running this today? What would SMD set up for a new client?                                                                |
| [what-smd-commits-to.md](what-smd-commits-to.md)               | The standard commitments in the agreement, without fees or amounts                                                                                             | Who owns the data? Is it used for training? What are the incident notice and exit terms? What does SMD not promise?                                    |
| [connecting.md](connecting.md)                                 | Starting to use the guide with an invite code from SMD Services                                                                                                | How do I connect with my invite code? I sent my code and got no reply. What if my code expired?                                                        |
| [who-smd-sells-to.md](who-smd-sells-to.md)                     | Who SMD sells to: the buyer, the two front doors, signs of a good fit, hard stops, yellow flags, where conversations start                                     | Does this prospect fit? Who is the buyer? Which prospects are not a fit today? What should I probe in the conversation?                                |

## How to read the labels

The library describes what the Operator can do. The Operator is configured for each client, so every capability in the library is something SMD can set up for a client, and the guide answers in those terms, in the present tense.

Where a document says how a capability stands for clients today, it uses one of three labels:

- **Running for a client today**: set up and switched on for a paying client.
- **Built, set up per client**: built, and switched on for a client when the engagement calls for it.
- **Configured per engagement**: SMD sets it up when a client's engagement calls for it.

The labels are knowledge for the representative. They answer "is a client running this today?" and "what would SMD set up for us?", and they matter when they change what to promise about timing. They are never a reason to say the Operator cannot do something.

The hard limits are stated as hard limits where they appear, for example that the Operator never moves money. Those do not change by configuration.

## What the library does not cover

Pricing, fees and discounts; any client, person or specific business; client documents or records; anything about the asker's own business. Questions on these go to SMD at team@smd.services.

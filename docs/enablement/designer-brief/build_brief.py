"""Builds the designer brief page (figures-brief.html) with the three reference renders embedded.

Usage: python3 build_brief.py <dir holding kit3fig1.png, kit3fig2.png, kit3fig4.png>
"""
import base64
import pathlib
import sys

src = pathlib.Path(sys.argv[1])
here = pathlib.Path(__file__).parent


def img(name: str, alt: str) -> str:
    data = base64.b64encode((src / name).read_bytes()).decode()
    return f'<div class="fig"><img alt="{alt}" src="data:image/png;base64,{data}"></div>'


HEAD = """<title>Operator Figures Brief</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=Archivo+Narrow:wght@500;600&family=JetBrains+Mono:wght@400;500&display=swap">
<style>
/* Layout: a working brief. One column, numbered sections, reference renders full width, fixed content in tables. */
:root { --bg:#f5f0e3; --panel:#fbf8f0; --ink:#1a1512; --ink2:#4a423c; --muted:#7d7267; --line:rgba(26,21,18,.18); --accent:#c5501e; --ok:#3f6335; --stop:#9c2a2a;
  --body:'Archivo',system-ui,sans-serif; --label:'Archivo Narrow','Archivo',system-ui,sans-serif; --mono:'JetBrains Mono',ui-monospace,Menlo,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --bg:#1a1512; --panel:#231c18; --ink:#f5f0e3; --ink2:#d3cabb; --muted:#a2978a; --line:rgba(245,240,227,.2); --accent:#e7783f; --ok:#9cc48a; --stop:#ef8f80; color-scheme:dark } }
:root[data-theme="dark"] { --bg:#1a1512; --panel:#231c18; --ink:#f5f0e3; --ink2:#d3cabb; --muted:#a2978a; --line:rgba(245,240,227,.2); --accent:#e7783f; --ok:#9cc48a; --stop:#ef8f80; color-scheme:dark }
body { background:var(--bg); color:var(--ink); font-family:var(--body); font-size:16px; line-height:1.55; }
.wrap { max-width:980px; margin:0 auto; padding-block:40px 72px; padding-left:max(16px, env(safe-area-inset-left, 0px)); padding-right:max(16px, env(safe-area-inset-right, 0px)); }
.eyebrow { font-family:var(--label); font-weight:600; text-transform:uppercase; letter-spacing:.09em; font-size:13px; color:var(--accent); }
h1 { font-weight:700; font-size:clamp(28px,5vw,42px); line-height:1.08; margin:6px 0 12px; text-wrap:balance; }
h2 { font-weight:700; font-size:23px; margin:0 0 8px; text-wrap:balance; }
h3 { font-family:var(--label); font-weight:600; text-transform:uppercase; letter-spacing:.08em; font-size:13px; color:var(--muted); margin:20px 0 8px; }
p { margin:0 0 12px; max-width:70ch; } .lede { font-size:18px; color:var(--ink2); }
section { padding-block:28px; border-top:1px solid var(--line); }
header { padding-bottom:22px; border-bottom:3px solid var(--ink); }
ul { margin:0 0 12px; padding-left:20px; display:grid; gap:5px; max-width:74ch; }
.fig { border:1.5px solid var(--ink); background:var(--panel); margin:12px 0 16px; overflow-x:auto; }
.fig img { display:block; width:100%; min-width:720px; height:auto; }
.tbl { overflow-x:auto; margin:10px 0 14px; }
table { border-collapse:collapse; width:100%; min-width:600px; font-size:14.5px; }
th, td { text-align:left; vertical-align:top; padding:8px 12px; border-bottom:1px solid var(--line); }
thead th { font-family:var(--label); text-transform:uppercase; letter-spacing:.07em; font-size:12.5px; color:var(--muted); border-bottom:1.5px solid var(--ink); }
code, .v { font-family:var(--mono); font-size:13px; }
.two { display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr)); gap:0; border:1.5px solid var(--ink); }
.two > div { padding:14px 16px; border-right:1px solid var(--line); border-bottom:1px solid var(--line); min-width:0; }
.two h4 { margin:0 0 8px; font-family:var(--label); text-transform:uppercase; letter-spacing:.07em; font-size:13px; }
.yes { color:var(--ok); } .no { color:var(--stop); }
.sw { display:inline-block; width:14px; height:14px; border:1.5px solid var(--ink); vertical-align:-2px; margin-right:6px; }
.note { font-size:14px; color:var(--muted); }
pre { background:var(--panel); border:1.5px solid var(--ink); padding:14px 16px; overflow-x:auto; font-family:var(--body); font-size:15px; white-space:pre-wrap; margin:10px 0; }
a { color:var(--accent); }
</style>
"""

BODY = f"""
<div class="wrap">
<header>
  <div class="eyebrow">SMD Services · design brief · 2026-11-09</div>
  <h1>Operator Figures Brief</h1>
  <p class="lede">We need three explanatory figures redrawn by an information designer. The content is finished and checked against the product. The job is craft: layout, hierarchy, rhythm and finish, so that each figure stands beside the best product diagrams in the industry.</p>
</header>

<section>
  <h2>1. The job</h2>
  <p>SMD Services builds and runs a managed AI worker for businesses, called the Operator. These three figures carry a first technical conversation between our sales staff and a buyer. They sit in a seven-figure set; the other four stay as they are for now and will be restyled by us to match your system.</p>
  <ul>
    <li><b>Figure 1, Before and after.</b> What changes for a business.</li>
    <li><b>Figure 2, System context.</b> What the Operator touches and who runs what.</li>
    <li><b>Figure 4, The permission model.</b> The client writes limits; here is a rule set beside its effects.</li>
  </ul>
  <p><b>Audience:</b> an experienced enterprise technical seller, and through them the owner, administrator or IT reviewer at a small or mid-sized business. Read on a shared screen and on a slide.</p>
  <div class="two">
    <div><h4 class="no">Fixed. Do not change.</h4><ul>
      <li>Every label, word for word (section 3 lists them).</li>
      <li>Every box, every arrow, its direction and its label.</li>
      <li>Which boxes sit in which zone.</li>
      <li>The numbers on Figure 2's arrows: they match numbered notes in the page text.</li>
      <li>What each treatment means (section 4).</li>
      <li>No added icons that stand for a brand, no robots, brains, shields or gears, no people illustrations.</li>
    </ul></div>
    <div><h4 class="yes">Open. This is your craft.</h4><ul>
      <li>Layout, proportion, spacing and alignment.</li>
      <li>Type sizes and weights within the two families.</li>
      <li>Line weights, corner treatment, arrow style.</li>
      <li>How the three ownership treatments and the status colours look, as long as each meaning stays distinct without relying on colour alone.</li>
      <li>A small, consistent set of generic pictograms if they add clarity (one family, one size).</li>
      <li>A better composition for any figure, if every fixed item survives.</li>
    </ul></div>
  </div>
  <p class="note" style="margin-top:12px">If you think a label is unclear or a relationship is wrong, tell us. Do not fix it silently: the wording is checked against the running product.</p>
</section>

<section>
  <h2>2. The standard</h2>
  <p>These are the figures ours should stand beside. Look at how plain they are: the credibility comes from named parts, clear boundaries and numbered steps, not from decoration.</p>
  <ul>
    <li><a href="https://tailscale.com/blog/how-tailscale-works">Tailscale, How Tailscale works</a>: small figures, one idea each; a rule shown beside its effect.</li>
    <li><a href="https://developers.cloudflare.com/reference-architecture/diagrams/ai/enterprise-ai-agent-workspace/">Cloudflare, Enterprise AI agent workspace</a>: zones, numbered steps matched to text.</li>
    <li><a href="https://developer.hashicorp.com/vault/docs/internals/architecture">HashiCorp Vault, Architecture</a>: twelve plain boxes and one labelled boundary.</li>
  </ul>
  <h3>What we know is weak in the current drawings</h3>
  <ul>
    <li><b>Figure 1</b> does its job but is plain. The single change (the middle slot) should be unmistakable at a glance.</li>
    <li><b>Figure 2</b> is correct and complete but dense: nine numbered connections, some sharing a number. It needs calmer routing and a clearer reading order without losing a connection.</li>
    <li><b>Figure 4</b> is the strongest. It needs finish, and the three outcomes (allowed, waits for a person, refused) should read instantly.</li>
  </ul>
</section>

<section>
  <h2>3. The fixed content</h2>

  <h3>Figure 1 · Before and after (current drawing, for reference)</h3>
  {img('kit3fig1.png', 'Current Figure 1: before and after')}
  <p><b>The one message:</b> capable people and capable systems, with connecting work done by hand between them; the Operator takes that middle position, inside limits the client writes, and SMD runs it.</p>
  <div class="tbl"><table>
    <thead><tr><th>Panel</th><th>Element</th><th>Text, verbatim</th></tr></thead>
    <tbody>
      <tr><td>Today</td><td>Left box</td><td>The client's people / owners, managers, staff</td></tr>
      <tr><td>Today</td><td>Middle (not a component: the gap)</td><td>Done by hand / read the email, update the system, chase the document, log it, book the follow-up</td></tr>
      <tr><td>Today</td><td>Right box</td><td>The client's systems / email, records, calendar, files</td></tr>
      <tr><td>Today</td><td>Arrows</td><td>people → middle: by hand. middle → systems: by hand.</td></tr>
      <tr><td>Today</td><td>Caption</td><td>The connecting work lives in people's heads and inboxes. It stops when someone is out, and it leaves when they do.</td></tr>
      <tr><td>With an Operator</td><td>Left box</td><td>The client's people / ask, direct, judge the work</td></tr>
      <tr><td>With an Operator</td><td>Middle box (the product)</td><td>The Operator / one per client / limits the client writes / every action recorded</td></tr>
      <tr><td>With an Operator</td><td>Right box</td><td>The client's systems / unchanged, still the system of record</td></tr>
      <tr><td>With an Operator</td><td>Arrows</td><td>people → Operator: asks. Operator → people: delivers. Operator → systems: writes. systems → Operator: reads.</td></tr>
      <tr><td>With an Operator</td><td>Below the Operator</td><td>SMD Services / the managed service. Arrow SMD Services → Operator: builds, runs, watches.</td></tr>
      <tr><td>With an Operator</td><td>Caption</td><td>Nothing new for staff to log in to: they email it like a colleague.</td></tr>
      <tr><td>Both</td><td>Panel labels</td><td>TODAY · WITH AN OPERATOR</td></tr>
    </tbody>
  </table></div>
  <p>The two panels must stay structurally identical (same three slots, same positions) so the eye sees that only the middle changes. The Today middle must read as a gap, not as a component.</p>

  <h3>Figure 2 · System context (current drawing, for reference)</h3>
  {img('kit3fig2.png', 'Current Figure 2: system context')}
  <p><b>The one message:</b> the client's systems stay theirs; the Operator runs on its own seat, one per client; SMD runs the service from outside it; the model provider is a third party.</p>
  <div class="tbl"><table>
    <thead><tr><th>Zone</th><th>Box</th><th>Subtitle, verbatim</th></tr></thead>
    <tbody>
      <tr><td rowspan="5">THE CLIENT'S ENVIRONMENT</td><td>Administrators</td><td>named by the client</td></tr>
      <tr><td>The Operator's mailbox</td><td>in the client's own mail tenant</td></tr>
      <tr><td>Staff</td><td>the people the client has listed</td></tr>
      <tr><td>System of record</td><td>where the client keeps its work</td></tr>
      <tr><td>Other connected systems</td><td>each one granted by the client</td></tr>
      <tr><td rowspan="6">THE OPERATOR · ONE SEAT PER CLIENT</td><td>Front door</td><td>verifies every inbound message</td></tr>
      <tr><td>Broker</td><td>holds send credentials; checks recipients</td></tr>
      <tr><td>Audit record</td><td>one entry per action; append-only, chained</td></tr>
      <tr><td>Memory + config</td><td>what it has learned; config it cannot edit</td></tr>
      <tr><td>Agent</td><td>does the work; cannot send on its own keys</td></tr>
      <tr><td>Jobs runner</td><td>queued jobs: cost-limited, reviewed, proven</td></tr>
      <tr><td rowspan="4">SMD SERVICES · RUN BY SMD</td><td>Client portal</td><td>pause, activity, limit settings</td></tr>
      <tr><td>Pager + hourly triage</td><td>watches every seat</td></tr>
      <tr><td>Per-client ops workspace</td><td>sealed to one seat; SMD's agents</td></tr>
      <tr><td>Releases + config</td><td>by reviewed change only</td></tr>
      <tr><td>MODEL PROVIDER · EXTERNAL</td><td>AI processing</td><td>does not train on client content. Zone note: some seats also use a speech-to-text and a web-search service</td></tr>
    </tbody>
  </table></div>
  <div class="tbl"><table>
    <thead><tr><th>No.</th><th>From</th><th>To</th><th>Arrow label, verbatim</th></tr></thead>
    <tbody>
      <tr><td>1</td><td>Staff</td><td>The Operator's mailbox</td><td>emails a request</td></tr>
      <tr><td>2</td><td>The Operator's mailbox</td><td>Front door</td><td>pulled in, verified</td></tr>
      <tr><td>3</td><td>Agent</td><td>System of record</td><td>reads, writes</td></tr>
      <tr><td>3</td><td>Agent</td><td>Other connected systems</td><td>where granted</td></tr>
      <tr><td>4</td><td>Broker</td><td>The Operator's mailbox</td><td>sends replies</td></tr>
      <tr><td>4</td><td>Broker</td><td>Audit record</td><td>sole writer</td></tr>
      <tr><td>5</td><td>Administrators</td><td>Client portal</td><td>use the portal</td></tr>
      <tr><td>5</td><td>Client portal</td><td>Front door</td><td>pause</td></tr>
      <tr><td>6</td><td>Front door</td><td>Pager + hourly triage</td><td>health, notices</td></tr>
      <tr><td>7</td><td>Releases + config</td><td>Memory + config</td><td>config changes</td></tr>
      <tr><td>8</td><td>Per-client ops workspace</td><td>the seat</td><td>sealed access</td></tr>
      <tr><td>9</td><td>Agent</td><td>AI processing</td><td>task content</td></tr>
      <tr><td>9</td><td>Jobs runner</td><td>AI processing</td><td>task content</td></tr>
    </tbody>
  </table></div>
  <p>Legend text: zone boundary · on the client's seat · client-owned, outside the seat · run by SMD · third party · see note n below.</p>

  <h3>Figure 4 · The permission model (current drawing, for reference)</h3>
  {img('kit3fig4.png', 'Current Figure 4: the permission model')}
  <p><b>The one message:</b> the client writes a limit for each kind of action; the limit is checked at every tool call; unwritten means refused.</p>
  <div class="tbl"><table>
    <thead><tr><th>The Operator attempts</th><th>Client's limit (kind / limit)</th><th>What happens</th><th>Outcome type</th></tr></thead>
    <tbody>
      <tr><td>Files an internal note / e.g. a note in the system of record</td><td>Internal write / limit: acts on its own</td><td>✓ Runs</td><td class="yes">allowed</td></tr>
      <tr><td>Emails the client's staff / e.g. a morning digest to the team</td><td>Send to staff / limit: acts on its own</td><td>✓ Sent</td><td class="yes">allowed</td></tr>
      <tr><td>Emails the client's customers / e.g. a follow-up on a missing document</td><td>Send to the client's customers / limit: drafts for a person</td><td>Draft: a person sends it</td><td>waits for a person</td></tr>
      <tr><td>Accepts a vendor's terms / e.g. agreeing to a delivery date</td><td>Commitment / limit: asks first</td><td>Held for an administrator</td><td>waits for a person</td></tr>
      <tr><td>Emails an outside party / e.g. someone on none of the client's lists</td><td>Send to outside parties / no limit written</td><td>✕ Refused</td><td class="no">refused</td></tr>
      <tr><td>Moves money / e.g. paying an invoice</td><td>Money movement / banned for every client</td><td>✕ Refused, whatever is written</td><td class="no">refused</td></tr>
    </tbody>
  </table></div>
  <p>Column headers: THE OPERATOR ATTEMPTS · CLIENT'S LIMITS · CHECKED AT EVERY TOOL CALL · WHAT HAPPENS. Zone footer: written by the client · stored read-only on the seat. Closing line: Unwritten means refused. The Operator cannot change its own limits. Legend: allowed · waits for a person · refused · on the client's seat · client-owned, outside the seat · zone boundary.</p>
</section>

<section>
  <h2>4. The visual system to work inside</h2>
  <p>One system for the whole set. Your three figures define it; we will restyle the other four to match, so please build from reusable components.</p>
  <div class="tbl"><table>
    <thead><tr><th>Meaning</th><th>Current treatment</th><th>Rule</th></tr></thead>
    <tbody>
      <tr><td>On the client's seat</td><td><span class="sw" style="background:#efe5cc"></span>tan fill</td><td>Used only for things that run on the client's own machine.</td></tr>
      <tr><td>Client-owned, outside the seat</td><td><span class="sw" style="background:#f5f0e3"></span>plain fill</td><td>The client's people and systems.</td></tr>
      <tr><td>Run by SMD</td><td><span class="sw" style="background:#fbf8f0;border-left-width:5px"></span>plain fill with a heavy left bar</td><td>Must be distinguishable from client-owned without colour.</td></tr>
      <tr><td>Third party</td><td><span class="sw" style="background:transparent;border-color:#7d7267"></span>clear fill, grey outline</td><td>The model provider only.</td></tr>
      <tr><td>The product's own controls</td><td><span class="sw" style="background:#c5501e;border-color:#c5501e"></span>orange</td><td>The Operator outline, step markers, closing rule lines. Nothing else is orange.</td></tr>
      <tr><td>Allowed</td><td><span class="sw" style="background:#3f6335;border-color:#3f6335"></span>green, solid line</td><td>Always paired with a tick or the word.</td></tr>
      <tr><td>Waits for a person</td><td>dashed ink line into a dashed-outline box</td><td>Deliberately not a colour.</td></tr>
      <tr><td>Refused or failed</td><td><span class="sw" style="background:#9c2a2a;border-color:#9c2a2a"></span>red</td><td>Always paired with a cross or the word.</td></tr>
      <tr><td>Zone boundary</td><td>dashed grey outline with a small caps label</td><td>Every zone is named and says how many exist ("one seat per client").</td></tr>
    </tbody>
  </table></div>
  <ul>
    <li><b>Palette.</b> Light: background <span class="v">#f5f0e3</span>, panel <span class="v">#fbf8f0</span>, seat <span class="v">#efe5cc</span>, ink <span class="v">#1a1512</span>, secondary text <span class="v">#4a423c</span>, muted <span class="v">#7d7267</span>, accent <span class="v">#c5501e</span>, allowed <span class="v">#3f6335</span>, refused <span class="v">#9c2a2a</span>. Dark: background <span class="v">#1a1512</span>, panel <span class="v">#231c18</span>, seat <span class="v">#4a3a26</span>, ink <span class="v">#f5f0e3</span>, accent <span class="v">#e7783f</span>, allowed <span class="v">#9cc48a</span>, refused <span class="v">#ef8f80</span>. You may adjust tints for contrast; keep the brand's cream, ink and burnt orange.</li>
    <li><b>Type.</b> Archivo for names and text, Archivo Narrow in small capitals for zone labels, figure titles and legends. No third family.</li>
    <li><b>Register.</b> Flat and plainspoken, like good commercial signage. No gradients, shadows, glows, 3D or stock illustration.</li>
    <li><b>Every figure carries</b> a title ("FIGURE 2 · SYSTEM CONTEXT"), a version stamp ("SMD SERVICES · V3 · 2026-11-09") and a legend for the treatments it uses.</li>
    <li><b>Arrows</b> have one head and a label. Flow runs one way where the content allows.</li>
    <li><b>Legibility.</b> Readable on a shared screen without zooming; smallest text no smaller than the current subtitles relative to the figure width.</li>
  </ul>
</section>

<section>
  <h2>5. What to deliver</h2>
  <ul>
    <li>An editable source file (Figma preferred; Illustrator accepted) with text as live text, built from components and named styles.</li>
    <li>For each figure: SVG in light and dark with text kept as text in the named fonts, and PNG at 2x.</li>
    <li>A 16:9 slide-ready variant of each figure.</li>
    <li>A one-page style sheet: the components, type sizes, line weights and colour tokens you used, so the remaining four figures can be matched.</li>
  </ul>
  <h3>How we will accept the work</h3>
  <ul>
    <li>Every fixed label and relationship in section 3 is present, word for word.</li>
    <li>Each treatment keeps its one meaning across all three figures, and no meaning depends on colour alone.</li>
    <li>No text touches or crosses a line or box edge; every arrow meets a named box; nothing is clipped; both themes hold.</li>
    <li>Each figure answers its one question without the page text beside it.</li>
    <li>Two rounds of revisions after the first proofs.</li>
  </ul>
  <h3>Terms we will ask for</h3>
  <ul>
    <li>Work made for hire with full assignment of rights to SMDurgan, LLC.</li>
    <li>The content is confidential until we publish it; no portfolio use without our written permission.</li>
  </ul>
</section>

<section>
  <h2>6. Posting text</h2>
  <p>For a marketplace posting or a first message to a designer.</p>
<pre>Information designer needed: redraw three product diagrams (content fixed, craft open)

We are a software and AI services firm. We have three explanatory figures for a B2B product: a before-and-after, a system context diagram, and a permission-model figure. The content is finished and checked against the product: every label, box and arrow is fixed. We need an information designer or technical illustrator to redraw them to the standard of the best developer-facing product diagrams (think Tailscale's "How Tailscale works", Cloudflare's reference architectures, HashiCorp Vault's architecture page): clear hierarchy, calm routing, consistent components, light and dark.

Deliverables: editable Figma (or Illustrator) source with live text and reusable components; SVG light and dark with text as text; PNG at 2x; a 16:9 variant of each; a one-page style sheet so we can match four further figures ourselves.

You are a fit if your portfolio shows explanatory or architecture diagrams for technical products, not only marketing illustration. Please send two or three examples of that kind, the tool you work in, and your price and turnaround for three figures with two rounds of revisions. A full written brief with the fixed content and current drawings is ready to share.</pre>
</section>
</div>
"""

(here / "figures-brief.html").write_text(HEAD + BODY)
print("wrote", here / "figures-brief.html", len(HEAD + BODY))

#!/usr/bin/env python3
"""Generates version 3 of how-the-operator-works.html (figures built from coordinates)."""
import sys

OUT = sys.argv[1]
STAMP = "SMD SERVICES · V3 · 2026-11-09"


def mk(i, cls="hd"):
    return (f'<marker id="{i}" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" '
            f'markerUnits="userSpaceOnUse" orient="auto"><path d="M0,0 L10,5 L0,10 z" class="{cls}"/></marker>')


def head(n, title):
    return (f'<text x="20" y="22" class="ft">FIGURE {n} · {title}</text>'
            f'<text x="940" y="22" class="fv">{STAMP}</text>')


def T(x, y, cls, s):
    return f'<text x="{x}" y="{y}" class="{cls}">{s}</text>'


def R(x, y, w, h, cls, extra=""):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="{cls}"{extra}/>'


def SMD(x, y, w, h):
    return R(x, y, w, h, "smd") + R(x, y, 4, h, "bar")


def L(x1, y1, x2, y2, cls="ln", m=None, extra=""):
    me = f' marker-end="url(#{m})"' if m else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" class="{cls}"{me}{extra}/>'


def P(d, cls="ln", m=None):
    me = f' marker-end="url(#{m})"' if m else ""
    return f'<path d="{d}" class="{cls}"{me}/>'


def C(x, y, n, r=9):
    return f'<circle cx="{x}" cy="{y}" r="{r}" class="b"/><text x="{x}" y="{y+4}" class="bn">{n}</text>'


def sw(x, y, cls):
    """legend swatch"""
    out = f'<rect x="{x}" y="{y}" width="22" height="14" class="{cls}" data-legend="1"/>'
    if cls == "smd":
        out += f'<rect x="{x}" y="{y}" width="4" height="14" class="bar"/>'
    return out


# ---------------------------------------------------------------- Figure 1
def fig1():
    o = [f'<svg viewBox="0 0 960 446" role="img" aria-label="Figure 1. Today, the client\'s people connect their systems by hand. With an Operator, a governed worker in the same position does that connecting work, and SMD Services runs it.">',
         "<defs>" + mk("a1") + "</defs>", head(1, "BEFORE AND AFTER")]
    o += [T(20, 50, "zl", "TODAY"),
          R(20, 60, 200, 100, "bx"), T(120, 106, "t m", "The client's people"), T(120, 126, "s m", "owners, managers, staff"),
          L(224, 110, 376, 110, m="a1"), T(300, 102, "lb m", "by hand"),
          R(380, 60, 200, 100, "gap"), T(480, 84, "t m", "Done by hand"),
          T(480, 106, "s m", "read the email, update the system,"), T(480, 124, "s m", "chase the document, log it,"), T(480, 142, "s m", "book the follow-up"),
          L(584, 110, 736, 110, m="a1"), T(660, 102, "lb m", "by hand"),
          R(740, 60, 200, 100, "bx"), T(840, 106, "t m", "The client's systems"), T(840, 126, "s m", "email, records, calendar, files"),
          T(20, 184, "s", "The connecting work lives in people's heads and inboxes. It stops when someone is out, and it leaves when they do."),
          L(20, 206, 940, 206, "zone"),
          T(20, 234, "zl", "WITH AN OPERATOR"),
          R(20, 244, 200, 100, "bx"), T(120, 290, "t m", "The client's people"), T(120, 310, "s m", "ask, direct, judge the work"),
          L(224, 278, 376, 278, m="a1"), T(300, 270, "lb m", "asks"),
          L(376, 312, 224, 312, m="a1"), T(300, 304, "lb m", "delivers"),
          R(380, 244, 200, 100, "op"), T(480, 268, "t m", "The Operator"), T(480, 286, "s m", "one per client"),
          T(480, 310, "a m", "limits the client writes"), T(480, 328, "s m", "every action recorded"),
          L(584, 278, 736, 278, m="a1"), T(660, 270, "lb m", "writes"),
          L(736, 312, 584, 312, m="a1"), T(660, 304, "lb m", "reads"),
          R(740, 244, 200, 100, "bx"), T(840, 282, "t m", "The client's systems"), T(840, 302, "s m", "unchanged, still the"), T(840, 318, "s m", "system of record"),
          L(480, 372, 480, 348, m="a1"), T(490, 364, "lb", "builds, runs, watches"),
          SMD(380, 376, 200, 44), T(482, 395, "t m", "SMD Services"), T(482, 411, "s m", "the managed service"),
          T(20, 392, "s", "Nothing new for staff to log in to:"), T(20, 408, "s", "they email it like a colleague."),
          sw(640, 376, "bx"), T(670, 388, "s", "client-owned, outside the seat"),
          sw(640, 396, "seat"), T(670, 408, "s", "on the client's seat"),
          sw(640, 416, "smd"), T(670, 428, "s", "run by SMD"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 2
def fig2():
    ys = [98, 166, 234, 302, 370, 438]
    cy = [y + 22 for y in ys]
    o = ['<svg viewBox="0 0 960 544" role="img" aria-label="Figure 2. System context: the client\'s environment, the Operator on its own seat, SMD Services and the model provider, with nine numbered connections.">',
         "<defs>" + mk("a2") + "</defs>", head(2, "SYSTEM CONTEXT")]
    # zones
    o += [R(20, 64, 220, 432, "zone"), T(32, 83, "zl", "THE CLIENT'S ENVIRONMENT"),
          R(350, 64, 270, 432, "zone"), T(362, 83, "zl", "THE OPERATOR · ONE SEAT PER CLIENT"),
          R(730, 64, 210, 296, "zone"), T(742, 83, "zl", "SMD SERVICES · RUN BY SMD"),
          R(730, 370, 210, 126, "zone"), T(742, 388, "zl", "MODEL PROVIDER · EXTERNAL")]
    left = {0: ("Administrators", "named by the client"), 1: ("The Operator's mailbox", "in the client's own mail tenant"),
            2: ("Staff", "the people the client has listed"), 4: ("System of record", "where the client keeps its work"),
            5: ("Other connected systems", "each one granted by the client")}
    for k, (t, s) in left.items():
        o += [R(32, ys[k], 196, 44, "bx"), T(44, ys[k] + 18, "t", t), T(44, ys[k] + 34, "s", s)]
    mid = [("Front door", "verifies every inbound message"), ("Broker", "holds send credentials; checks recipients"),
           ("Audit record", "one entry per action; append-only, chained"), ("Memory + config", "what it has learned; config it cannot edit"),
           ("Agent", "does the work; cannot send on its own keys"), ("Jobs runner", "queued jobs: cost-limited, reviewed, proven")]
    for k, (t, s) in enumerate(mid):
        o += [R(362, ys[k], 246, 44, "seat"), T(372, ys[k] + 18, "t", t), T(372, ys[k] + 34, "s", s)]
    right = [("Client portal", "pause, activity, limit settings"), ("Pager + hourly triage", "watches every seat"),
             ("Per-client ops workspace", "one per client; access logged"), ("Releases + config", "by reviewed change only")]
    for k, (t, s) in enumerate(right):
        o += [SMD(742, ys[k], 186, 44), T(756, ys[k] + 18, "t", t), T(756, ys[k] + 34, "s", s)]
    o += [R(742, 396, 186, 44, "ext"), T(754, 414, "t", "AI processing"), T(754, 430, "s", "does not train on client content"),
          T(742, 460, "mu", "some seats also use a speech-to-text"), T(742, 476, "mu", "and a web-search service")]
    # administrators to the portal, over the top
    o += [P("M214,94 V48 H918 V94", m="a2"), T(566, 42, "lb m", "use the portal"), C(250, 48, 5)]
    # left gutter
    o += [L(60, 230, 60, 214, m="a2"), C(78, 222, 1), T(94, 226, "lb", "emails a request"),
          P("M232,178 H296 V120 H358", m="a2"), T(295, 112, "lb m", "pulled in, verified"), C(296, 150, 2),
          L(358, 198, 232, 198, m="a2"), T(283, 213, "lb m", "sends replies"), C(338, 198, 4),
          L(358, 384, 232, 384, m="a2"), T(283, 376, "lb m", "reads, writes"), C(338, 384, 3),
          P("M358,402 H296 V460 H232", m="a2"), T(288, 475, "lb m", "where granted"), C(296, 431, 3)]
    # inside the seat
    o += [L(420, 214, 420, 230, m="a2"), C(438, 222, 4), T(454, 226, "lb", "sole writer")]
    # right gutter
    o += [L(738, 110, 612, 110, m="a2"), T(672, 102, "lb m", "pause"), C(716, 110, 5),
          P("M612,132 H634 V188 H738", m="a2"), T(688, 180, "lb m", "health, notices"), C(634, 160, 6),
          L(738, 256, 624, 256, m="a2"), T(663, 248, "lb m", "logged access"), C(716, 256, 8),
          L(738, 324, 612, 324, m="a2"), T(663, 316, "lb m", "config changes"), C(716, 324, 7),
          L(612, 406, 738, 406, m="a2"), T(686, 398, "lb m", "task content"), C(638, 406, 9),
          P("M612,452 H690 V428 H738", m="a2"), C(650, 452, 9)]
    # legend
    o += [L(20, 520, 50, 520, "zone", extra=' data-legend="1"'), T(58, 524, "s", "zone boundary"),
          sw(150, 513, "seat"), T(180, 524, "s", "on the client's seat"),
          sw(300, 513, "bx"), T(330, 524, "s", "client-owned, outside the seat"),
          sw(506, 513, "smd"), T(536, 524, "s", "run by SMD"),
          sw(612, 513, "ext"), T(642, 524, "s", "third party"),
          '<circle cx="724" cy="520" r="9" class="b"/><text x="724" y="524" class="bn">n</text>', T(740, 524, "s", "see note n below"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 3
def fig3():
    steps = [
        ("Arrive", "Email, system event or schedule. Signature verified.", "FRONT DOOR",
         ["Bad or stale signature:", "rejected before any work."]),
        ("Sender check", "Matched to the client's staff list.", "FRONT DOOR",
         ["Unknown sender: content fenced, no reply,", "no send, commitment or delete."]),
        ("Skill and sender's authority", "The matching skill loads. The agent is told who is asking.", "AGENT",
         ["Skill not enabled on this seat:", "declined."]),
        ("Tool lookup", "Each of 299 tools has one fixed kind of action.", "AGENT",
         ["Not in the registry:", "never runs."]),
        ("Limit check", "The kind of action, against the client's written limit.", "AGENT",
         ["No limit written for it:", "refused. That is the default."]),
        ("Content checks", "Numbers and dates must trace to something it read.", "AGENT",
         ["Untraceable number or date: blocked.", "Money or contract content:", "draft for a person."]),
        ("Act", "The tool runs in the client's system. One audit entry written.", "AGENT + BROKER",
         ["A check that cannot run:", "the tool call is blocked."]),
        ("Deliver", "Reply in the thread, a draft for a person, or ask first.", "BROKER",
         ["Recipient not on the thread or the", "staff list: blocked, SMD told."]),
        ("Report", "A notice goes to SMD for each staff request.", "FRONT DOOR",
         ["No answer in 30 minutes:", "SMD is paged."]),
    ]
    o = ['<svg viewBox="0 0 960 682" role="img" aria-label="Figure 3. Nine numbered steps from arrival to report, top to bottom, each tagged with the part of the seat that does it and with what happens when it fails. Steps 4 to 7 repeat for every tool call.">',
         "<defs>" + mk("a3") + mk("r3", "hdr") + mk("o3", "hdo") + "</defs>", head(3, "LIFE OF A REQUEST")]
    o += [P("M260,44 H250 V226 H260"), T(240, 131, "s e", "Once, when the"), T(240, 147, "s e", "request arrives"),
          P("M260,248 H250 V498 H260", "lo"), T(240, 357, "a e", "Repeats for every tool"), T(240, 373, "a e", "call the agent makes,"), T(240, 389, "a e", "until the work is done"),
          P("M286,475 H274 V271 H286", "lo", "o3"),
          P("M260,520 H250 V634 H260"), T(240, 573, "s e", "When the work"), T(240, 589, "s e", "is done")]
    for i, (t, s, tag, fail) in enumerate(steps):
        y = 44 + 68 * i
        c = y + 23
        o += [R(290, y, 380, 46, "seat"), C(312, c, i + 1, 11), T(334, y + 19, "t", t), T(334, y + 35, "s", s), T(658, y + 19, "tag", tag)]
        if i == 6:
            o += [L(674, c - 12, 708, c - 12, m="a3"), T(716, c - 8, "s", "Long or costly work: queued job, Figure 5"),
                  L(674, c + 12, 708, c + 12, "lr", "r3"), T(716, c + 16, "x", fail[0]), T(716, c + 32, "x", fail[1])]
        else:
            o.append(L(674, c, 708, c, "lr", "r3"))
            y0 = c - 4 if len(fail) == 2 else c - 12
            for j, f in enumerate(fail):
                o.append(T(716, y0 + 16 * j, "x", f))
        if i < 8:
            o.append(L(480, y + 50, 480, y + 64, m="a3"))
    o += [L(290, 660, 322, 660, m="a3", extra=' data-legend="1"'), T(330, 664, "s", "next step"),
          L(400, 660, 432, 660, "lr", "r3", ' data-legend="1"'), T(440, 664, "s", "when a step fails or is refused"),
          L(622, 660, 654, 660, "lo", extra=' data-legend="1"'), T(662, 664, "s", "every tool call"),
          sw(770, 653, "seat"), T(800, 664, "s", "on the client's seat"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 4
def fig4():
    rows = [
        ("Files an internal note", "e.g. a note in the system of record", "Internal write", "limit: acts on its own", "vg", "g", "✓ Runs"),
        ("Emails the client's staff", "e.g. a morning digest to the team", "Send to staff", "limit: acts on its own", "vg", "g", "✓ Sent"),
        ("Emails the client's customers", "e.g. a follow-up on a missing document", "Send to the client's customers", "limit: drafts for a person", "vi", "p", "Draft: a person sends it"),
        ("Accepts a vendor's terms", "e.g. agreeing to a delivery date", "Commitment", "limit: asks first", "vi", "p", "Held for an administrator"),
        ("Emails an outside party", "e.g. someone on none of the client's lists", "Send to outside parties", "no limit written", "vx", "r", "✕ Refused"),
        ("Moves money", "e.g. paying an invoice", "Money movement", "banned for every client", "vx", "r", "✕ Refused, whatever is written"),
    ]
    o = ['<svg viewBox="0 0 960 516" role="img" aria-label="Figure 4. Six attempted actions pass through the client\'s written limits and end as run, sent, drafted for a person, held for an administrator, or refused.">',
         "<defs>" + mk("a4") + mk("g4", "hdg") + mk("r4", "hdr") + "</defs>", head(4, "THE PERMISSION MODEL")]
    o += [T(20, 58, "zl", "THE OPERATOR ATTEMPTS"),
          R(318, 40, 324, 408, "zone"), T(330, 58, "zl", "CLIENT'S LIMITS · CHECKED AT EVERY TOOL CALL"),
          T(710, 58, "zl", "WHAT HAPPENS")]
    for i, (a, eg, kind, val, vc, oc, out) in enumerate(rows):
        y = 68 + 60 * i
        o += [R(20, y, 250, 44, "seat"), T(32, y + 19, "t", a), T(32, y + 35, "s", eg),
              L(274, y + 22, 326, y + 22, m="a4"),
              R(330, y, 300, 44, "seat"), T(342, y + 19, "t", kind), T(342, y + 35, vc, val)]
        if i == 3:
            o.append(T(618, y + 35, "mu e", "built, not yet proven in client use"))
        if oc == "g":
            o += [L(634, y + 22, 706, y + 22, "lg", "g4"), R(710, y, 230, 44, "bx"), T(722, y + 27, "tg", out)]
        elif oc == "p":
            o += [L(634, y + 22, 706, y + 22, "lp", "a4"), R(710, y, 230, 44, "pd"), T(722, y + 27, "t", out)]
        else:
            o += [L(634, y + 22, 706, y + 22, "lrs", "r4"), R(710, y, 230, 44, "bx"), T(722, y + 27, "tx", out)]
    o += [T(480, 434, "mu m", "written by the client · stored read-only on the seat"),
          T(480, 474, "a m", "Unwritten means refused. The Operator cannot change its own limits."),
          L(20, 494, 52, 494, "lg", "g4", ' data-legend="1"'), T(60, 498, "s", "allowed"),
          L(130, 494, 162, 494, "lp", "a4", ' data-legend="1"'), T(170, 498, "s", "waits for a person"),
          L(300, 494, 332, 494, "lrs", "r4", ' data-legend="1"'), T(340, 498, "s", "refused"),
          sw(410, 487, "seat"), T(440, 498, "s", "on the client's seat"),
          sw(570, 487, "bx"), T(600, 498, "s", "client-owned, outside the seat"),
          L(790, 494, 820, 494, "zone", extra=' data-legend="1"'), T(828, 498, "s", "zone boundary"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 5
def fig5():
    st = [
        ("Submit", ["requester and", "monthly allowance", "checked"], ["refused: requester", "told why"]),
        ("Proof gate", ["first of its kind", "only: a proof run", "and SMD's yes"], ["waits; SMD paged"]),
        ("Estimate", ["cost projected", "before any", "paid step"], ["over the limit:", "holds; SMD paged"]),
        ("Run", ["cost limit checked", "before every", "paid step"], ["over the limit:", "holds; SMD paged"]),
        ("Review", ["checked against its", "sources by an", "independent review"], ["must-fix found:", "holds, nothing", "filed; SMD paged"]),
        ("File", ["written to the", "client's system", "of record"], None),
        ("Read back", ["delivered file", "fetched, compared", "to what was built"], ["mismatch: failed;", "SMD paged"]),
        ("Reply", ["to the original", "requester, and", "no one else"], ["not sent in 20", "minutes: SMD paged"]),
    ]
    o = ['<svg viewBox="0 0 960 368" role="img" aria-label="Figure 5. Eight numbered stages of a queued job: submit, proof gate, estimate, run, review, file, read back, reply, each with the hold or failure that can stop it, above a strip of job states.">',
         "<defs>" + mk("a5") + mk("r5", "hdr") + "</defs>", head(5, "LIFE OF A QUEUED JOB")]
    for k, (t, cap, hold) in enumerate(st):
        x = 20 + 118 * k
        cx = x + 47
        if k in (1, 6):
            o.append(T(cx, 48, "pt", "PROOF STEP"))
        for j, c in enumerate(cap):
            o.append(T(cx, 64 + 16 * j, "s m", c))
        o += [R(x, 106, 94, 58, "seat"), C(cx, 124, k + 1, 10), T(cx, 153, "t m", t)]
        if k < 7:
            o.append(L(x + 98, 135, x + 114, 135, m="a5"))
        if hold:
            o.append(L(cx, 168, cx, 186, "lr", "r5"))
            for j, h in enumerate(hold):
                o.append(T(cx, 202 + 16 * j, "x m", h))
    o += [T(20, 268, "zl", "JOB STATE"),
          P("M20,290 H940 M20,284 V296 M244,284 V296 M834,284 V296 M940,284 V296"),
          T(132, 282, "s m", "queued"), T(539, 282, "s m", "running"), T(887, 282, "s m", "delivered"),
          T(20, 318, "s", "<tspan class=\"sb\">held:</tspan> stopped at a red branch, before anything is filed"),
          T(480, 318, "s", "<tspan class=\"sb\">failed:</tspan> ended without a proven delivery"),
          L(20, 346, 52, 346, m="a5", extra=' data-legend="1"'), T(60, 350, "s", "next stage"),
          L(140, 346, 172, 346, "lr", "r5", ' data-legend="1"'), T(180, 350, "s", "hold or failure"),
          T(290, 350, "pt2", "PROOF STEP"), T(362, 350, "s", "a job cannot skip it"),
          sw(500, 339, "seat"), T(530, 350, "s", "on the client's seat"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 6
def fig6():
    o = ['<svg viewBox="0 0 960 526" role="img" aria-label="Figure 6. Three client seats side by side, each with its own machine, storage volume, memory, audit record and connection credentials, each with its own ops workspace, above one shared SMD Services layer.">',
         "<defs>" + mk("a6") + "</defs>", head(6, "ISOLATION AND WHAT IS SHARED")]
    for n, x0 in zip("ABC", (20, 340, 660)):
        o += ["<g>", R(x0, 40, 280, 252, "zone"), T(x0 + 12, 60, "zl", f"CLIENT {n}'S SEAT · THEIRS ALONE")]
        cells = [(x0 + 12, 72, 122, "Machine", "one per client"), (x0 + 146, 72, 122, "Storage volume", "one per client"),
                 (x0 + 12, 132, 122, "Memory", "this client's only"), (x0 + 146, 132, 122, "Audit record", "this client's only"),
                 (x0 + 12, 192, 256, "Connection credentials", "stay on this machine; never in a shared store")]
        for (x, y, w, t, s) in cells:
            o += [R(x, y, w, 48, "seat"), T(x + w / 2, y + 20, "t m", t), T(x + w / 2, y + 37, "s m", s)]
        o += [T(x0 + 140, 262, "s m", "will not start if pointed at"), T(x0 + 140, 278, "s m", "another client's storage"),
              L(x0 + 22, 296, x0 + 22, 396, m="a6"), T(x0 + 30, 366, "lb", "health every minute"), T(x0 + 30, 382, "lb", "audit copy daily"),
              L(x0 + 140, 396, x0 + 140, 296, m="a6"), T(x0 + 148, 391, "lb", "releases, config"),
              SMD(x0 + 152, 328, 116, 44), T(x0 + 212, 346, "t m", "Ops workspace"), T(x0 + 212, 362, "s m", "one per client"),
              L(x0 + 238, 324, x0 + 238, 296, m="a6"), T(x0 + 230, 314, "lb e", "logged access"),
              "</g>"]
    o += [SMD(20, 400, 920, 56), T(36, 423, "t", "SMD Services · one, shared across clients"),
          T(36, 442, "s", "Client portal, SMD's console, pager and hourly triage, releases and config, audit archive: each kept apart by client"),
          T(480, 484, "a m", "No two clients share a machine, a storage volume, connection credentials, memory or an audit record."),
          sw(20, 497, "seat"), T(50, 508, "s", "on the client's seat"),
          sw(190, 497, "smd"), T(220, 508, "s", "run by SMD"),
          L(320, 504, 350, 504, "zone", extra=' data-legend="1"'), T(358, 508, "s", "zone boundary"),
          "</svg>"]
    return "\n    ".join(o)


# ---------------------------------------------------------------- Figure 7
def fig7():
    o = ['<svg viewBox="0 0 960 446" role="img" aria-label="Figure 7. The audit record and the three other records made from the Operator\'s work on the left, the four readers on the right, and seven numbered links between them.">',
         "<defs>" + mk("a7") + "</defs>", head(7, "AUDIT AND RECORD MAP")]
    ys = [72, 148, 224, 300]
    o += [T(20, 58, "zl", "WHERE EACH RECORD LIVES"), T(560, 58, "zl", "WHO READS IT")]
    stores = [("seat", "Audit record", "on the client's seat; one entry per action, chained"),
              ("smd", "Newest-entry fingerprint", "in SMD's console; a new one every minute"),
              ("smd", "Daily audit archive", "a dated copy that cannot be overwritten"),
              ("smd", "Request notices", "in SMD's operations mailbox; one per staff request")]
    for y, (c, t, s) in zip(ys, stores):
        o += [SMD(20, y, 380, 52) if c == "smd" else R(20, y, 380, 52, c), T(36, y + 22, "t", t), T(36, y + 40, "s", s)]
    readers = [("seat", "Agent", "can read it; cannot rewrite it"),
               ("bx", "Client administrator", "sees it in the Client portal"),
               ("smd", "Daily verifier", "checks the chain every day; a break raises an alarm"),
               ("smd", "SMD operations", "reads each notice; paged when a reply is missing")]
    for y, (c, t, s) in zip(ys, readers):
        o += [SMD(560, y, 380, 52) if c == "smd" else R(560, y, 380, 52, c), T(576, y + 22, "t", t), T(576, y + 40, "s", s)]
    o += [L(404, 88, 556, 98, m="a7"), C(480, 93, 1),
          L(404, 102, 556, 174, m="a7"), C(480, 138, 2),
          L(80, 128, 80, 144, m="a7"), C(98, 136, 3), T(114, 140, "lb", "newest entry's fingerprint leaves the seat"),
          L(404, 116, 556, 236, m="a7"), C(480, 176, 4),
          L(404, 174, 556, 250, m="a7"), C(480, 212, 5),
          L(556, 264, 404, 252, m="a7"), C(480, 258, 6),
          L(404, 326, 556, 326, m="a7"), C(480, 326, 7),
          T(20, 384, "s", "<tspan class=\"sb\">An audit entry holds:</tspan> what was done, by which skill, under which limit, and the outcome. It does not hold the text of the client's documents."),
          T(20, 402, "s", "<tspan class=\"sb\">Tamper-evident, not tamper-proof:</tspan> entries are chained, not signed. Every read SMD's console makes of a seat is itself logged."),
          sw(20, 417, "seat"), T(50, 428, "s", "on the client's seat"),
          sw(190, 417, "bx"), T(220, 428, "s", "client-owned, outside the seat"),
          sw(400, 417, "smd"), T(430, 428, "s", "run by SMD"),
          '<circle cx="530" cy="424" r="9" class="b"/><text x="530" y="428" class="bn">n</text>', T(546, 428, "s", "see note n below"),
          "</svg>"]
    return "\n    ".join(o)


def steps(items):
    return '<ol class="steps">\n' + "\n".join(
        f'    <li><span class="n">{n}</span><span><b>{b}</b> {t}</span></li>' for n, b, t in items) + "\n  </ol>"


CSS = open(__file__.replace("gen3.py", "gen3.head.html.txt")).read()
BODY = open(__file__.replace("gen3.py", "gen3.body.html")).read()
figs = {"FIG1": fig1(), "FIG2": fig2(), "FIG3": fig3(), "FIG4": fig4(), "FIG5": fig5(), "FIG6": fig6(), "FIG7": fig7()}
for k, v in figs.items():
    BODY = BODY.replace("{{" + k + "}}", v)
html = CSS + "\n" + BODY
assert "—" not in html and "{{" not in html
open(OUT, "w").write(html)
print("wrote", len(html))

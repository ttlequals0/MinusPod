#!/usr/bin/env python3
"""Generate MinusPod workflow diagrams as SVG, light + dark, from design tokens.

Tokens mirror frontend/src/index.css :root and .dark. Shape language mirrors
design-guide/MinusPod Design System: 8px card radius, 1px border, 20% tint
badges, 12px uppercase meta, Roboto.
"""
import os

FONT = "Roboto, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

LIGHT = dict(
    bg="#F0F2F4", card="#FFFFFF", fg="#272B30", cardFg="#272B30",
    muted="#E3E6E8", mutedFg="#6D7378", border="#D3D9DE",
    primary="#279BBE", destructive="#EA312E", success="#00A63E",
    warning="#E17100", tintBase="#FFFFFF",
)
DARK = dict(
    bg="#272B30", card="#3B4045", fg="#FFFFFF", cardFg="#FFFFFF",
    muted="#51575C", mutedFg="#999999", border="#34383D",
    primary="#5ABFDD", destructive="#EF5F5D", success="#05DF73",
    warning="#FFB900", tintBase="#3B4045",
)


def tint(hexc, base, a=0.20):
    """20% tint fill over the surface, per the badge rule."""
    def ch(c):
        return int(c, 16)
    r, g, b = ch(hexc[1:3]), ch(hexc[3:5]), ch(hexc[5:7])
    br, bg_, bb = ch(base[1:3]), ch(base[3:5]), ch(base[5:7])
    f = lambda c, d: round(a * c + (1 - a) * d)
    return "#%02X%02X%02X" % (f(r, br), f(g, bg_), f(b, bb))


class Canvas:
    def __init__(self, t, w, h, title):
        self.t = t
        self.w = w
        self.h = h
        self.title = title
        self.o = []
        self.defs = []

    # --- primitives -------------------------------------------------
    def esc(self, s):
        return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    def text(self, x, y, s, size=14, fill=None, weight=400, anchor="start",
             spacing=None, upper=False, opacity=None):
        t = self.t
        fill = fill or t["cardFg"]
        s = s.upper() if upper else s
        extra = ""
        if spacing:
            extra += f' letter-spacing="{spacing}"'
        if opacity:
            extra += f' opacity="{opacity}"'
        self.o.append(
            f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
            f'{self.esc(s)}</text>')

    def card(self, x, y, w, h, fill=None, stroke=None, r=8, sw=1, dash=None):
        t = self.t
        fill = fill or t["card"]
        stroke = stroke or t["border"]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.o.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')

    def badge(self, x, y, label, accent, w=None):
        """px-2 py-0.5 text-xs rounded, 20% tint fill."""
        t = self.t
        cw = w or (len(label) * 6.4 + 16)
        self.card(x, y, cw, 20, fill=tint(accent, t["tintBase"]),
                  stroke="none", r=4, sw=0)
        self.text(x + cw / 2, y + 14, label, size=11, fill=accent,
                  weight=500, anchor="middle")
        return cw

    def arrow(self, x1, y1, x2, y2, color=None, dash=None):
        t = self.t
        color = color or t["mutedFg"]
        d = f' stroke-dasharray="{dash}"' if dash else ""
        m = "arp" if color == t["primary"] else "ar"
        self.o.append(
            f'<path d="M {x1} {y1} L {x2} {y2}" stroke="{color}" '
            f'stroke-width="1.5" fill="none" marker-end="url(#{m})"{d}/>')

    def curve(self, x1, y1, x2, y2, color=None):
        t = self.t
        color = color or t["mutedFg"]
        mx = (x1 + x2) / 2
        self.o.append(
            f'<path d="M {x1} {y1} C {mx} {y1}, {mx} {y2}, {x2} {y2}" '
            f'stroke="{color}" stroke-width="1.5" fill="none" '
            f'marker-end="url(#ar)"/>')

    def vcurve(self, x1, y1, x2, y2, color=None):
        t = self.t
        color = color or t["mutedFg"]
        my = (y1 + y2) / 2
        self.o.append(
            f'<path d="M {x1} {y1} C {x1} {my}, {x2} {my}, {x2} {y2}" '
            f'stroke="{color}" stroke-width="1.5" fill="none" '
            f'marker-end="url(#ar)"/>')

    def rule(self, x1, y, x2, color=None):
        t = self.t
        self.o.append(
            f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" '
            f'stroke="{color or t["border"]}" stroke-width="1"/>')

    # --- signature: the episode ribbon ------------------------------
    def ribbon(self, x, y, w, segs, h=34, r=6):
        """segs = [(fraction, kind, label)] kind: show|ad|gone|keep|beep"""
        t = self.t
        self.o.append(f'<g clip-path="url(#clip{int(x)}_{int(y)})">')
        self.defs.append(
            f'<clipPath id="clip{int(x)}_{int(y)}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/></clipPath>')
        total = sum(s[0] for s in segs)
        cx = x
        for frac, kind, label in segs:
            sw_ = w * frac / total
            if kind == "show":
                fill, fg = tint(t["primary"], t["tintBase"]), t["primary"]
            elif kind == "ad":
                fill, fg = tint(t["destructive"], t["tintBase"]), t["destructive"]
            elif kind == "keep":
                fill, fg = tint(t["success"], t["tintBase"]), t["success"]
            elif kind == "beep":
                fill, fg = tint(t["warning"], t["tintBase"]), t["warning"]
            else:
                fill, fg = t["bg"], t["mutedFg"]
            self.o.append(
                f'<rect x="{cx}" y="{y}" width="{sw_}" height="{h}" fill="{fill}"/>')
            if kind == "ad":
                self.o.append(
                    f'<rect x="{cx}" y="{y}" width="{sw_}" height="{h}" '
                    f'fill="url(#hatch)"/>')
            if kind == "gone":
                self.o.append(
                    f'<rect x="{cx}" y="{y}" width="{sw_}" height="{h}" '
                    f'fill="url(#hatchq)"/>')
            if label and sw_ > len(label) * 6.2:
                self.text(cx + sw_ / 2, y + h / 2 + 4, label, size=11,
                          fill=fg, weight=500, anchor="middle")
            cx += sw_
            if cx < x + w - 0.5:
                self.o.append(
                    f'<line x1="{cx}" y1="{y}" x2="{cx}" y2="{y+h}" '
                    f'stroke="{t["card"]}" stroke-width="2"/>')
        self.o.append('</g>')
        self.o.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" '
            f'fill="none" stroke="{t["border"]}" stroke-width="1"/>')

    # --- output -----------------------------------------------------
    def render(self):
        t = self.t
        defs = (
            f'<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 1 L 9 5 L 0 9 z" fill="{t["mutedFg"]}"/></marker>'
            f'<marker id="arp" viewBox="0 0 10 10" refX="9" refY="5" '
            f'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 1 L 9 5 L 0 9 z" fill="{t["primary"]}"/></marker>'
            f'<pattern id="hatch" width="7" height="7" patternTransform="rotate(45)" '
            f'patternUnits="userSpaceOnUse">'
            f'<line x1="0" y1="0" x2="0" y2="7" stroke="{t["destructive"]}" '
            f'stroke-width="2" opacity="0.28"/></pattern>'
            f'<pattern id="hatchq" width="7" height="7" patternTransform="rotate(45)" '
            f'patternUnits="userSpaceOnUse">'
            f'<line x1="0" y1="0" x2="0" y2="7" stroke="{t["mutedFg"]}" '
            f'stroke-width="2" opacity="0.20"/></pattern>'
            + "".join(self.defs))
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" '
            f'width="{self.w}" height="{self.h}" role="img" '
            f'aria-label="{self.esc(self.title)}">'
            f'<title>{self.esc(self.title)}</title>'
            f'<defs>{defs}</defs>'
            f'<rect width="{self.w}" height="{self.h}" fill="{t["bg"]}"/>'
            + "".join(self.o) + '</svg>')


# ====================================================================
# Shared pieces for the detailed layer
# ====================================================================
def hdr(c, x, y, s, anchor="start"):
    c.text(x, y, s.upper(), 11, fill=c.t["mutedFg"], weight=500, spacing="0.1em",
           anchor=anchor)


def bw_(label):
    return len(label) * 7 + 16


def note(c, x, y, w, lines, col=None, badge=None):
    """Dashed explanatory callout; returns its height."""
    t = c.t
    col = col or t["warning"]
    b0 = 44 if badge else 24
    h = b0 + 18 * (len(lines) - 1) + 14
    c.card(x, y, w, h, fill=tint(col, t["tintBase"], 0.10), stroke=col, dash="5 4")
    if badge:
        c.badge(x + 16, y + 12, badge, col, bw_(badge))
    for i, ln in enumerate(lines):
        c.text(x + 16, y + b0 + 18 * i, ln, 12)
    return h


def dline(c, x1, y1, x2, y2, col=None, arrow=True):
    col = col or c.t["mutedFg"]
    m = ' marker-end="url(#ar)"' if arrow else ""
    c.o.append(f'<path d="M {x1} {y1} L {x2} {y2}" stroke="{col}" stroke-width="1.5" '
               f'fill="none" stroke-dasharray="4 4"{m}/>')


def label(c, x, y, s, anchor="middle", col=None):
    c.text(x, y, s, 11, fill=col or c.t["mutedFg"], anchor=anchor)


# ====================================================================
# Diagram 1: what the job is (hero)
# ====================================================================
def d_hero(t):
    c = Canvas(t, 1180, 372, "Episode before and after processing")
    mut, pri = t["mutedFg"], t["primary"]
    c.card(20, 20, 1140, 240)
    c.text(44, 52, "One episode", 18, weight=600)
    c.text(1136, 52, "18 MINUTES SHORTER", 12, fill=mut, weight=500, anchor="end",
           spacing="0.08em")
    full, units = 1092.0, 62.0
    hdr(c, 44, 92, "As published")
    hdr(c, 1136, 92, "62 min", anchor="end")
    c.ribbon(44, 102, full, [
        (5, "ad", "Pre-roll"), (19, "show", "Show content"),
        (8, "ad", "Mid-roll"), (25, "show", "Show content"),
        (5, "ad", "Post-roll")])
    c.arrow(590, 148, 590, 176, color=pri)
    label(c, 606, 168, "ads found and cut", "start", pri)
    kept = full * 44 / units
    hdr(c, 44, 198, "As served")
    hdr(c, 1136, 198, "44 min", anchor="end")
    c.ribbon(44, 208, kept, [(19, "show", "Show content"), (25, "show", "Show content")])
    c.o.append(
        f'<rect x="{44+kept+6}" y="208" width="{full-kept-6}" height="34" rx="6" '
        f'fill="none" stroke="{mut}" stroke-width="1" stroke-dasharray="4 4" opacity="0.7"/>')
    c.text(44 + kept + (full - kept) / 2, 229, "18 min of ads, gone", 11, fill=mut,
           anchor="middle", weight=500)
    note(c, 20, 280, 1140, ["The listener subscribes to one MinusPod address, once.",
                            "Everything below happens behind that address."],
         col=pri, badge="BEHIND THE ADDRESS")
    return c.render()


# ====================================================================
# Diagram 2: how work arrives
# ====================================================================
def d_arrive(t):
    c = Canvas(t, 1180, 360, "How episodes enter the processing queue")
    pri, mut = t["primary"], t["mutedFg"]
    hdr(c, 20, 34, "Triggers")
    hdr(c, 480, 34, "Queue")
    hdr(c, 860, 34, "Pipeline")
    trig = [("Scheduled poll", "default every 15 minutes", None),
            ("Publisher announce", "instant", "OPTIONAL"),
            ("Listener presses play", "on demand", None)]
    for i, (a, b, tag) in enumerate(trig):
        y = 50 + i * 84
        c.card(20, y, 320, 64)
        c.text(38, y + 28, a, 14, weight=500)
        c.text(38, y + 48, b, 12, fill=mut)
        if tag:
            c.badge(340 - 16 - bw_(tag), y + 12, tag, mut, bw_(tag))
        c.curve(340, y + 32, 480, 180, color=mut)
    c.card(480, 140, 220, 80, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(590, 172, "Queue", 16, fill=pri, weight=600, anchor="middle")
    c.text(590, 194, "one episode at a time", 12, fill=pri, anchor="middle", opacity="0.85")
    c.arrow(700, 180, 860, 180)
    label(c, 780, 172, "next in line")
    c.card(860, 140, 300, 80)
    c.text(880, 172, "Pipeline", 14, weight=500)
    c.text(880, 194, "runs once, then served from disk", 12, fill=mut)
    note(c, 480, 260, 680, ["Only the leader worker runs episodes. A play request that lands on",
                            "another worker is queued and picked up within 30 s."],
         badge="ONE RUNNER")
    return c.render()


# ====================================================================
# Diagram 3: the Whisper pool
# ====================================================================
def d_pool(t):
    c = Canvas(t, 1180, 440, "The Whisper pool runs several episodes at once")
    pri, mut = t["primary"], t["mutedFg"]
    hdr(c, 20, 34, "Waiting")
    for i, name in enumerate(("Episode 5", "Episode 6", "Episode 7")):
        y = 50 + i * 62
        c.card(20, y, 220, 50)
        c.text(38, y + 22, name, 13, fill=t["cardFg"], weight=500)
        c.text(38, y + 40, "queued", 11, fill=mut)
    c.arrow(246, 140, 300, 140, color=pri)
    label(c, 273, 128, "slot frees")
    px, py, pw, ph = 300, 40, 460, 260
    c.card(px, py, pw, ph, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(px + 20, py + 30, "Whisper pool", 15, fill=pri, weight=600)
    c.text(px + pw - 20, py + 30, "UP TO 4 AT ONCE", 11, fill=pri, weight=500,
           anchor="end", spacing="0.08em")
    for i in range(4):
        sy = py + 50 + i * 52
        c.card(px + 20, sy, pw - 40, 40)
        c.text(px + 38, sy + 25, f"Episode {i + 1}", 13, weight=500)
        c.badge(px + pw - 32 - bw_("TRANSCRIBING"), sy + 10, "TRANSCRIBING", pri,
                bw_("TRANSCRIBING"))
    c.arrow(px + pw + 4, 170, 860, 170, color=pri)
    label(c, 810, 162, "transcript ready")
    hdr(c, 860, 34, "Then")
    c.card(860, 130, 300, 80)
    c.text(880, 162, "The pipeline", 14, weight=500)
    c.text(880, 184, "detect, cut, serve", 12, fill=mut)
    note(c, 300, 330, 460, ["Four is an example, not the setting.",
                            "WHISPER_POOL_MAX_EPISODES: 1 to 16, default 1."],
         badge="CONFIGURABLE")
    note(c, 860, 250, 300, ["Remote Whisper API only.", "Off by default."],
         badge="WHEN IT RUNS")
    return c.render()


# ====================================================================
# Diagram 4: the standard pipeline
# ====================================================================
def d_pipeline(t):
    row, gap, gg = 54, 16, 34
    X, GW, SX, SW = 20, 800, 36, 768
    mut = t["mutedFg"]
    groups = [
        ("Fetch and transcribe", [
            ("01", "Download", "Fetch the published file", None, "primary"),
            ("02", "Transcribe", "Speech to timestamped text", "SLOWEST STEP", "mutedFg")]),
        ("Gather evidence, in parallel", [
            [("03", "Second download", "Copies disagree where ads sit",
              "PER FEED", "mutedFg"),
             ("04", "Audio analysis", "Loudness jumps and cue sounds", None, "primary")]]),
        ("Decide and cut", [
            ("05", "Detect ads", "Fingerprints and scripts first, then the model reads", "PAID", "warning"),
            ("06", "Validate", "Confidence, length, position, past corrections", "FREE", "success"),
            ("07", "Boundary review", "A second opinion on each cut's edges", "PAID, OPT-IN", "warning"),
            ("08", "Cut audio", "Remove or replace the accepted spans", "THE EDIT", "destructive")]),
        ("Check and publish", [
            ("09", "Verify pass", "Re-read the cut file for missed ads", "PAID, PER FEED", "warning"),
            ("10", "Transcript, chapters", "Rebuilt against the shorter timeline", "PAID", "warning"),
            ("11", "Publish feed", "The rewritten RSS points at the processed file", "DONE", "success")]),
    ]
    heights = []
    for _, items in groups:
        n = len(items)
        heights.append(34 + n * row + (n - 1) * gap + 16)
    H = 20 + sum(heights) + gg * 3 + 20
    c = Canvas(t, 1180, H, "The standard processing pipeline")

    def stage(x, y, w, n, name, desc, tag, key):
        acc = t[key]
        c.card(x, y, w, row)
        c.o.append(f'<rect x="{x}" y="{y+1}" width="4" height="{row-2}" rx="2" fill="{acc}"/>')
        c.text(x + 22, y + 23, n, 12, fill=mut, weight=500)
        c.text(x + 52, y + 23, name, 15, weight=500)
        c.text(x + 52, y + 41, desc, 12, fill=mut)
        if tag:
            tw = bw_(tag)
            c.badge(x + w - 16 - tw, y + 17, tag, acc if key != "primary" else mut, tw)

    y = 20
    tops = []
    for gi, (title, items) in enumerate(groups):
        h = heights[gi]
        tops.append((y, h))
        c.card(X, y, GW, h, fill=tint(t["primary"], t["tintBase"], 0.04))
        hdr(c, X + 16, y + 22, title)
        sy = y + 34
        for it in items:
            if isinstance(it, list):
                hw = (SW - 24) // 2
                stage(SX, sy, hw, *it[0])
                stage(SX + hw + 24, sy, hw, *it[1])
            else:
                stage(SX, sy, SW, *it)
            sy += row + gap
        y += h + gg
    labels = ["transcript ready", "hints and signals", "the cut file"]
    for gi in range(3):
        yb = tops[gi][0] + tops[gi][1]
        c.arrow(420, yb, 420, yb + gg)
        label(c, 432, yb + 21, labels[gi], "start")
    # arrows between stacked stages inside a group
    for gi, (title, items) in enumerate(groups):
        y0 = tops[gi][0] + 34
        for k in range(len(items) - 1):
            ya = y0 + k * (row + gap) + row
            c.arrow(420, ya, 420, ya + gap)
    # side callouts
    sx = 860
    gy0 = tops[0][0]
    note(c, sx, gy0 + 40, 300, ["Long episodes transcribe in chunks.",
                                "The next chunk is prepared while",
                                "the GPU works."], badge="CHUNKED")
    dline(c, sx, gy0 + 70, X + GW, gy0 + 70, arrow=False)
    gy2 = tops[2][0]
    note(c, sx, gy2 + 40, 300, ["Amber badges make an LLM call.",
                                "A hosted Whisper API bills too;",
                                "a local GPU costs only time."], badge="PAID STEPS")
    dline(c, sx, gy2 + 70, X + GW, gy2 + 70, arrow=False)
    gy3 = tops[3][0]
    note(c, sx, gy3 + 40, 300, ["The verify pass re-transcribes",
                                "the cut file the same way."], badge="VERIFY")
    dline(c, sx, gy3 + 70, X + GW, gy3 + 70, arrow=False)
    return c.render()


# ====================================================================
# Diagram 5: evidence into the gate
# ====================================================================
def d_gate(t):
    c = Canvas(t, 1180, 500, "Five evidence sources and five outcomes")
    pri, mut = t["primary"], t["mutedFg"]
    cw, gap = 212, 20
    hdr(c, 20, 34, "Evidence")
    ev = [("Acoustic match", "Clip seen before", t["success"], "FREE"),
          ("Known script", "Wording learned", t["success"], "FREE"),
          ("Copies differ", "Two fetches differ", t["success"], "FREE"),
          ("Model reads it", "Judges the words", t["warning"], "PAID"),
          ("Audio signals", "Loudness and cues", t["success"], "FREE")]
    for i, (a, b, col, tag) in enumerate(ev):
        x = 20 + i * (cw + gap)
        c.card(x, 46, cw, 76, fill=tint(col, t["tintBase"], 0.10), stroke=col)
        c.badge(x + 16, 60, tag, col)
        c.text(x + 16, 94, a, 14, weight=500)
        c.text(x + 16, 112, b, 12, fill=mut)
        c.vcurve(x + cw / 2, 122, 590, 196)
    c.card(420, 196, 340, 62, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(590, 222, "Decision gate", 16, fill=pri, weight=600, anchor="middle")
    c.text(590, 242, "cuts at 80% confidence by default", 12, fill=pri, anchor="middle",
           opacity="0.85")
    note(c, 20, 190, 370, ["Free sources run first and go into",
                           "the model prompt as hints."], col=t["success"], badge="BEFORE THE MODEL")
    note(c, 790, 190, 370, ["Audio signals only promote or extend",
                            "a cut, after detection runs."], badge="AFTER DETECTION")
    hdr(c, 20, 292, "Outcomes")
    out = [("Cut", "Removed", t["destructive"]), ("Beep", "Tone, same runtime", t["warning"]),
           ("Keep", "Left in by policy", t["success"]), ("Hold", "Waits for a person", t["warning"]),
           ("Reject", "Logged with a reason", mut)]
    for i, (a, b, col) in enumerate(out):
        x = 20 + i * (cw + gap)
        c.vcurve(590, 258, x + cw / 2, 308)
        c.card(x, 308, cw, 78)
        c.o.append(f'<rect x="{x}" y="308" width="{cw}" height="4" fill="{col}" rx="2"/>')
        c.text(x + 16, 344, a, 15, weight=600, fill=col)
        c.text(x + 16, 364, b, 12, fill=mut)
    hdr(c, 20, 428, "Resulting audio")
    c.ribbon(20, 438, 1140, [
        (6, "gone", "cut"), (18, "show", "Show"), (5, "beep", "beep"),
        (16, "show", "Show"), (7, "keep", "kept intro"), (24, "show", "Show"),
        (6, "gone", "cut")], h=26)
    return c.render()


# ====================================================================
# Diagram 6: processing modes
# ====================================================================
def d_modes(t):
    mut = t["mutedFg"]
    modes = [
        ("Standard", "Find the ads, cut them out", None,
         [(7, "gone", ""), (26, "show", "Show"), (11, "gone", ""),
          (34, "show", "Show"), (8, "gone", "")]),
        ("Keep content only", "Mark the show, remove everything else", "EXPERIMENTAL",
         [(7, "gone", ""), (26, "show", "Marked"), (11, "gone", ""),
          (34, "show", "Marked"), (8, "gone", "")]),
        ("Cue-only", "Cut on cue pairs and known patterns, no model call", "EXPERIMENTAL",
         [(12, "show", "Show"), (13, "gone", "cue to cue"), (44, "show", "Show"),
          (12, "gone", "cue to cue"), (15, "show", "Show")]),
        ("Skip ad detection", "Transcript and chapters only, nothing cut", None,
         [(100, "show", "Full episode, transcript and chapters added")]),
        ("Pass-through", "Relay the file exactly as published", "ALSO PER EPISODE",
         [(100, "show", "Full episode, untouched")]),
    ]
    row = 68
    H = 50 + len(modes) * (row + 12) + 100
    c = Canvas(t, 1180, H, "The five per-feed processing modes")
    hdr(c, 40, 34, "Mode, one per feed")
    hdr(c, 580, 34, "Audio it produces")
    y = 46
    for name, desc, flag, segs in modes:
        c.card(20, y, 1140, row)
        c.text(40, y + 28, name, 15, weight=500)
        c.text(40, y + 48, desc, 12, fill=mut)
        if flag:
            c.badge(40 + len(name) * 8.6 + 12, y + 14, flag, t["warning"], bw_(flag))
        c.ribbon(580, y + 20, 560, segs, h=28)
        y += row + 12
    note(c, 20, y + 8, 560, ["Changing the mode does not change the published address.",
                             "One episode can also be set to pass-through alone."],
         col=t["primary"], badge="PER FEED")
    note(c, 600, y + 8, 560, ["Marked content must cover 55% of runtime, no cut over",
                              "7 minutes or a quarter of the episode, else standard."],
         badge="KEEP CONTENT SAFETY NET")
    return c.render()


# ====================================================================
# Diagram 7: re-run entry points
# ====================================================================
def d_rerun(t):
    mut, pri = t["mutedFg"], t["primary"]
    steps = ["Download", "Transcribe", "Detect", "Validate", "Cut", "Publish"]
    modes = [
        ("Reprocess", "The routine option",
         ["reuse", "reuse", "run", "run", "run", "run"], None),
        ("Full analysis", "Asks the model with a clean slate",
         ["reuse", "reuse", "run", "run", "run", "run"], "IGNORES LEARNED PATTERNS"),
        ("Re-detect ads", "Iterate on a model or a setting",
         ["reuse", "reuse", "run", "run", "run", "run"], "NEEDS A SAVED TRANSCRIPT"),
        ("Recut audio", "After editing the ad list by hand",
         ["reuse", "reuse", "skip", "skip", "run", "run"], "NO MODEL CALL"),
    ]
    row, gap = 86, 12
    H = 66 + len(modes) * (row + gap) + 110
    c = Canvas(t, 1180, H, "Where each re-run mode enters the pipeline")
    sx, sw_, sg = 560, 92, 6
    hdr(c, 20, 32, "Re-run mode")
    groups = [("Saved work", 0, 1), ("Analysis", 2, 3), ("Edit and serve", 4, 5)]
    for name, a, b in groups:
        gx = sx + a * (sw_ + sg)
        gw = (b - a + 1) * sw_ + (b - a) * sg
        c.rule(gx, 38, gx + gw, color=mut)
        hdr(c, gx + gw / 2, 32, name, anchor="middle")
    for i, s in enumerate(steps):
        c.text(sx + i * (sw_ + sg) + sw_ / 2, 56, s.upper(), 10, fill=mut, weight=500,
               anchor="middle", spacing="0.06em")
    y = 66
    for name, desc, states, flag in modes:
        c.card(20, y, 1140, row)
        c.text(44, y + 28, name, 15, weight=500)
        c.text(44, y + 48, desc, 12, fill=mut)
        if flag:
            c.badge(44, y + 58, flag, t["warning"], bw_(flag))
        for i, st in enumerate(states):
            x = sx + i * (sw_ + sg)
            if st == "run":
                c.card(x, y + 29, sw_, 28, fill=tint(pri, t["tintBase"]), stroke=pri, r=4)
                c.text(x + sw_ / 2, y + 48, "runs", 11, fill=pri, weight=500, anchor="middle")
            elif st == "reuse":
                c.card(x, y + 29, sw_, 28, fill=t["bg"], stroke=t["border"], r=4)
                c.text(x + sw_ / 2, y + 48, "reused", 11, fill=mut, anchor="middle")
            else:
                c.card(x, y + 29, sw_, 28, fill="none", stroke=t["border"], r=4, dash="3 3")
                c.text(x + sw_ / 2, y + 48, "skipped", 11, fill=mut, anchor="middle",
                       opacity="0.7")
        y += row + gap
    note(c, 20, y + 6, 370, ["MinusPod keeps each episode's original",
                             "audio, so a correction is a re-cut."], col=pri,
         badge="ORIGINAL KEPT")
    note(c, 410, y + 6, 370, ["Reprocess and full analysis reuse the saved",
                              "transcript, or transcribe again without one."],
         badge="SAVED TRANSCRIPT")
    note(c, 800, y + 6, 360, ["Re-detect ads always needs one,",
                              "so it skips episodes that have none."],
         badge="NEEDS A TRANSCRIPT")
    return c.render()


# ====================================================================
# Diagram 8: learning loop
# ====================================================================
def d_learn(t):
    c = Canvas(t, 1180, 390, "How detection gets cheaper over time")
    pri, mut = t["primary"], t["mutedFg"]
    hdr(c, 20, 34, "The loop")
    steps = [("Ad detected and cut", "by any detection source", None),
             ("Pattern stored", "no person in the loop", None),
             ("Corrections refine it", "confirm, adjust, or reject", None),
             ("Matched free", "next time, before the model", t["success"])]
    bw = 258
    for i, (a, b, col) in enumerate(steps):
        x = 20 + i * (bw + 36)
        c.card(x, 46, bw, 66, fill=tint(col, t["tintBase"], 0.12) if col else t["card"],
               stroke=col or t["border"])
        c.text(x + 16, 74, a, 14, weight=500, fill=col or t["cardFg"])
        c.text(x + 16, 94, b, 12, fill=mut)
        if i < 3:
            c.arrow(x + bw, 79, x + bw + 36, 79)
    c.o.append(
        f'<path d="M 1160 112 C 1160 150, 1160 150, 590 150 C 20 150, 20 150, 20 112" '
        f'stroke="{mut}" stroke-width="1.5" fill="none" stroke-dasharray="4 4" '
        f'marker-end="url(#ar)"/>')
    label(c, 590, 168, "the next episode starts here with the pattern already known")
    hdr(c, 20, 206, "Patterns widen as they prove out")
    scopes = [("This show", "where it was first seen"),
              ("This network", "same publisher, other shows"),
              ("Everywhere", "national sponsors")]
    for i, (a, b) in enumerate(scopes):
        x = 20 + i * 410
        w = 320
        c.card(x, 220, w, 56, fill=tint(pri, t["tintBase"], 0.10 + i * 0.05), stroke=pri)
        c.text(x + 16, 244, a, 14, weight=500, fill=pri)
        c.text(x + 16, 264, b, 12, fill=mut)
        if i < 2:
            c.arrow(x + w, 248, x + w + 90, 248)
            label(c, x + w + 45, 240, "proves out")
    note(c, 20, 300, 360, ["Every pattern carries its own",
                           "confirmation and false-positive counts."], col=pri,
         badge="PER PATTERN")
    note(c, 410, 300, 360, ["Created or confirmed patterns always cut.",
                            "Learned ones follow the feed's category actions."],
         badge="WHAT CUTS")
    note(c, 800, 300, 360, ["Community sync is opt-in both ways.",
                            "Nothing local is published unless you submit it."],
         badge="SHARING")
    return c.render()


# ====================================================================
# Diagram 9: failure handling
# ====================================================================
def d_fail(t):
    c = Canvas(t, 1180, 410, "What happens when a stage fails")
    mut, war = t["mutedFg"], t["warning"]
    hdr(c, 20, 34, "What goes wrong")
    hdr(c, 440, 34, "What MinusPod does")
    rows = [("Model call fails", "detection stage", None, "then",
             "Publishes any free-evidence cuts, flags the episode, queues one retry"),
            ("Endpoint offline", "model endpoint unreachable", "OPT-IN QUEUE", "if enabled",
             "Opt-in offline queue parks it and re-queues when the endpoint returns"),
            ("A cut looks wrong", "validator is unsure", None, "instead",
             "It was held, not cut. Approving re-cuts from the original")]
    y = 46
    for a, sub, tag, lab, b in rows:
        c.card(20, y, 340, 64, fill=tint(war, t["tintBase"], 0.12), stroke=war)
        c.text(40, y + 28, a, 14, weight=500, fill=war)
        c.text(40, y + 48, sub, 12, fill=mut)
        if tag:
            c.badge(360 - 16 - bw_(tag), y + 12, tag, war, bw_(tag))
        c.arrow(360, y + 32, 440, y + 32)
        label(c, 400, y + 24, lab)
        c.card(440, y, 720, 64)
        c.text(460, y + 38, b, 13)
        y += 80
    note(c, 20, y + 12, 560, ["Nothing fails silently. With no free evidence to fall back",
                              "on, or a bad API key, the run fails and waits on the episode page."],
         col=t["destructive"], badge="HARD FAILURE")
    note(c, 600, y + 12, 560, ["The offline queue is off until you turn it on.",
                               "Without it, an unreachable endpoint retries, then gives up."],
         badge="OFFLINE QUEUE")
    return c.render()


def d_systemone(t):
    """System One: one detection window becomes batched yes/no questions."""
    c = Canvas(t, 1180, 670, "One detection window sent to System One as batched yes or no questions")
    mut, pri, war = t["mutedFg"], t["primary"], t["warning"]

    def head(x, y, s):
        c.text(x, y, s, 11, fill=mut, weight=500, spacing="0.1em")

    # left: windows over the transcript
    head(20, 34, "TRANSCRIPT WINDOWS")
    c.card(20, 46, 300, 28, fill=t["bg"])
    c.text(170, 65, "full episode transcript", 11, fill=mut, anchor="middle")
    c.text(105, 94, "window size", 11, fill=mut, anchor="middle")
    c.o.append(f'<path d="M 20 100 L 190 100 M 20 96 L 20 104 M 190 96 L 190 104" '
               f'stroke="{mut}" stroke-width="1" fill="none"/>')
    c.card(20, 110, 170, 30, fill=tint(pri, t["tintBase"]), stroke=pri, r=6)
    c.text(105, 130, "window 1", 12, fill=pri, weight=500, anchor="middle")
    c.card(150, 148, 170, 30, fill=tint(pri, t["tintBase"]), stroke=pri, r=6)
    c.text(235, 168, "window 2", 12, fill=pri, weight=500, anchor="middle")
    c.o.append(f'<rect x="150" y="106" width="40" height="76" rx="4" fill="{war}" '
               f'fill-opacity="0.28" stroke="{war}" stroke-width="1" stroke-dasharray="3 3"/>')
    c.arrow(170, 182, 170, 196, color=war)
    c.text(170, 214, "overlap is scored twice", 12, fill=war, weight=500, anchor="middle")
    c.curve(190, 125, 370, 82, color=pri)

    # middle: one request
    head(370, 34, "ONE WINDOW, ONE REQUEST")
    c.card(370, 50, 440, 64, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(388, 72, "State: the whole window transcript", 14, fill=pri, weight=600)
    c.text(388, 90, "L0001 ... L0148", 13, weight=500)
    c.text(388, 106, "shared by every question", 12, fill=mut)

    c.arrow(390, 114, 390, 178)
    c.o.append(f'<path d="M 390 146 L 358 146 L 358 393 L 368 393" stroke="{mut}" '
               f'stroke-width="1.5" fill="none" marker-end="url(#ar)"/>')
    c.badge(408, 124, "questions per request", war)
    c.text(408, 168, "the state travels with every batch", 11, fill=mut)

    batches = [(178, "BATCH 1", (37, 38, 39)), (328, "BATCH 2", (40, 41, 148))]
    for by, name, lines in batches:
        c.card(370, by, 440, 130)
        c.text(386, by + 20, name, 11, fill=mut, weight=500, spacing="0.1em")
        c.text(556, by + 20, "ONE QUESTION PER SEGMENT", 11, fill=mut, weight=500,
               spacing="0.1em")
        c.card(386, by + 30, 150, 88, fill=tint(pri, t["tintBase"], 0.12), stroke=pri, r=6)
        c.text(400, by + 54, "State (copy)", 13, fill=pri, weight=500)
        c.text(400, by + 74, "L0001 ... L0148", 12)
        c.text(400, by + 92, "whole window", 12, fill=mut)
        for i, n in enumerate(lines):
            qy = by + 30 + i * 31
            c.card(556, qy, 240, 26, r=6)
            c.text(570, qy + 18, f"Is line {n} advertising?", 12)

    # right: server
    head(850, 34, "SERVER")
    for by, name in ((178, "batch 1"), (328, "batch 2")):
        c.arrow(810, by + 30, 850, by + 30)
        c.card(850, by, 280, 60)
        c.text(866, by + 26, "Prefill: whole window", 14, weight=500)
        c.text(866, by + 46, f"window plus the questions of {name}", 12, fill=mut)
        c.o.append(f'<path d="M 1130 {by+30} L 1146 {by+30}" stroke="{mut}" '
                   f'stroke-width="1.5" fill="none"/>')
    c.card(850, 250, 280, 66, fill=tint(war, t["tintBase"], 0.10), stroke=war, dash="5 4")
    for i, ln in enumerate(("a server that cannot reuse the prompt",
                            "cache prefills the window again",
                            "for every batch")):
        c.text(866, 272 + i * 16, ln, 12, fill=war if i == 0 else t["cardFg"])
    c.o.append(f'<path d="M 1146 208 L 1146 528" stroke="{mut}" stroke-width="1.5" fill="none"/>')
    c.text(1138, 430, "yes or no per line", 12, fill=mut, anchor="end")

    c.arrow(1146, 528, 1132, 528)
    c.card(850, 500, 280, 56, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(990, 524, "Decision gate", 16, fill=pri, weight=600, anchor="middle")
    c.text(990, 544, "cuts at 80% confidence by default", 12, fill=pri,
           anchor="middle", opacity="0.85")
    out = [("Cut", "Removed", t["destructive"]), ("Hold", "For review", war),
           ("Keep", "Left in by policy", t["success"])]
    for i, (a, b, col) in enumerate(out):
        x = 850 + i * 96
        c.vcurve(990, 556, x + 44, 590)
        c.card(x, 590, 88, 62)
        c.o.append(f'<rect x="{x}" y="590" width="88" height="4" fill="{col}" rx="2"/>')
        c.text(x + 10, 616, a, 14, weight=600, fill=col)
        c.text(x + 10, 636, b, 10, fill=mut)

    # budget callout
    c.card(370, 490, 440, 80, fill=tint(war, t["tintBase"], 0.10), stroke=war, dash="5 4")
    c.badge(386, 500, "max prompt tokens", war)
    c.text(386, 540, "A window over the budget is split before sending.", 12)
    c.text(386, 558, "A context error retries at half size.", 12)
    c.arrow(590, 490, 590, 460, color=war, dash="4 4")
    return c.render()


def d_youtube(t):
    """YouTube feed: discovery, SponsorBlock gate, then cut, transcript and serve."""
    c = Canvas(t, 1180, 680, "The YouTube pipeline: discovery, SponsorBlock gate, cutting and serving")
    mut, pri, war, ok = t["mutedFg"], t["primary"], t["warning"], t["success"]

    # top row
    hdr(c, 20, 24, "Discovery")
    hdr(c, 440, 24, "Serving")
    hdr(c, 940, 24, "Side pass")
    c.card(20, 34, 380, 100)
    c.text(36, 58, "Discovery", 15, weight=500)
    c.text(36, 76, "playlist, channel or video, on the refresh schedule", 12, fill=mut)
    bw = c.badge(36, 86, "DATA API KEY", pri, bw_("DATA API KEY"))
    c.badge(36 + bw + 8, 86, "YT-DLP", mut, bw_("YT-DLP"))
    c.text(36, 124, "private and deleted hidden", 12, fill=mut)
    c.arrow(400, 84, 440, 84)
    c.card(440, 34, 460, 100)
    c.text(456, 58, "Serving", 15, weight=500)
    for i, ln in enumerate(("the feed lists every discovered episode",
                            "first play starts processing,",
                            "the app is told to retry")):
        c.text(456, 78 + i * 18, ln, 12, fill=mut)
    c.card(940, 34, 220, 100, fill="none", dash="5 4")
    c.text(956, 58, "Metadata backfill", 15, weight=500)
    for i, ln in enumerate(("descriptions and publish", "times, 10 per pass,", "no download")):
        c.text(956, 78 + i * 16, ln, 12, fill=mut)
    c.arrow(670, 134, 670, 180)

    # gate
    gy = 180
    c.card(20, gy, 880, 250, fill=tint(pri, t["tintBase"], 0.06), stroke=pri)
    c.text(40, gy + 24, "SPONSORBLOCK GATE", 11, fill=pri, weight=500, spacing="0.1em")
    c.card(40, gy + 45, 130, 60)
    c.text(56, gy + 71, "Processing delay", 14, weight=500)
    c.text(56, gy + 90, "before first look", 11, fill=mut)
    c.arrow(170, gy + 75, 200, gy + 75)
    c.card(200, gy + 45, 220, 60, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(216, gy + 71, "Probe N times", 14, fill=pri, weight=600)
    c.text(216, gy + 90, "at the probe interval", 12, fill=pri, opacity="0.85")
    nh = note(c, 200, gy + 135, 220, ["SponsorBlock unreachable:", "waits are held, no attempt",
                                       "counted, other feeds", "keep running"])
    c.arrow(310, gy + 135, 310, gy + 107, color=war, dash="4 4")

    c.card(460, gy + 20, 200, 50, fill=tint(ok, t["tintBase"], 0.12), stroke=ok)
    c.text(476, gy + 42, "Marks found", 14, fill=ok, weight=600)
    c.text(476, gy + 59, "first probe that finds them", 11, fill=mut)
    c.card(460, gy + 110, 200, 48)
    c.text(476, gy + 131, "None after last probe", 13, weight=500)
    c.text(476, gy + 148, "marks may arrive later", 11, fill=mut)
    c.curve(420, gy + 66, 460, gy + 45, color=ok)
    c.curve(420, gy + 84, 460, gy + 134)

    c.card(700, gy + 82, 180, 44)
    c.text(716, gy + 103, "LLM fallback", 14, weight=500)
    c.text(716, gy + 118, "fallback on", 11, fill=mut)
    c.card(700, gy + 142, 180, 64)
    c.text(716, gy + 163, "Watch window", 14, weight=500)
    c.text(716, gy + 180, "fallback off: check each", 11, fill=mut)
    c.text(716, gy + 194, "interval to the ceiling", 11, fill=mut)
    c.curve(660, gy + 134, 700, gy + 104)
    c.curve(660, gy + 134, 700, gy + 174)

    bx = 890
    c.o.append(f'<path d="M 660 {gy+45} L {bx} {gy+45}" stroke="{ok}" stroke-width="1.5" fill="none"/>')
    c.o.append(f'<path d="M 880 {gy+104} L {bx} {gy+104} M 880 {gy+174} L {bx} {gy+174} '
               f'M {bx} {gy+45} L {bx} {gy+174}" stroke="{mut}" stroke-width="1.5" fill="none"/>')
    c.arrow(bx, gy + 45, 940, gy + 45, color=mut)
    label(c, 884, gy + 37, "queued", "end")

    # right column: cut, transcript, serve
    hdr(c, 940, gy - 8, "After the gate")
    c.card(940, gy, 220, 196)
    c.o.append(f'<rect x="940" y="{gy+1}" width="4" height="194" rx="2" fill="{t["destructive"]}"/>')
    c.text(960, gy + 28, "Cutting", 15, weight=500)
    c.text(960, gy + 50, "marks cut as given", 12, fill=mut)
    c.text(960, gy + 68, "learned patterns always run", 12, fill=mut)
    c.badge(960, gy + 80, "LLM ON TOP, PER FEED", war, bw_("LLM ON TOP, PER FEED") - 20)
    note(c, 956, gy + 112, 188, ["marks change in the", "watch window: recut from", "the retained original"])
    c.arrow(1050, gy + 196, 1050, gy + 226)
    c.card(940, gy + 226, 220, 110)
    c.text(960, gy + 254, "Transcript and chapters", 14, weight=500)
    for i, ln in enumerate(("captions become the", "transcript, Whisper skipped", "creator chapters reused")):
        c.text(960, gy + 276 + i * 18, ln, 12, fill=mut)
    c.arrow(1050, gy + 336, 1050, gy + 366)
    c.card(940, gy + 366, 220, 100, fill=tint(pri, t["tintBase"]), stroke=pri)
    c.text(960, gy + 400, "Served cut", 16, fill=pri, weight=600)
    c.text(960, gy + 422, "the feed and episode page", 12, fill=pri, opacity="0.85")
    note(c, 600, gy + 392, 290, ["rate, submit to SponsorBlock by hand"], col=mut)
    dline(c, 890, gy + 416, 940, gy + 416, col=mut)
    return c.render()


DIAGRAMS = [
    ("wf-overview", d_hero),
    ("wf-arrival", d_arrive),
    ("wf-pool", d_pool),
    ("wf-pipeline", d_pipeline),
    ("wf-detection", d_gate),
    ("wf-modes", d_modes),
    ("wf-rerun", d_rerun),
    ("wf-learning", d_learn),
    ("wf-failure", d_fail),
    ("wf-systemone", d_systemone),
    ("wf-youtube", d_youtube),
]

if __name__ == "__main__":
    out = "docs/images"
    os.makedirs(out, exist_ok=True)
    for name, fn in DIAGRAMS:
        for suffix, tokens in (("light", LIGHT), ("dark", DARK)):
            path = os.path.join(out, f"{name}-{suffix}.svg")
            with open(path, "w") as f:
                f.write(fn(tokens))
            print(path)

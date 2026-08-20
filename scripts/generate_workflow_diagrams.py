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
# Diagram 1: what the job is (hero)
# ====================================================================
def d_hero(t):
    c = Canvas(t, 880, 268, "Episode before and after processing")
    c.card(20, 20, 840, 228)
    c.text(44, 52, "One episode", 18, weight=600)
    c.text(836, 52, "18 MINUTES SHORTER", 12, fill=t["mutedFg"],
           weight=500, anchor="end", spacing="0.08em")

    full = 792.0
    units = 62.0
    c.text(44, 92, "AS PUBLISHED", 11, fill=t["mutedFg"], weight=500,
           spacing="0.1em")
    c.text(836, 92, "62 MIN", 11, fill=t["mutedFg"], weight=500, anchor="end",
           spacing="0.1em")
    c.ribbon(44, 102, full, [
        (5, "ad", "Pre-roll"), (19, "show", "Show content"),
        (8, "ad", "Mid-roll"), (25, "show", "Show content"),
        (5, "ad", "Post-roll")])

    c.arrow(440, 148, 440, 172, color=t["primary"])

    kept = full * 44 / units
    c.text(44, 194, "AS SERVED", 11, fill=t["mutedFg"], weight=500,
           spacing="0.1em")
    c.text(836, 194, "44 MIN", 11, fill=t["primary"], weight=500, anchor="end",
           spacing="0.1em")
    c.ribbon(44, 204, kept, [
        (19, "show", "Show content"), (25, "show", "Show content")])
    # the reclaimed space, drawn as absence
    c.o.append(
        f'<rect x="{44+kept+6}" y="204" width="{full-kept-6}" height="34" rx="6" '
        f'fill="none" stroke="{t["mutedFg"]}" stroke-width="1" '
        f'stroke-dasharray="4 4" opacity="0.7"/>')
    c.text(44 + kept + (full - kept) / 2, 225, "18 min of ads, gone", 11,
           fill=t["mutedFg"], anchor="middle", weight=500)
    return c.render()


# ====================================================================
# Diagram 2: how work arrives
# ====================================================================
def d_arrive(t):
    c = Canvas(t, 880, 250, "How episodes enter the processing queue")
    trig = [("Scheduled poll", "default every 15 minutes"),
            ("Publisher announce", "optional, instant"),
            ("Listener presses play", "on demand")]
    for i, (a, b) in enumerate(trig):
        y = 20 + i * 70
        c.card(20, y, 250, 54)
        c.text(38, y + 24, a, 14, weight=500)
        c.text(38, y + 42, b, 12, fill=t["mutedFg"])
        c.curve(270, y + 27, 350, 125)

    c.card(350, 92, 190, 66, fill=tint(t["primary"], t["tintBase"]),
           stroke=t["primary"])
    c.text(445, 118, "Queue", 16, fill=t["primary"], weight=600, anchor="middle")
    c.text(445, 138, "one episode at a time", 12, fill=t["primary"],
           anchor="middle", opacity="0.85")

    c.arrow(540, 125, 610, 125)
    c.card(610, 92, 250, 66)
    c.text(634, 118, "Pipeline", 14, weight=500)
    c.text(634, 138, "runs once, then served from disk", 12, fill=t["mutedFg"])
    return c.render()


# ====================================================================
# Diagram 3: the standard pipeline
# ====================================================================
def d_pipeline(t):
    """Vertical flow with one real parallel branch after transcription."""
    W, X, FW = 880, 20, 840
    row, gap, gapB = 54, 16, 34
    tail = [
        ("05", "Detect ads", "Fingerprints and learned scripts first, then the model reads the transcript",
         "PAID", "warning"),
        ("06", "Validate", "Confidence, length, position, and past corrections",
         "FREE", "success"),
        ("07", "Boundary review", "A second opinion on where each cut starts and ends",
         "PAID, OPT-IN", "warning"),
        ("08", "Cut audio", "Remove or replace the accepted spans",
         "THE EDIT", "destructive"),
        ("09", "Verify pass", "Re-read the cut file for anything the first pass missed",
         "PAID, PER FEED", "warning"),
        ("10", "Transcript, chapters", "Rebuilt against the new, shorter timeline",
         "PAID", "warning"),
        ("11", "Publish feed", "The rewritten RSS points at the processed file",
         "DONE", "success"),
    ]
    H = 20 + (row + gap) + (row + gapB) + (row + gapB)         + len(tail) * (row + gap) - gap + 20
    c = Canvas(t, W, H, "The standard processing pipeline")

    def stage(x, y, w, n, name, desc, tag=None, accent=None):
        c.card(x, y, w, row)
        c.o.append(f'<rect x="{x}" y="{y+1}" width="4" height="{row-2}" '
                   f'rx="2" fill="{accent or t["primary"]}"/>')
        c.text(x + 22, y + 23, n, 12, fill=t["mutedFg"], weight=500)
        c.text(x + 52, y + 23, name, 15, weight=500)
        c.text(x + 52, y + 41, desc, 12, fill=t["mutedFg"])
        if tag:
            tw = len(tag) * 6.6 + 16
            c.badge(x + w - 16 - tw, y + 17, tag, accent or t["mutedFg"], tw)

    def bracket(y_from, y_to, split=True):
        """Fan out to, or in from, the two parallel lanes."""
        mid = y_from + (y_to - y_from) / 2
        if split:
            c.o.append(
                f'<path d="M 440 {y_from} L 440 {mid} M 232 {mid} L 648 {mid} '
                f'M 232 {mid} L 232 {y_to-6} M 648 {mid} L 648 {y_to-6}" '
                f'stroke="{t["mutedFg"]}" stroke-width="1.5" fill="none"/>')
            c.arrow(232, y_to - 7, 232, y_to)
            c.arrow(648, y_to - 7, 648, y_to)
        else:
            c.o.append(
                f'<path d="M 232 {y_from} L 232 {mid} M 648 {y_from} L 648 {mid} '
                f'M 232 {mid} L 648 {mid} M 440 {mid} L 440 {y_to-6}" '
                f'stroke="{t["mutedFg"]}" stroke-width="1.5" fill="none"/>')
            c.arrow(440, y_to - 7, 440, y_to)

    y = 20
    stage(X, y, FW, "01", "Download", "Fetch the published file")
    c.arrow(440, y + row, 440, y + row + gap)
    y += row + gap

    stage(X, y, FW, "02", "Transcribe", "Speech to timestamped text; the next chunk is prepared while the GPU works",
          "SLOWEST STEP", t["mutedFg"])
    bracket(y + row, y + row + gapB, split=True)
    y += row + gapB

    hw = 412
    stage(X, y, hw, "03", "Second download",
          "Two copies disagree where ads were inserted",
          "PER FEED", t["mutedFg"])
    stage(X + FW - hw, y, hw, "04", "Audio analysis",
          "Loudness jumps and the show's own cue sounds")
    bracket(y + row, y + row + gapB, split=False)
    y += row + gapB

    for i, (n, name, desc, tag, key) in enumerate(tail):
        stage(X, y, FW, n, name, desc, tag, t[key])
        if i < len(tail) - 1:
            c.arrow(440, y + row, 440, y + row + gap)
        y += row + gap
    return c.render()


# ====================================================================
# Diagram 4: evidence into the gate
# ====================================================================
def d_gate(t):
    c = Canvas(t, 880, 400, "Five evidence sources and five outcomes")
    ev = [("Acoustic match", "Clip seen before", t["success"], "FREE"),
          ("Known script", "Wording learned", t["success"], "FREE"),
          ("Copies differ", "Two fetches differ", t["success"], "FREE"),
          ("Model reads it", "Judges the words", t["warning"], "PAID"),
          ("Audio signals", "Loudness and cues", t["success"], "FREE")]
    bw = 160
    for i, (a, b, col, tag) in enumerate(ev):
        x = 20 + i * (bw + 10)
        c.card(x, 20, bw, 76, fill=tint(col, t["tintBase"], 0.10), stroke=col)
        c.badge(x + 16, 34, tag, col)
        c.text(x + 16, 68, a, 14, weight=500)
        c.text(x + 16, 86, b, 12, fill=t["mutedFg"])
        c.vcurve(x + bw / 2, 96, 440, 140)

    c.card(300, 140, 280, 62, fill=tint(t["primary"], t["tintBase"]),
           stroke=t["primary"])
    c.text(440, 166, "Decision gate", 16, fill=t["primary"], weight=600,
           anchor="middle")
    c.text(440, 186, "cuts at 80% confidence by default", 12, fill=t["primary"],
           anchor="middle", opacity="0.85")

    out = [("Cut", "Removed", t["destructive"], "ad"),
           ("Beep", "Tone, same runtime", t["warning"], "beep"),
           ("Keep", "Left in by policy", t["success"], "keep"),
           ("Hold", "Waits for a person", t["warning"], "hold"),
           ("Reject", "Logged with a reason", t["mutedFg"], "gone")]
    ow = 160
    for i, (a, b, col, kind) in enumerate(out):
        x = 20 + i * (ow + 10)
        c.vcurve(440, 202, x + ow / 2, 250)
        c.card(x, 250, ow, 78)
        c.o.append(f'<rect x="{x}" y="250" width="{ow}" height="4" '
                   f'fill="{col}" rx="2"/>')
        c.text(x + 16, 286, a, 15, weight=600, fill=col)
        c.text(x + 16, 306, b, 12, fill=t["mutedFg"])

    c.text(20, 360, "RESULTING AUDIO", 11, fill=t["mutedFg"], weight=500,
           spacing="0.1em")
    c.ribbon(20, 368, 840, [
        (6, "gone", "cut"), (18, "show", "Show"), (5, "beep", "beep"),
        (16, "show", "Show"), (7, "keep", "kept intro"), (24, "show", "Show"),
        (6, "gone", "cut")], h=24)
    return c.render()


# ====================================================================
# Diagram 5: processing modes
# ====================================================================
def d_modes(t):
    modes = [
        ("Standard", "Find the ads, cut them out", None,
         [(7, "gone", ""), (26, "show", "Show"), (11, "gone", ""),
          (34, "show", "Show"), (8, "gone", "")]),
        ("Keep content only", "Mark the show, remove everything else",
         "EXPERIMENTAL",
         [(7, "gone", ""), (26, "show", "Marked"), (11, "gone", ""),
          (34, "show", "Marked"), (8, "gone", "")]),
        ("Cue-only", "Cut on cue pairs and known patterns, no model call",
         "EXPERIMENTAL",
         [(12, "show", "Show"), (13, "gone", "cue to cue"), (44, "show", "Show"),
          (12, "gone", "cue to cue"), (15, "show", "Show")]),
        ("Skip ad detection", "Transcript and chapters only, nothing cut", None,
         [(100, "show", "Full episode, transcript and chapters added")]),
        ("Pass-through", "Relay the file exactly as published", None,
         [(100, "show", "Full episode, untouched")]),
    ]
    row = 68
    c = Canvas(t, 880, 30 + len(modes) * (row + 12), "The five per-feed processing modes")
    y = 20
    for name, desc, flag, segs in modes:
        c.card(20, y, 840, row)
        c.text(40, y + 28, name, 15, weight=500)
        c.text(40, y + 48, desc, 12, fill=t["mutedFg"])
        if flag:
            c.badge(40 + len(name) * 8.6 + 8, y + 15, flag, t["warning"])
        c.ribbon(430, y + 20, 410, segs, h=28)
        y += row + 12
    return c.render()


# ====================================================================
# Diagram 6: re-run entry points
# ====================================================================
def d_rerun(t):
    """Which stages each re-run mode actually pays for."""
    steps = ["Download", "Transcribe", "Detect", "Validate", "Cut", "Publish"]
    # state per step: reuse | run | skip
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
    c = Canvas(t, 880, 66 + len(modes) * (row + gap) + 30,
               "Where each re-run mode enters the pipeline")

    # legend header aligned to the strip
    sx, sw_, sg = 372, 76, 6
    c.text(20, 32, "RE-RUN MODE", 11, fill=t["mutedFg"], weight=500, spacing="0.1em")
    for i, s in enumerate(steps):
        c.text(sx + i * (sw_ + sg) + sw_ / 2, 32, s.upper(), 10,
               fill=t["mutedFg"], weight=500, anchor="middle", spacing="0.06em")

    y = 46
    for name, desc, states, flag in modes:
        c.card(20, y, 840, row)
        c.text(44, y + 28, name, 15, weight=500)
        c.text(44, y + 48, desc, 12, fill=t["mutedFg"])
        if flag:
            c.badge(44, y + 58, flag, t["warning"])
        for i, st in enumerate(states):
            x = sx + i * (sw_ + sg)
            if st == "run":
                c.card(x, y + 29, sw_, 28, fill=tint(t["primary"], t["tintBase"]),
                       stroke=t["primary"], r=4)
                c.text(x + sw_ / 2, y + 48, "runs", 11, fill=t["primary"],
                       weight=500, anchor="middle")
            elif st == "reuse":
                c.card(x, y + 29, sw_, 28, fill=t["bg"], stroke=t["border"], r=4)
                c.text(x + sw_ / 2, y + 48, "reused", 11, fill=t["mutedFg"],
                       anchor="middle")
            else:
                c.card(x, y + 29, sw_, 28, fill="none", stroke=t["border"], r=4,
                       dash="3 3")
                c.text(x + sw_ / 2, y + 48, "skipped", 11, fill=t["mutedFg"],
                       anchor="middle", opacity="0.7")
        y += row + gap

    c.text(20, y + 18, "MinusPod keeps each episode's original audio by default, "
                       "so a correction is a re-cut rather than a full re-run.", 12,
           fill=t["mutedFg"])
    return c.render()


# ====================================================================
# Diagram 7: learning loop
# ====================================================================
def d_learn(t):
    c = Canvas(t, 880, 250, "How detection gets cheaper over time")
    steps = [("Ad detected and cut", "by any detection source", None),
             ("Pattern stored", "no person in the loop", None),
             ("Corrections refine it", "confirm, adjust, or reject", None),
             ("Matched free", "next time, before the model", t["success"])]
    bw = 196
    for i, (a, b, col) in enumerate(steps):
        x = 20 + i * (bw + 12)
        c.card(x, 30, bw, 66,
               fill=tint(col, t["tintBase"], 0.12) if col else t["card"],
               stroke=col or t["border"])
        c.text(x + 16, 58, a, 14, weight=500, fill=col or t["cardFg"])
        c.text(x + 16, 78, b, 12, fill=t["mutedFg"])
        if i < 3:
            c.arrow(x + bw, 63, x + bw + 10, 63)
    c.o.append(
        f'<path d="M 860 96 C 860 130, 860 130, 440 130 C 20 130, 20 130, 20 96" '
        f'stroke="{t["mutedFg"]}" stroke-width="1.5" fill="none" '
        f'stroke-dasharray="4 4" marker-end="url(#ar)"/>')

    c.text(20, 172, "PATTERNS WIDEN AS THEY PROVE OUT", 11, fill=t["mutedFg"],
           weight=500, spacing="0.1em")
    scopes = [("This show", "where it was first seen"),
              ("This network", "same publisher, other shows"),
              ("Everywhere", "national sponsors")]
    for i, (a, b) in enumerate(scopes):
        x = 20 + i * 288
        w = 264
        c.card(x, 186, w, 52, fill=tint(t["primary"], t["tintBase"], 0.10 + i * 0.05),
               stroke=t["primary"])
        c.text(x + 16, 210, a, 14, weight=500, fill=t["primary"])
        c.text(x + 16, 228, b, 12, fill=t["mutedFg"])
        if i < 2:
            c.arrow(x + w, 212, x + w + 22, 212)
    return c.render()


# ====================================================================
# Diagram 8: failure handling
# ====================================================================
def d_fail(t):
    c = Canvas(t, 880, 200, "What happens when a stage fails")
    rows = [("Model call fails",
             "Publishes any free-evidence cuts, flags the episode, queues one retry"),
            ("Endpoint offline",
             "Opt-in offline queue parks it and re-queues when the endpoint returns"),
            ("A cut looks wrong",
             "It was held, not cut. Approving re-cuts from the original")]
    y = 20
    for a, b in rows:
        c.card(20, y, 250, 52, fill=tint(t["warning"], t["tintBase"], 0.12),
               stroke=t["warning"])
        c.text(40, y + 32, a, 14, weight=500, fill=t["warning"])
        c.arrow(270, y + 26, 306, y + 26)
        c.card(306, y, 554, 52)
        c.text(326, y + 32, b, 13, fill=t["cardFg"])
        y += 60
    return c.render()


DIAGRAMS = [
    ("wf-overview", d_hero),
    ("wf-arrival", d_arrive),
    ("wf-pipeline", d_pipeline),
    ("wf-detection", d_gate),
    ("wf-modes", d_modes),
    ("wf-rerun", d_rerun),
    ("wf-learning", d_learn),
    ("wf-failure", d_fail),
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

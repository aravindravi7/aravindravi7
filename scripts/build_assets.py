#!/usr/bin/env python3
"""Render the profile README's SVGs from data/career.yml.

    pip install pyyaml fonttools brotli
    python scripts/build_assets.py

Writes assets/header.svg, assets/nutshell.svg, assets/posters/<id>.svg and
assets/tickets/<id>.svg. Fonts (OFL, licenses in assets/fonts/) are fetched once
from a pinned google/fonts commit into scripts/.fonts/, then subset to the glyphs
each SVG uses and embedded as base64 WOFF2, so the SVGs make no external requests.

After changing an asset, bump its ?v= query in README.md: GitHub caches images.
"""

import base64
import html
import io
import math
import urllib.request
from collections import defaultdict
from pathlib import Path

import yaml
from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
FONT_CACHE = ROOT / "scripts" / ".fonts"
GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/47d54ca1ebb14fd864695f1d39dc734f39017c33/ofl/"

# key: (google/fonts path, CSS family, fallback stack)
FONTS = {
    "display": ("anton/Anton-Regular.ttf", "AR Display", "Impact,'Arial Narrow Bold',sans-serif"),
    "sans": ("barlowcondensed/BarlowCondensed-Medium.ttf", "AR Sans", "'Arial Narrow',Arial,sans-serif"),
    "sansb": ("barlowcondensed/BarlowCondensed-SemiBold.ttf", "AR Sans Bold", "'Arial Narrow',Arial,sans-serif"),
    "serif": ("instrumentserif/InstrumentSerif-Italic.ttf", "AR Serif", "Georgia,serif"),
}
FALLBACK = ["display", "sans", "serif"]  # for glyphs a face lacks (Barlow has no →)

INK = "#0B0F1A"
SURFACE = "#131926"
HAIRLINE = "rgba(255,255,255,0.08)"
TITLE = "#F4F4F4"
BODY = "#EAEAEA"
MUTED = "rgba(255,255,255,0.55)"

# Genre hues, tuned in OKLCH to lightness 0.74–0.85 and chroma ~0.14 so they
# read as one family. All pass WCAG AA as text on INK (lowest: healthtech, 7.7:1).
HUES = {
    "energy": "#2BEDCE",
    "ai": "#77B2FF",
    "nonprofit": "#5ED99E",
    "web3": "#C19FFF",
    "fintech": "#FFB74E",
    "edtech": "#FC96BF",
    "healthtech": "#FF7E76",
}

REDUCED_MOTION = "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"


# ---------------------------------------------------------------- fonts


class Face:
    def __init__(self, key):
        rel, self.family, self.fallback = FONTS[key]
        self.path = FONT_CACHE / Path(rel).name
        if not self.path.exists():
            FONT_CACHE.mkdir(parents=True, exist_ok=True)
            urllib.request.urlretrieve(GOOGLE_FONTS + rel, self.path)
        font = TTFont(self.path)
        self.cmap = font.getBestCmap()
        self.hmtx = font["hmtx"]
        self.upm = font["head"].unitsPerEm

    def has(self, ch):
        return ord(ch) in self.cmap

    def advance(self, ch):
        return self.hmtx[self.cmap[ord(ch)]][0] / self.upm


FACES = {key: Face(key) for key in FONTS}


def segments(s, key):
    """Split s into runs of (font key, text), falling back for missing glyphs."""
    runs = []
    for ch in s:
        k = key if FACES[key].has(ch) else next(f for f in FALLBACK if FACES[f].has(ch))
        if runs and runs[-1][0] == k:
            runs[-1][1] += ch
        else:
            runs.append([k, ch])
    return runs


def width(s, key, size, track=0.0):
    """Advance width in px, ignoring kerning (which only tightens), plus tracking in em."""
    em = sum(FACES[k].advance(ch) for k, run in segments(s, key) for ch in run)
    return (em + track * len(s)) * size


def fit(s, key, max_size, max_width, track=0.0):
    return min(max_size, max_width / width(s, key, 1, track))


def wrap(s, key, size, max_width):
    lines, line = [], ""
    for word in s.split():
        trial = f"{line} {word}".strip()
        if line and width(trial, key, size) > max_width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def balanced(s, key, size, max_width):
    """Wrap into the fewest lines, then even them out so no word is left alone."""
    lines = wrap(s, key, size, max_width)
    if len(lines) != 2:
        return lines
    words = s.split()
    splits = [(" ".join(words[:i]), " ".join(words[i:])) for i in range(1, len(words))]
    ok = [p for p in splits if max(width(x, key, size) for x in p) <= max_width]
    return list(min(ok, key=lambda p: abs(width(p[0], key, size) - width(p[1], key, size))))


def woff2_b64(key, chars):
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["kern"]
    opts.hinting = False
    opts.desubroutinize = True
    opts.name_IDs = [0, 1, 2]
    font = TTFont(FACES[key].path)
    sub = subset.Subsetter(opts)
    sub.populate(text="".join(sorted(chars)) + " ")
    sub.subset(font)
    buf = io.BytesIO()
    font.flavor = "woff2"
    font.save(buf)
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------- svg document


class Svg:
    def __init__(self, w, h, title, desc):
        self.w, self.h, self.title, self.desc = w, h, title, desc
        self.used = defaultdict(set)
        self.defs, self.body, self.css = [], [], []

    def add(self, *parts):
        self.body.extend(parts)

    def text(self, x, y, s, key, size, fill, anchor="start", track=0.0, attrs="", cls=""):
        """Emit a <text>, compensating the trailing letter-spacing so centering stays true."""
        if anchor == "middle":
            x += track * size / 2
        elif anchor == "end":
            x += track * size
        runs = segments(s, key)
        for k, run in runs:
            self.used[k].update(run)
        inner = "".join(
            html.escape(run) if k == key else f'<tspan class="f-{k}">{html.escape(run)}</tspan>'
            for k, run in runs
        )
        ls = f' letter-spacing="{track * size:.2f}"' if track else ""
        anc = f' text-anchor="{anchor}"' if anchor != "start" else ""
        return (f'<text x="{x:.1f}" y="{y:.1f}" class="{f"f-{key} {cls}".strip()}" font-size="{size:.2f}" '
                f'fill="{fill}"{anc}{ls}{attrs}>{inner}</text>')

    def grain(self, opacity=0.05):
        self.defs.append(
            '<filter id="grain" x="0" y="0" width="100%" height="100%">'
            '<feTurbulence type="fractalNoise" baseFrequency=".85" numOctaves="2" stitchTiles="stitch"/>'
            '<feColorMatrix type="saturate" values="0"/></filter>')
        return f'<rect width="{self.w}" height="{self.h}" filter="url(#grain)" opacity="{opacity}"/>'

    def render(self):
        faces = "".join(
            f"@font-face{{font-family:'{FACES[k].family}';"
            f"src:url(data:font/woff2;base64,{woff2_b64(k, chars)}) format('woff2')}}"
            f".f-{k}{{font-family:'{FACES[k].family}',{FACES[k].fallback}}}"
            for k, chars in sorted(self.used.items()))
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
            f'viewBox="0 0 {self.w} {self.h}" role="img" aria-labelledby="t d">\n'
            f'<title id="t">{html.escape(self.title)}</title>\n'
            f'<desc id="d">{html.escape(self.desc)}</desc>\n'
            f'<style>{faces}{"".join(self.css)}</style>\n'
            f'<defs>{"".join(self.defs)}</defs>\n'
            + "\n".join(self.body) + "\n</svg>\n")


def glow(id_, hue, strength):
    return (f'<radialGradient id="{id_}"><stop offset="0" stop-color="{hue}" stop-opacity="{strength}"/>'
            f'<stop offset="1" stop-color="{hue}" stop-opacity="0"/></radialGradient>')


# ---------------------------------------------------------------- poster motifs
# Each draws inside the motif zone (x 30–270, y 84–254), centred on (150, 169).


def battery_rack(h):
    levels = [0.88, 0.56, 0.76, 0.42]
    out = []
    for i, lv in enumerate(levels):
        x = 46 + i * 56
        out.append(f'<rect x="{x + 13}" y="101" width="14" height="7" rx="2" fill="{h}" opacity=".85"/>')
        out.append(f'<rect x="{x}" y="108" width="40" height="122" rx="6" fill="{INK}" fill-opacity=".6" stroke="{h}" stroke-width="1.8"/>')
        fh = 110 * lv
        out.append(f'<rect x="{x + 6}" y="{224 - fh:.1f}" width="28" height="{fh:.1f}" rx="3" fill="{h}" opacity="{0.55 + 0.45 * lv:.2f}"/>')
        out.append(f'<path d="M{x + 6} 152 h28 M{x + 6} 188 h28" stroke="{INK}" stroke-opacity=".35" stroke-width="1"/>')
    out.append(f'<path d="M38 240 H262" stroke="{TITLE}" stroke-opacity=".35" stroke-width="1.5"/>')
    return out


def agent_nodes(h):
    cx, cy = 150, 166
    pts = [(cx + 92 * math.cos(math.radians(a)), cy + 70 * math.sin(math.radians(a)))
           for a in (-90, -38, 14, 64, 116, 166, 218)]
    out = [f'<path d="M{" L".join(f"{x:.1f} {y:.1f}" for x, y in pts)} Z" fill="none" stroke="{TITLE}" stroke-opacity=".18" stroke-width="1"/>']
    out += [f'<path d="M{cx} {cy} L{x:.1f} {y:.1f}" stroke="{h}" stroke-opacity=".7" stroke-width="1.5"/>' for x, y in pts]
    out.append(f'<circle cx="{cx}" cy="{cy}" r="30" fill="none" stroke="{h}" stroke-opacity=".35"/>')
    out.append(f'<circle cx="{cx}" cy="{cy}" r="18" fill="{h}"/>')
    out += [f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8.5" fill="{INK}" stroke="{TITLE}" stroke-width="1.8"/>' for x, y in pts]
    return out


def chat_fields(h):
    out = []
    lift = 'transform="translate(0 -8)"'
    # field rows converging on a vanishing point below the bubbles
    vx, vy, top, bot = 150, 150, 206, 248
    for i in range(9):
        bx = 22 + i * 32
        t = (top - vy) / (bot - vy)
        out.append(f'<path d="M{vx + (bx - vx) * t:.1f} {top} L{bx} {bot}" stroke="{h}" stroke-opacity=".55" stroke-width="1.4"/>')
    out.append(f'<path d="M40 {top} H260" stroke="{h}" stroke-opacity=".8" stroke-width="1.4"/>')
    for y in (218, 232):
        out.append(f'<path d="M30 {y} H270" stroke="{h}" stroke-opacity=".25" stroke-width="1"/>')
    # incoming message: outlined, white
    out.append(f'<g {lift}>')
    out.append(f'<path d="M68 98 h104 a10 10 0 0 1 10 10 v26 a10 10 0 0 1 -10 10 h-90 l-14 12 v-12 a10 10 0 0 1 -10 -10 v-26 a10 10 0 0 1 10 -10 Z" fill="{INK}" stroke="{TITLE}" stroke-width="1.8"/>')
    out.append(f'<rect x="72" y="111" width="80" height="5" rx="2.5" fill="{TITLE}" opacity=".75"/>')
    out.append(f'<rect x="72" y="125" width="56" height="5" rx="2.5" fill="{TITLE}" opacity=".45"/>')
    # reply: filled with the genre hue
    out.append(f'<path d="M128 150 h104 a10 10 0 0 1 10 10 v24 a10 10 0 0 1 -10 10 v12 l-14 -12 h-90 a10 10 0 0 1 -10 -10 v-24 a10 10 0 0 1 10 -10 Z" fill="{h}"/>')
    out.append(f'<rect x="138" y="162" width="84" height="5" rx="2.5" fill="{INK}" opacity=".7"/>')
    out.append(f'<rect x="138" y="176" width="52" height="5" rx="2.5" fill="{INK}" opacity=".45"/>')
    out.append('</g>')
    return out


def hex_cluster(h):
    r, cx, cy = 30, 150, 169
    w = math.sqrt(3) * r

    def hexagon(x, y, **kw):
        pts = " ".join(f"{x + r * math.cos(math.radians(a)):.1f},{y + r * math.sin(math.radians(a)):.1f}"
                       for a in range(-90, 270, 60))
        attrs = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in kw.items())
        return f'<polygon points="{pts}" {attrs}/>'

    ring = [(cx + w, cy), (cx - w, cy), (cx + w / 2, cy - 1.5 * r), (cx - w / 2, cy - 1.5 * r),
            (cx + w / 2, cy + 1.5 * r), (cx - w / 2, cy + 1.5 * r)]
    out = [hexagon(x, y, fill=INK, fill_opacity=".5", stroke=h, stroke_width="1.8", stroke_opacity=".8") for x, y in ring]
    out.append(hexagon(cx, cy, fill=h, stroke=TITLE, stroke_width="1.8"))
    return out


def rising_bars(h):
    out = []
    for i in range(10):
        bh = 12 * 10 ** (i / 9)  # first to last is exactly 10x
        x = 50 + i * 19
        out.append(f'<rect x="{x}" y="{240 - bh:.1f}" width="13" height="{bh:.1f}" rx="2" fill="{h}" opacity="{0.3 + 0.7 * i / 9:.2f}"/>')
    out.append(f'<path d="M42 240.5 H262" stroke="{TITLE}" stroke-opacity=".35" stroke-width="1.5"/>')
    return out


def lms_window(h):
    out = [f'<rect x="56" y="98" width="188" height="140" rx="9" fill="{INK}" stroke="{TITLE}" stroke-width="1.8"/>',
           f'<path d="M56 120 H244" stroke="{TITLE}" stroke-opacity=".5" stroke-width="1.2"/>']
    out += [f'<circle cx="{70 + i * 11}" cy="109" r="3" fill="{TITLE}" opacity="{0.8 - i * 0.2:.1f}"/>' for i in range(3)]
    out.append(f'<rect x="57" y="121" width="42" height="116" fill="{h}" opacity=".16"/>')
    out += [f'<rect x="66" y="{134 + i * 16}" width="{24 - (i % 2) * 6}" height="4" rx="2" fill="{h}" opacity="{0.9 if i == 1 else 0.5}"/>' for i in range(5)]
    out.append(f'<rect x="110" y="132" width="122" height="54" rx="5" fill="{h}"/>')
    out.append(f'<path d="M165 149 L165 169 L181 159 Z" fill="{INK}"/>')
    out.append(f'<rect x="110" y="198" width="122" height="5" rx="2.5" fill="{TITLE}" opacity=".2"/>')
    out.append(f'<rect x="110" y="198" width="84" height="5" rx="2.5" fill="{h}"/>')
    out.append(f'<rect x="110" y="214" width="96" height="4" rx="2" fill="{TITLE}" opacity=".45"/>')
    out.append(f'<rect x="110" y="225" width="64" height="4" rx="2" fill="{TITLE}" opacity=".3"/>')
    return out


def ecg_line(h):
    out = [f'<path d="M{x} 96 V244" stroke="{TITLE}" stroke-opacity=".07"/>' for x in range(40, 270, 22)]
    out += [f'<path d="M30 {y} H270" stroke="{TITLE}" stroke-opacity=".07"/>' for y in range(100, 246, 22)]
    trace = ("M30 178 H88 C94 178 96 168 102 168 C108 168 110 178 116 178 H126 L134 190 L148 100 "
             "L162 236 L172 178 H190 C198 178 202 160 212 160 C222 160 226 178 234 178 H270")
    out.append(f'<path d="{trace}" fill="none" stroke="{h}" stroke-opacity=".3" stroke-width="9" stroke-linejoin="round" stroke-linecap="round"/>')
    out.append(f'<path d="{trace}" fill="none" stroke="{h}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>')
    out.append(f'<circle cx="270" cy="178" r="4.5" fill="{TITLE}"/>')
    return out


def transmission_tower(h):
    s = f'stroke="{TITLE}" stroke-width="1.7" fill="none" stroke-linejoin="round"'
    out = [
        # sagging lines running off the edges
        f'<path d="M30 140 Q63 156 96 136 M204 136 Q237 156 270 140 M30 168 Q69 182 108 166 M192 166 Q231 182 270 168" fill="none" stroke="{h}" stroke-opacity=".55" stroke-width="1.3"/>',
        f'<path d="M150 92 L138 128 L120 240 M150 92 L162 128 L180 240" {s}/>',
        f'<path d="M96 128 H204 M108 158 H192 M96 128 L138 118 M204 128 L162 118" {s}/>',
        f'<path d="M138 128 L157 158 M162 128 L143 158 M135 158 L170 196 M165 158 L130 196 M129 196 L178 240 M171 196 L122 240 M130 196 H170" stroke="{TITLE}" stroke-opacity=".6" stroke-width="1.2" fill="none"/>',
        f'<path d="M96 128 v8 M204 128 v8 M108 158 v8 M192 158 v8" stroke="{h}" stroke-width="2.4" stroke-linecap="round"/>',
        f'<path d="M106 240.5 H194" stroke="{TITLE}" stroke-opacity=".35" stroke-width="1.5"/>',
        # the bolt
        f'<path d="M236 82 L218 112 H230 L221 138 L247 103 H234 L243 82 Z" fill="{h}" stroke="{h}" stroke-width="1" stroke-linejoin="round"/>',
    ]
    return out


MOTIFS = {f.__name__: f for f in (battery_rack, agent_nodes, chat_fields, hex_cluster,
                                  rising_bars, lms_window, ecg_line, transmission_tower)}


# ---------------------------------------------------------------- assets


def poster(r):
    hue = HUES[r["hue"]]
    hero = r["hero"]
    where = f'{r["location"]}, ' if r.get("location") else ""
    svg = Svg(300, 450, f'{r["company"]} poster',
              f'{r["genre"].capitalize()}. {r["tagline"]} {hero["value"]} {hero["label"]}. '
              f'{r["title"]}, {where}{r["dates"]}, {r["runtime_months"]} months.')
    svg.defs.append('<clipPath id="card"><rect width="300" height="450" rx="10"/></clipPath>')
    svg.defs.append(glow("glow", hue, 0.26))
    svg.defs.append('<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">'
                    f'<stop offset=".55" stop-color="{INK}" stop-opacity="0"/>'
                    f'<stop offset="1" stop-color="{INK}" stop-opacity=".9"/></linearGradient>')
    grain = svg.grain()
    svg.add('<g clip-path="url(#card)">',
            f'<rect width="300" height="450" fill="{INK}"/>',
            '<ellipse cx="150" cy="172" rx="170" ry="150" fill="url(#glow)"/>',
            '<rect width="300" height="450" fill="url(#fade)"/>')
    svg.add(*MOTIFS[r["motif"]](hue))
    if r["motif"] == "rising_bars":
        svg.add(svg.text(253, 112, "10x", "display", 30, hue, anchor="end"))

    svg.add(svg.text(150, 40, r["genre"], "sansb", min(11.5, fit(r["genre"], "sansb", 11.5, 250, 0.28)),
                     hue, anchor="middle", track=0.28))
    svg.add(svg.text(150, 65, r["tagline"], "serif", fit(r["tagline"], "serif", 18, 250),
                     BODY, anchor="middle"))
    tsize = fit(r["poster_title"], "display", 50, 256, 0.02)
    cap = 1760 / 2048 * tsize  # Anton cap height
    svg.add(svg.text(150, 283 + cap / 2, r["poster_title"], "display", tsize, TITLE, anchor="middle", track=0.02))
    svg.add(svg.text(150, 354, hero["value"], "display", fit(hero["value"], "display", 32, 240, 0.01),
                     hue, anchor="middle", track=0.01))
    svg.add(svg.text(150, 375, hero["label"], "sans", fit(hero["label"], "sans", 14, 240),
                     BODY, anchor="middle", attrs=' opacity=".82"'))
    svg.add(f'<path d="M54 392.5 H246" stroke="{HAIRLINE}"/>')
    bsize = min(fit(line, "sans", 8.4, 252, 0.1) for line in r["billing"])
    for i, line in enumerate(r["billing"]):
        svg.add(svg.text(150, 409 + i * 11.5, line, "sans", bsize, MUTED, anchor="middle", track=0.1))
    svg.add(grain, '</g>',
            f'<rect x="10.5" y="10.5" width="279" height="429" rx="6" fill="none" stroke="{HAIRLINE}"/>',
            f'<rect x=".5" y=".5" width="299" height="449" rx="10" fill="none" stroke="{HAIRLINE}"/>')
    return svg.render()


def header(p):
    hue = HUES["energy"]
    W, H, BAR = 860, 352, 32
    svg = Svg(W, H, f'{p["name"]}: now showing on GitHub',
              f'Opening titles. {p["name"]}, {p["title"]} · {p["descriptor"]} · {p["location"]}. '
              f'{p["tagline"]} {p["status"]} · {p["education"]}.')
    svg.css.append(
        ".bt{animation:bt .6s cubic-bezier(.2,.7,.2,1) both}@keyframes bt{from{transform:translateY(-32px)}}"
        ".bb{animation:bb .6s cubic-bezier(.2,.7,.2,1) both}@keyframes bb{from{transform:translateY(32px)}}"
        ".flk{animation:flk .7s steps(1) .35s both}"
        "@keyframes flk{0%{opacity:0}15%{opacity:.7}30%{opacity:.15}45%{opacity:1}60%{opacity:.4}75%{opacity:.9}}"
        ".fade{animation:fade .5s ease-out both}@keyframes fade{from{opacity:0}}"
        ".rise{animation:rise .8s cubic-bezier(.2,.7,.2,1) .9s both}"
        "@keyframes rise{from{opacity:0;transform:translateY(16px)}}"
        ".dot{animation:fade .3s ease-out 2.4s both,pulse 2.4s ease-in-out 3s infinite}"
        "@keyframes pulse{50%{opacity:.25}}" + REDUCED_MOTION)
    svg.defs.append(f'<clipPath id="card"><rect width="{W}" height="{H}" rx="14"/></clipPath>')
    svg.defs.append(glow("glow", hue, 0.2))
    grain = svg.grain()
    svg.add('<g clip-path="url(#card)">', f'<rect width="{W}" height="{H}" fill="{INK}"/>',
            f'<g class="flk"><ellipse cx="430" cy="150" rx="420" ry="150" fill="url(#glow)"/></g>')

    svg.add(svg.text(430, 76, "NOW SHOWING ON GITHUB", "sansb", 13, hue, anchor="middle", track=0.42,
                     cls="fade", attrs=' style="animation-delay:.7s"'))
    svg.add(f'<g class="rise">{svg.text(430, 178, p["name"].upper(), "display", 96, TITLE, anchor="middle", track=0.03)}</g>')
    role = f'{p["title"]} · {p["descriptor"]} · {p["location"]}'
    svg.add(f'<g class="fade" style="animation-delay:1.6s">{svg.text(430, 216, role, "sans", 21, BODY, anchor="middle", track=0.02)}</g>')
    svg.add(f'<g class="fade" style="animation-delay:2s">{svg.text(430, 252, p["tagline"], "serif", 24, BODY, anchor="middle", attrs=" opacity=\".8\"")}</g>')

    chip = f'{p["status"]} · {p["education"]}'
    cw = width(chip, "sansb", 14.5, 0.02) + 50
    x0 = 430 - cw / 2
    svg.add(f'<g class="fade" style="animation-delay:2.4s">',
            f'<rect x="{x0:.1f}" y="272" width="{cw:.1f}" height="28" rx="14" fill="{hue}" fill-opacity=".07" stroke="{hue}" stroke-opacity=".35"/>',
            # marquee bulbs: a dotted ring just outside the chip
            f'<rect x="{x0 - 5:.1f}" y="267" width="{cw + 10:.1f}" height="38" rx="19" fill="none" stroke="{hue}" stroke-opacity=".55" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="0 7"/>',
            svg.text(x0 + 34, 291, chip, "sansb", 14.5, BODY, track=0.02), '</g>',
            f'<circle cx="{x0 + 19:.1f}" cy="286" r="4.5" fill="{hue}" class="dot"/>')

    svg.add(f'<g class="bt"><rect width="{W}" height="{BAR}" fill="#04060B"/><rect y="{BAR}" width="{W}" height="1" fill="{HAIRLINE}"/></g>',
            f'<g class="bb"><rect y="{H - BAR}" width="{W}" height="{BAR}" fill="#04060B"/><rect y="{H - BAR - 1}" width="{W}" height="1" fill="{HAIRLINE}"/></g>',
            grain, '</g>',
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="none" stroke="{HAIRLINE}"/>')
    return svg.render()


def nutshell(items):
    W, PAD, COLS, ROW = 860, 26, 3, 128
    rows = math.ceil(len(items) / COLS)
    H = PAD * 2 + ROW * rows - 12
    cw = (W - PAD * 2) / COLS
    svg = Svg(W, H, "Box office: one headline number per role",
              " ".join(f'{i["value"]} {i["label"]}, {i["company"]}.' for i in items))
    svg.defs.append(f'<clipPath id="card"><rect width="{W}" height="{H}" rx="14"/></clipPath>')
    grain = svg.grain()
    svg.add('<g clip-path="url(#card)">', f'<rect width="{W}" height="{H}" fill="{INK}"/>')
    for n, it in enumerate(items):
        hue = HUES[it["hue"]]
        col, row = n % COLS, n // COLS
        x, y = PAD + col * cw + 22, PAD + row * ROW
        svg.defs.append(glow(f"g{n}", hue, 0.13))
        svg.add(f'<ellipse cx="{x + 60:.1f}" cy="{y + 44}" rx="130" ry="70" fill="url(#g{n})"/>')
        svg.add(f'<rect x="{x - 22:.1f}" y="{y + 14}" width="3" height="56" rx="1.5" fill="{hue}"/>')
        svg.add(svg.text(x, y + 62, it["value"], "display", fit(it["value"], "display", 56, cw - 44, 0.01), hue, track=0.01))
        svg.add(svg.text(x, y + 88, it["label"], "sans", 18, BODY))
        svg.add(svg.text(x, y + 107, it["company"], "sansb", 14.5, hue, attrs=' opacity=".85"'))
    for col in range(1, COLS):
        svg.add(f'<path d="M{PAD + col * cw:.1f} {PAD + 6} V{H - PAD - 6}" stroke="{HAIRLINE}"/>')
    for row in range(1, rows):
        svg.add(f'<path d="M{PAD} {PAD + row * ROW - 9} H{W - PAD}" stroke="{HAIRLINE}"/>')
    svg.add(grain, '</g>', f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="14" fill="none" stroke="{HAIRLINE}"/>')
    return svg.render()


def ticket(b):
    hue = HUES[b["hue"]]
    W, H, STUB = 420, 200, 328
    svg = Svg(W, H, f'{b["name"]}: now showing',
              f'Admit one. {b["name"]}: {b["description"]} {" · ".join(b["facts"])}. '
              f'{b["stack"]}. Showing: live on Streamlit. Watch live.')
    svg.css.append(".pop{transform-box:fill-box;transform-origin:center;"
                   "animation:pop .6s cubic-bezier(.3,1.5,.5,1) .5s both}"
                   "@keyframes pop{from{opacity:0;transform:scale(.6)}}" + REDUCED_MOTION)
    svg.defs.append(f'<mask id="notch"><rect width="{W}" height="{H}" rx="12" fill="#fff"/>'
                    f'<circle cx="{STUB}" cy="0" r="11" fill="#000"/><circle cx="{STUB}" cy="{H}" r="11" fill="#000"/></mask>')
    svg.defs.append(glow("glow", hue, 0.16))
    grain = svg.grain()
    svg.add('<g mask="url(#notch)">', f'<rect width="{W}" height="{H}" fill="{INK}"/>',
            '<ellipse cx="90" cy="40" rx="220" ry="140" fill="url(#glow)"/>',
            f'<rect x="{STUB}" width="{W - STUB}" height="{H}" fill="{hue}" fill-opacity=".09"/>',
            f'<rect x=".5" y=".5" width="{W - 1}" height="{H - 1}" rx="12" fill="none" stroke="rgba(255,255,255,0.12)"/>',
            f'<circle cx="{STUB}" cy="0" r="11.5" fill="none" stroke="rgba(255,255,255,0.12)"/>',
            f'<circle cx="{STUB}" cy="{H}" r="11.5" fill="none" stroke="rgba(255,255,255,0.12)"/>',
            f'<path d="M{STUB} 18 V{H - 18}" stroke="rgba(255,255,255,0.28)" stroke-width="1.5" stroke-dasharray="1 5" stroke-linecap="round"/>')

    x = 26
    svg.add(f'<circle cx="{x + 3.5}" cy="31.5" r="3.5" fill="{hue}"/>',
            svg.text(x + 13, 36, "Showing: live on Streamlit", "sansb", 13, hue, track=0.02))
    svg.add(svg.text(x, 76, b["name"], "display", fit(b["name"], "display", 32, 280, 0.02), TITLE, track=0.02))
    dsize = 15.5
    lines = balanced(b["description"], "serif", dsize, 284)
    for i, line in enumerate(lines):
        svg.add(svg.text(x, 99 + i * 17, line, "serif", dsize, BODY, attrs=' opacity=".85"'))
    # fact chips, wrapped in order
    cy, cx, csize, gap = 99 + (len(lines) - 1) * 17 + 13, x, 12.5, 6
    for fact in b["facts"]:
        fw = width(fact, "sans", csize) + 16
        if cx + fw > STUB - 18:
            cx, cy = x, cy + 21 + gap
        svg.add(f'<rect x="{cx}" y="{cy}" width="{fw:.1f}" height="21" rx="5" fill="{SURFACE}" stroke="rgba(255,255,255,0.1)"/>',
                svg.text(cx + 8, cy + 14.8, fact, "sans", csize, "#D6DBE4"))
        cx += fw + gap
    svg.add(svg.text(x, 187, b["stack"], "sans", 12.5, MUTED))

    sx = (STUB + W) / 2
    svg.add(svg.text(sx, 36, "ADMIT ONE", "sansb", 11, hue, anchor="middle", track=0.24),
            f'<path d="M{STUB + 18} 48 H{W - 18}" stroke="{hue}" stroke-opacity=".3"/>',
            f'<g class="pop"><circle cx="{sx}" cy="100" r="23" fill="{hue}" fill-opacity=".14" stroke="{hue}" stroke-width="1.6"/>'
            f'<path d="M{sx - 6} 89 L{sx - 6} 111 L{sx + 11} 100 Z" fill="{hue}"/></g>',
            svg.text(sx, 147, "Watch live", "sansb", 14, BODY, anchor="middle", track=0.02),
            f'<path d="M{STUB + 18} 162 H{W - 18}" stroke="{hue}" stroke-opacity=".3"/>')
    svg.add(grain, '</g>')
    return svg.render()


def main():
    data = yaml.safe_load((ROOT / "data" / "career.yml").read_text())
    out = {"header.svg": header(data["person"]), "nutshell.svg": nutshell(data["nutshell"])}
    for r in data["roles"] + data["early_work"]:
        out[f'posters/{r["id"]}.svg'] = poster(r)
    for b in data["now_showing"]:
        out[f'tickets/{b["id"]}.svg'] = ticket(b)
    for rel, svg in out.items():
        path = ASSETS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(svg)
        print(f"{rel:32} {len(svg.encode()) / 1024:6.1f} KB")


if __name__ == "__main__":
    main()

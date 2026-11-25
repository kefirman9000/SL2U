"""Fireworks show and foregrounds for videos.

The fireworks are drawn into the same transparent layer as the drawn visualizers. The song's beat
grid is worked out first, so rockets can be sent up early and burst exactly on a beat. A show can
follow a timeline: parts with their own effect and amount, blended into each other, a build-up and
a finale. A foreground (a skyline, a crowd, mountains or your own transparent PNG) can sit in front
of everything drawn, lit up by the flashes.
"""
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import art, reactive

EFFECTS = {"mixed": "Mixed show", "peony": "Classic burst", "willow": "Gold willow", "ring": "Rings",
           "crackle": "Crackle", "heart": "Hearts", "xmas": "Christmas shapes", "finale": "Finale",
           "embers": "Rising embers", "lightning": "Lightning", "confetti": "Confetti cannons"}
GROUND = ("lightning", "confetti", "embers")  # set off from the ground, no rockets
MIX_TYPES = ("peony", "willow", "ring", "crackle", "heart", "xmas")
MIX_DEFAULT = {"peony": 40, "willow": 20, "ring": 12, "crackle": 15, "heart": 5, "xmas": 0}
PALETTES = ("multi", "gold", "pink", "ice", "fire", "xmas", "custom")
SHAPES = ("snowflake", "tree", "present", "santa", "cane", "star", "bell", "text")
FOREGROUNDS = {"skyline": "City skyline", "crowd": "Crowd", "mountains": "Mountains and trees"}
RISE = 0.6  # seconds a rocket takes to go up


def _hex(h):
    h = str(h or "").lstrip("#")
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else None
    except ValueError:
        return None


def _hsl(h, s, l):
    s, l = s / 100, l / 100
    a = s * min(l, 1 - l)
    f = lambda n: l - a * max(-1, min((n + h / 30) % 12 - 3, min(9 - (n + h / 30) % 12, 1)))
    return tuple(int(round(255 * f(n))) for n in (0, 8, 4))


def _whiten(c, t):
    return tuple(int(v + (255 - v) * t) for v in c)


def _sec(v, default=0.0):
    """'1:30' or 90 -> seconds."""
    if v is None or v == "":
        return default
    if isinstance(v, (int, float)):
        return max(0.0, float(v))
    s = str(v).strip()
    if ":" in s:
        m, _, x = s.partition(":")
        try:
            return int(m or 0) * 60 + float(x or 0)
        except ValueError:
            return default
    try:
        return max(0.0, float(s))
    except ValueError:
        return default


# ---------------------------------------------------------------- the show plan (what goes off when)

class Plan:
    """The single effect, or the timeline: parts blended near each change, a build-up and the finale."""

    def __init__(self, opts):
        self.effect = opts.get("fw_effect") if opts.get("fw_effect") in EFFECTS else "mixed"
        self.on = bool(opts.get("fw_tl"))
        parts = []
        for p in opts.get("fw_parts") or []:
            if isinstance(p, dict) and p.get("e") in EFFECTS:
                parts.append({"t": _sec(p.get("t")), "e": p["e"], "a": max(0.0, min(4.0, float(p.get("a") or 1)))})
        parts.sort(key=lambda p: p["t"])
        self.blend = max(0.0, min(20.0, float(opts.get("fw_blend", 6) or 0)))
        fin = opts.get("fw_finale")
        c = parts
        if fin not in (None, "", False):
            fin, length = _sec(fin), max(3.0, _sec(opts.get("fw_finale_len"), 20))
            c = [p for p in parts if p["t"] < fin]
            if opts.get("fw_build", True) and c:
                last = c[-1]
                c.append({"t": max(last["t"] + 1, fin - 20), "e": last["e"], "a": last["a"], "build": True})
            c.append({"t": fin, "e": "finale", "a": 1.2, "end": fin + length})
        self.cues = c or [{"t": 0, "e": self.effect, "a": 1}]

    def mix(self, t):
        """[(effect, weight, amount)] at song time t."""
        if not self.on:
            return [(self.effect, 1.0, 1.0)]
        c, bl = self.cues, self.blend
        i = 0
        while i + 1 < len(c) and c[i + 1]["t"] <= t:
            i += 1
        cur = c[i]
        if t < cur["t"] or (cur["e"] == "finale" and t > cur.get("end", 1e9)):
            return []
        a = cur["a"]
        if cur.get("build"):  # more and more towards the finale
            nx = c[i + 1]
            a = cur["a"] + (2.2 - cur["a"]) * min(1.0, (t - cur["t"]) / max(1.0, nx["t"] - cur["t"]))
        out = [[cur["e"], 1.0, a]]
        if bl > 0:
            nx = c[i + 1] if i + 1 < len(c) else None
            pv = c[i - 1] if i > 0 else None
            if nx and not nx.get("build") and nx["t"] - t < bl / 2:
                p = 0.5 - (nx["t"] - t) / bl
                out[0][1] = 1 - p
                out.append([nx["e"], p, nx["a"]])
            elif pv and not cur.get("build") and t - cur["t"] < bl / 2:
                p = 0.5 - (t - cur["t"]) / bl
                out[0][1] = 1 - p
                out.append([pv["e"], p, 2.2 if pv.get("build") else pv["a"]])
        return [tuple(x) for x in out if x[1] > 0.01]

    def weight(self, t, name):
        return sum(w * a for e, w, a in self.mix(t) if e == name)

    def shots(self, s, t, rng):
        out = []
        for e, w, a in self.mix(t):
            n = a * w
            n = int(n) + (1 if rng.random() < n % 1 else 0)
            for _ in range(n):
                if e == "finale":
                    out += [("mixed", 0.5)] * (3 + int(round(3 * s)))
                else:
                    out.append((e, s))
        return out


# ---------------------------------------------------------------- Christmas shapes

def _quad(p0, c, p1, n=14):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * c[0] + t * t * p1[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * c[1] + t * t * p1[1]) for t in np.linspace(0, 1, n)]


def _ell(cx, cy, rx, ry, rot=0.0, n=28):
    cr, sr = math.cos(rot), math.sin(rot)
    return [(cx + rx * math.cos(a) * cr - ry * math.sin(a) * sr, cy + rx * math.cos(a) * sr + ry * math.sin(a) * cr)
            for a in np.linspace(0, 2 * math.pi, n)]


def _star(cx, cy, r, n=5):
    return [(cx + math.cos(i * math.pi / n - math.pi / 2) * (r * 0.45 if i % 2 else r),
             cy + math.sin(i * math.pi / n - math.pi / 2) * (r * 0.45 if i % 2 else r)) for i in range(n * 2)]


def _draw_shape(name):
    wide = name == "text"
    im = Image.new("RGBA", (320 if wide else 120, 100 if wide else 120), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    ln = lambda pts, c, w: d.line(pts, fill=c, width=w, joint="curve")
    if name == "snowflake":
        c = (223, 242, 255, 255)
        for i in range(6):
            a = i * math.pi / 3
            rot = lambda x, y: (60 + x * math.cos(a) - y * math.sin(a), 60 + x * math.sin(a) + y * math.cos(a))
            ln([rot(0, 0), rot(0, -52)], c, 3)
            for dd, l in ((-22, 16), (-38, 12)):
                ln([rot(0, dd), rot(-l, dd - l)], c, 3)
                ln([rot(0, dd), rot(l, dd - l)], c, 3)
    elif name == "tree":
        for y, w in ((14, 26), (34, 40), (56, 54)):
            d.polygon([(60, y), (60 - w, y + 32), (60 + w, y + 32)], fill=(33, 196, 90, 255))
        d.rectangle((52, 88, 68, 108), fill=(154, 90, 42, 255))
        d.polygon(_star(60, 12, 12), fill=(255, 216, 74, 255))
        for x, y, c in ((50, 40, (255, 59, 59)), (72, 52, (74, 176, 255)), (40, 70, (255, 216, 74)), (80, 76, (255, 59, 59)),
                        (60, 66, (255, 122, 217)), (30, 84, (74, 176, 255)), (90, 86, (255, 216, 74))):
            d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=c + (255,))
    elif name == "present":
        d.rectangle((20, 46, 100, 108), fill=(255, 51, 70, 255))
        d.rectangle((14, 34, 106, 50), fill=(255, 90, 106, 255))
        gold = (255, 216, 74, 255)
        d.rectangle((54, 34, 66, 108), fill=gold)
        d.rectangle((20, 72, 100, 82), fill=gold)
        ln(_ell(46, 24, 15, 9, -0.4), gold, 8)
        ln(_ell(74, 24, 15, 9, 0.4), gold, 8)
    elif name == "santa":
        skin, white, red = (255, 180, 138, 255), (255, 255, 255, 255), (255, 45, 58, 255)
        d.arc((36, 38, 84, 86), 189, 351, fill=skin, width=4)
        ln(_quad((35, 58), (34, 108), (60, 114)) + _quad((60, 114), (86, 108), (85, 58))[1:], white, 4)
        ln(_quad((44, 74), (52, 68), (60, 72)) + _quad((60, 72), (68, 68), (76, 74))[1:], white, 4)
        ln([(32, 46), (88, 46)], white, 4)
        d.ellipse((94, 16, 106, 28), outline=white, width=4)
        ln(_quad((34, 42), (52, 2), (96, 20)) + [(86, 42)], red, 4)
        for x in (51, 69):
            d.ellipse((x - 2, 56, x + 2, 60), fill=(90, 184, 255, 255))
        d.ellipse((57, 62, 63, 68), fill=(255, 74, 90, 255))
    elif name == "cane":
        path = [(66, 112), (66, 40)] + [(48 + 18 * math.cos(-a), 40 + 18 * math.sin(-a)) for a in np.linspace(0, math.pi, 20)]
        ln(path, (255, 255, 255, 255), 16)
        segs, acc = [], 0.0  # red stripes every 20 px along the cane
        for (x0, y0), (x1, y1) in zip(path, path[1:]):
            L = math.hypot(x1 - x0, y1 - y0)
            steps = max(1, int(L / 2))
            for k in range(steps):
                a0, a1 = k / steps, (k + 1) / steps
                p0 = (x0 + (x1 - x0) * a0, y0 + (y1 - y0) * a0)
                p1 = (x0 + (x1 - x0) * a1, y0 + (y1 - y0) * a1)
                if (acc % 20) < 9:
                    segs.append((p0, p1))
                acc += L / steps
        for p0, p1 in segs:
            ln([p0, p1], (255, 45, 58, 255), 16)
    elif name == "star":
        d.polygon(_star(60, 62, 52), fill=(255, 224, 102, 255))
    elif name == "bell":
        gold = (255, 204, 51, 255)
        body = (_quad((26, 88), (36, 82), (38, 56)) + _quad((38, 56), (40, 22), (60, 22))[1:] +
                _quad((60, 22), (80, 22), (82, 56))[1:] + _quad((82, 56), (84, 82), (94, 88))[1:] + [(26, 88)])
        ln(body, gold, 4)
        d.ellipse((54, 92, 66, 104), outline=(255, 241, 176, 255), width=4)
        ln(_ell(46, 16, 12, 7, -0.4), (255, 45, 58, 255), 4)
        ln(_ell(74, 16, 12, 7, 0.4), (255, 45, 58, 255), 4)
    elif name == "text":
        try:
            font = ImageFont.truetype(art.find_font("sans"), 40)
        except Exception:
            font = ImageFont.load_default()
        for word, y, c in (("Merry", 22, (255, 59, 74, 255)), ("Christmas", 70, (59, 209, 111, 255))):
            d.text((160, y), word, font=font, fill=c, anchor="mm")
    return im


_SHAPES = {}
LINE_SHAPES = ("santa", "bell", "text", "snowflake")


def shape_points(name, gap_k=1.0):
    """Star positions (x, y in about -1..1) and colours for a Christmas shape: its outline, thinned out."""
    key = (name, round(gap_k, 2))
    if key in _SHAPES:
        return _SHAPES[key]
    im = _draw_shape(name)
    a = np.asarray(im).astype(np.int32)
    h, w = a.shape[:2]
    lit = a[:, :, 3] > 140
    edge = []
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            if not lit[y, x]:
                continue
            keep = name in LINE_SHAPES
            if not keep:
                for dx, dy in ((3, 0), (-3, 0), (0, 3), (0, -3)):
                    xx, yy = x + dx, y + dy
                    if not (0 <= xx < w and 0 <= yy < h) or not lit[yy, xx] or \
                            np.abs(a[y, x, :3] - a[yy, xx, :3]).sum() > 120:
                        keep = True
                        break
            if keep:
                edge.append((x, y, tuple(int(v) for v in a[y, x, :3])))
    rnd = random.Random(5)
    rnd.shuffle(edge)
    gap = (3.3 if name == "text" else 4.6) * gap_k
    pts, grid = [], {}
    for x, y, c in edge:  # keep stars at least `gap` apart
        gx, gy = int(x // gap), int(y // gap)
        if any((x - px) ** 2 + (y - py) ** 2 < gap * gap for i in (-1, 0, 1) for j in (-1, 0, 1)
               for px, py in grid.get((gx + i, gy + j), ())):
            continue
        grid.setdefault((gx, gy), []).append((x, y))
        pts.append(((x - w / 2) / 60, (y - h / 2) / 60, c))
    _SHAPES[key] = pts
    return pts


# ---------------------------------------------------------------- particles

class P:
    __slots__ = ("kind", "x", "y", "vx", "vy", "life", "max", "c", "size", "drag", "grav", "hold", "hdrag",
                 "line", "glow", "flicker", "crackle", "long", "hist", "rot", "vr", "w", "h", "ph")

    def __init__(self, x, y, vx, vy, c, **o):
        self.kind, self.x, self.y, self.vx, self.vy, self.c = "spark", x, y, vx, vy, c
        self.life = self.max = 70.0
        self.size, self.drag, self.grav, self.hold, self.hdrag = 2.2, 0.985, 0.045, 0, 0
        self.line = self.flicker = self.crackle = False
        self.glow, self.long, self.hist = True, 1.0, []
        self.rot = self.vr = self.w = self.h = self.ph = 0.0
        for k, v in o.items():
            setattr(self, k, v)


_SPRITES = {}


def _sprite(r):
    """Soft round light, alpha only."""
    r = max(4, int(r))
    if r in _SPRITES:
        return _SPRITES[r]
    yy, xx = np.mgrid[-r:r, -r:r]
    d = np.sqrt(xx * xx + yy * yy) / r
    _SPRITES[r] = Image.fromarray((np.clip(1 - d, 0, 1) ** 2 * 255).astype(np.uint8), "L")
    return _SPRITES[r]


# ---------------------------------------------------------------- the show

class Show:
    """Fireworks layer. Same interface as drawn.Style (frame(i) -> RGBA image) plus setup()."""

    def __init__(self, w, h, fps, rgb, vertical, data, size=1.0):
        from .drawn import Painter
        self.w, self.h, self.fps, self.rgb, self.data = w, h, fps, rgb, data
        self.cx, self.cy = w / 2, h / 2
        self.u = min(w, h) / 720  # sizes below are for a 720 px tall picture
        self.k = 60.0 / fps       # and speeds per 1/60 s
        self.p = Painter(w, h)
        self.rng = random.Random(11)
        self.parts, self.rockets, self.flashes, self.bolts = [], [], [], []
        self.events = {}
        self.sky_lo = 0.14  # bursts between 14% and 42% from the top (lower in tall videos)
        self.sky_hi = 0.42 if w >= h else 0.5
        self.center = 1.0
        self.light = None
        self.setup({}, None, 0.0, None, len(data["bass"]))

    # -- options and the beat plan
    def setup(self, opts, audio_path, start, dur, n_frames):
        o = opts or {}
        self.opts = o
        self.start = float(start or 0)
        self.n = n_frames
        self.plan = Plan(o)
        self.palette = o.get("fw_palette") if o.get("fw_palette") in PALETTES else "multi"
        self.custom = [c for c in (_hex(x) for x in (o.get("fw_colors") or [])) if c] or [(255, 45, 58)]
        mix = o.get("fw_mix") if isinstance(o.get("fw_mix"), dict) else {}
        self.mix = {k: max(0.0, float(mix.get(k, MIX_DEFAULT[k]) or 0)) for k in MIX_TYPES}
        self.xshape = o.get("fw_shape") if o.get("fw_shape") in SHAPES else "all"
        self.exact = max(0.0, min(1.0, float(o.get("fw_exact", 0.7))))
        self.snow_on = o.get("fw_snow", True) is not False
        self.amount = max(0.4, min(1.6, float(o.get("fw_size") or 1)))
        self.trail = max(0.0, min(1.0, float(o.get("fw_trail", 0.55))))
        every = max(1, min(4, int(o.get("fw_every") or 1)))
        rockets = o.get("fw_rockets", True) is not False
        self.events = {}
        if audio_path is None:
            return
        bpm, first = reactive.beat_grid(audio_path, self.start, dur)
        per = 60.0 / max(40.0, bpm)
        bass = self.data["bass"]
        rng = random.Random(3)
        j, t = 0, first
        while t < (n_frames / self.fps) + RISE:
            song_t = self.start + t
            if j % every == 0:
                f = int(round(t * self.fps))
                b = float(bass[min(len(bass) - 1, max(0, f))]) if len(bass) else 0.5
                s = min(1.0, 0.45 + 0.55 * b + (0.25 if j % 4 == 0 else 0))
                for typ, ss in self.plan.shots(s, song_t, rng):
                    if typ in GROUND or not rockets or t - RISE < 0:
                        self.events.setdefault(f, []).append(("fire", typ, ss))
                    else:  # send it up early so it bursts on the beat
                        fl = int(round((t - RISE) * self.fps))
                        self.events.setdefault(fl, []).append(("launch", typ, ss, f - fl))
            j += 1
            t = first + j * per

    # -- colours
    def colour(self):
        r = self.rng
        p = self.palette
        if p == "gold":
            return (255, 196, 90) if r.random() < 0.75 else (255, 250, 235)
        if p == "pink":
            return _hsl(r.uniform(285, 335), 95, 65)
        if p == "ice":
            return _hsl(r.uniform(195, 225), 95, 65) if r.random() < 0.6 else (235, 245, 255)
        if p == "fire":
            return _hsl(r.uniform(0, 32), 100, 58)
        if p == "xmas":
            return r.choice([(255, 45, 58), (33, 196, 90), (255, 216, 74), (245, 248, 255)])
        if p == "custom":
            c = r.choice(self.custom)
            return tuple(max(0, min(255, v + r.randint(-14, 14))) for v in c)
        return _hsl(r.choice([0, 30, 50, 120, 190, 220, 280, 320]), 100, 62)

    # -- making things
    def spark(self, x, y, vx, vy, c, **o):
        self.parts.append(P(x, y, vx, vy, c, **o))

    def flash(self, x, y, c, a):
        self.flashes.append([x, y, c, min(1.0, a)])

    def sky(self):
        return self.rng.uniform(0.14, 0.86) * self.w, self.rng.uniform(self.sky_lo, self.sky_hi) * self.h

    def pick_mixed(self):
        tot = sum(self.mix.values())
        if tot <= 0:
            return "peony"
        r = self.rng.random() * tot
        for k, v in self.mix.items():
            r -= v
            if r < 0:
                return k
        return "peony"

    def burst(self, typ, x, y, s):
        R, u = self.rng, self.u
        if typ == "mixed":
            typ = self.pick_mixed()
        if typ == "xmas":
            return self.shape_burst(x, y, s)
        k = self.amount
        sp = 4.2 * k * (0.75 + 0.35 * s) * u
        c = self.colour()
        self.flash(x, y, c, 0.55 + 0.45 * s)
        if typ == "peony":
            c2 = self.colour() if R.random() < 0.4 else c
            for i in range(int((70 + 70 * s) * k)):
                a, v = R.uniform(0, 6.283), sp * math.sqrt(R.uniform(0.15, 1))
                self.spark(x, y, math.cos(a) * v, math.sin(a) * v, c if i % 3 else c2, life=R.uniform(55, 85), max=85, flicker=True)
        elif typ == "willow":
            g = (255, 190, 90) if self.palette == "multi" else c
            for _ in range(int((60 + 40 * s) * k)):
                a, v = R.uniform(0, 6.283), sp * 0.8 * math.sqrt(R.uniform(0.3, 1))
                self.spark(x, y, math.cos(a) * v, math.sin(a) * v, g, life=R.uniform(140, 190), max=190, drag=0.972,
                           grav=0.03, line=True, size=1.6, long=2.0)
        elif typ == "ring":  # a tilted circle of bigger stars, slightly uneven, drooping as it falls
            ax, tilt = R.uniform(0, 6.283), R.uniform(0.2, 1.25)
            c2 = self.colour() if R.random() < 0.35 else c
            n = int((34 + 16 * s) * k)
            for i in range(n):
                a, v = i / n * 6.283 + R.uniform(-0.05, 0.05), sp * 0.95 * R.uniform(0.93, 1.06)
                ux, uy, uz = math.cos(a), math.sin(a) * math.cos(tilt), math.sin(a) * math.sin(tilt)
                rx, ry = ux * math.cos(ax) - uy * math.sin(ax), ux * math.sin(ax) + uy * math.cos(ax)
                self.spark(x, y, rx * v, ry * v, c2 if (i % 2 and c2 != c) else c, life=R.uniform(62, 80), max=80,
                           grav=0.05, drag=0.982, size=2.6 * (0.75 + 0.25 * uz), flicker=True, line=True, long=0.6)
            if R.random() < 0.5:
                for _ in range(16):
                    a, v = R.uniform(0, 6.283), sp * R.uniform(0.2, 0.45)
                    self.spark(x, y, math.cos(a) * v, math.sin(a) * v, _whiten(c, 0.4), life=R.uniform(30, 45), max=45, size=1.6)
        elif typ == "crackle":
            for _ in range(int((26 + 16 * s) * k)):
                a, v = R.uniform(0, 6.283), sp * 0.75 * R.uniform(0.5, 1)
                self.spark(x, y, math.cos(a) * v, math.sin(a) * v, (255, 235, 190), life=R.uniform(38, 50), max=50,
                           crackle=True, line=True, size=1.4)
        elif typ == "heart":
            hc = (255, 80, 140) if self.palette == "multi" else c
            n = int((80 + 30 * s) * k)
            for i in range(n):
                t = i / n * 6.283
                hx = 16 * math.sin(t) ** 3
                hy = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
                self.spark(x, y, hx * sp * 0.055, hy * sp * 0.055, hc, life=80, max=85, grav=0.012, drag=0.975)
        else:
            self.burst("peony", x, y, s)

    def shape_burst(self, x, y, s):
        R, e = self.rng, self.exact
        L = lambda a, b: a + (b - a) * e  # 0 = loose like a real show, 1 = sharp picture
        name = R.choice(SHAPES) if self.xshape == "all" else self.xshape
        pts = shape_points(name, round(L(1.25, 0.6) * 20) / 20)
        tx = 0.3 if name == "text" else 1.0
        r0 = (175 if name == "text" else 150) * self.amount * (0.85 + 0.3 * s) * self.u
        t = R.uniform(-1, 1) * L(0.14, 0.03) * tx
        drag, jv, jp = 0.93, L(0.1, 0.015) * tx, L(0.05, 0.006) * tx
        if pts:  # keep the whole shape inside the picture
            ex = max(abs(q[0]) for q in pts) * r0 * 1.1 + 10 * self.u
            ey = max(abs(q[1]) for q in pts) * r0 * 1.1 + 10 * self.u
            x = min(max(x, ex), self.w - ex) if self.w > 2 * ex else self.w / 2
            y = min(max(y, ey), self.h * 0.6) if self.h * 0.6 > ey else ey
        self.flash(x, y, (255, 240, 220), 0.6 + 0.4 * s)
        for px0, py0, c in pts:
            px, py = px0 * math.cos(t) - py0 * math.sin(t), px0 * math.sin(t) + py0 * math.cos(t)
            j = 1 + R.uniform(-jv, jv)
            self.spark(x, y, (px * j + R.uniform(-jp, jp)) * r0 * (1 - drag), (py * j + R.uniform(-jp, jp)) * r0 * (1 - drag), c,
                       drag=drag, grav=L(0.035, 0.02), hold=L(22, 60), hdrag=L(0.97, 0.985),
                       life=R.uniform(95, 125) * (0.6 if R.random() < L(0.15, 0.02) else 1), max=125,
                       size=L(3, 2.4) * R.uniform(0.85, 1.1) * (0.8 if name == "text" else 1), flicker=True, long=L(0.4, 0.15))
        for i in range(18):
            a, v = R.uniform(0, 6.283), R.uniform(0.5, 2.2) * self.u
            self.spark(x, y, math.cos(a) * v, math.sin(a) * v, _whiten(pts[i % len(pts)][2] if pts else (255, 255, 255), 0.5),
                       life=R.uniform(25, 45), max=45, size=1.4)

    def launch(self, typ, s, frames):
        x, y = self.sky()
        self.rockets.append({"x0": x + self.rng.uniform(-60, 60) * self.u, "x": x, "y": y, "y0": self.h + 20 * self.u,
                             "f": 0, "T": max(1, frames), "type": typ, "s": s})

    def fire(self, typ, s):
        if typ == "lightning":
            self.bolt(s)
        elif typ == "confetti":
            self.confetti(s)
        elif typ == "embers":
            self.embers(int(40 * self.amount), True)
            self.flash(self.w / 2, self.h, (255, 140, 60), 0.25 * s)
        else:
            x, y = self.sky()
            self.burst(typ, x, y, s)

    def bolt(self, s):
        R, u = self.rng, self.u
        x, y = R.uniform(0.2, 0.8) * self.w, -10 * u
        ty = R.uniform(0.45, 0.8) * self.h
        pts = [(x, y)]
        while y < ty:
            x += R.uniform(-38, 38) * u
            y += R.uniform(14, 34) * u
            pts.append((x, y))
        br = []
        if R.random() < 0.7:
            bx, by = pts[int(len(pts) * R.uniform(0.3, 0.6))]
            br = [(bx, by)]
            dr = R.choice((-1, 1))
            for _ in range(8):
                bx += dr * R.uniform(8, 30) * u
                by += R.uniform(10, 28) * u
                br.append((bx, by))
        self.bolts.append({"pts": pts, "br": br, "life": 14.0})
        self.flash(x, ty * 0.5, (190, 210, 255), 0.9 * s + 0.2)

    def confetti(self, s):
        R, u = self.rng, self.u
        n = int((60 + 50 * s) * self.amount)
        for side in (-1, 1):
            for _ in range(n // 2):
                x = 40 * u if side < 0 else self.w - 40 * u
                a, v = -math.pi / 2 + side * -R.uniform(0.08, 0.38), R.uniform(16, 22) * u * (self.h / self.u / 720)
                self.parts.append(P(x, self.h + 10 * u, math.cos(a) * v, math.sin(a) * v, self.colour(), kind="conf",
                                    rot=R.uniform(0, 6), vr=R.uniform(-0.3, 0.3), w=R.uniform(7, 12) * u,
                                    h=R.uniform(4, 7) * u, ph=R.uniform(0, 6), life=R.uniform(260, 380), max=380))

    def embers(self, n, fast=False):
        R, u = self.rng, self.u
        for _ in range(n):
            c = (255, R.randint(110, 190), 50) if self.palette == "multi" else self.colour()
            self.parts.append(P(R.uniform(0, self.w), self.h + R.uniform(0, 30) * u, R.uniform(-0.3, 0.3) * u,
                                -R.uniform(0.6, 2.2) * (2.2 if fast else 1) * u, c, kind="ember",
                                life=R.uniform(220, 420), max=420, size=R.uniform(1, 2.6), ph=R.uniform(0, 6)))

    # -- one frame
    def frame(self, i):
        R, u, k = self.rng, self.u, self.k
        song_t = self.start + i / self.fps
        for ev in self.events.get(i, ()):
            if ev[0] == "launch":
                self.launch(ev[1], ev[2], ev[3])
            else:
                self.fire(ev[1], ev[2])
        ew = self.plan.weight(song_t, "embers")
        if ew and R.random() < 0.5 * k * self.amount * min(1.5, ew):
            self.embers(1)
        if self.snow_on:
            sw = min(1.0, self.plan.weight(song_t, "xmas"))
            if sw and R.random() < 0.9 * sw * k:
                self.parts.append(P(R.uniform(-20, self.w + 20), -8, R.uniform(-0.3, 0.3) * u, R.uniform(0.5, 1.4) * u,
                                    (255, 255, 255), kind="snow", size=R.uniform(1, 2.8), ph=R.uniform(0, 6), life=1e9, max=1e9))

        p = self.p
        p.begin()
        d, g, gs = p.d, p.g, p.gs
        self._sky_light(p.glow, gs)

        keep = []
        for r in self.rockets:  # rockets going up
            r["f"] += 1
            q = min(1.0, r["f"] / r["T"])
            e = 1 - (1 - q) ** 2
            x, y = r["x0"] + (r["x"] - r["x0"]) * e, r["y0"] + (r["y"] - r["y0"]) * e
            self.spark(x, y, R.uniform(-0.3, 0.3) * u, R.uniform(0.5, 1.5) * u, (255, 200, 120), life=R.uniform(14, 24),
                       max=24, size=1.4, grav=0.02)
            if q >= 1:
                self.burst(r["type"], x, y, r["s"])
            else:
                keep.append(r)
        self.rockets = keep

        nxt = []
        tr = self.trail
        for q in self.parts:
            q.life -= k
            if q.life <= 0:
                if q.crackle:
                    for _ in range(6):
                        a, v = R.uniform(0, 6.283), R.uniform(0.6, 2.2) * u
                        nxt.append(P(q.x, q.y, math.cos(a) * v, math.sin(a) * v, (255, 255, 255), life=R.uniform(8, 16),
                                     max=16, size=1.3, glow=False))
                    self.flash(q.x, q.y, (255, 240, 210), 0.06)
                continue
            f = q.life / q.max
            if q.kind == "conf":
                up = q.vy < 0
                cd = (0.985 if up else 0.955) ** k
                q.vx *= cd
                q.vy = q.vy * cd + (0.12 if up else 0.2) * k * u
                q.ph += 0.12 * k
                q.vx += math.sin(q.ph) * 0.08 * k * u
                q.x += q.vx * k
                q.y += q.vy * k
                q.rot += q.vr * k
                fl = math.cos(q.ph * 1.7)
                cr, sr = math.cos(q.rot), math.sin(q.rot)
                pts = [(q.x + (dx * cr - dy * fl * sr), q.y + (dx * sr + dy * fl * cr))
                       for dx, dy in ((-q.w / 2, -q.h / 2), (q.w / 2, -q.h / 2), (q.w / 2, q.h / 2), (-q.w / 2, q.h / 2))]
                d.polygon(pts, fill=q.c + (int(255 * min(1, f * 3)),))
                if q.y < self.h + 40 * u:
                    nxt.append(q)
                continue
            if q.kind == "snow":
                q.ph += 0.02 * k
                q.x += (q.vx + math.sin(q.ph) * 0.35 * u) * k
                q.y += q.vy * k
                r = q.size * u
                d.ellipse((q.x - r, q.y - r, q.x + r, q.y + r), fill=(255, 255, 255, 215))
                if q.y < self.h + 10:
                    nxt.append(q)
                continue
            if q.kind == "ember":
                q.ph += 0.03 * k
                q.x += (q.vx + math.sin(q.ph) * 0.5 * u) * k
                q.y += q.vy * k
                q.vy *= 0.995 ** k
                a = int(230 * min(1, f * 2.5))
                r = q.size * u
                d.ellipse((q.x - r, q.y - r, q.x + r, q.y + r), fill=q.c + (a,))
                gr = r * 3 / gs
                g.ellipse((q.x / gs - gr, q.y / gs - gr, q.x / gs + gr, q.y / gs + gr), fill=q.c + (a // 3,))
                if q.y > -10:
                    nxt.append(q)
                continue
            # sparks
            hl = max(1, int(round(((14 if q.line else 9) * tr * q.long + 1) / k)))
            q.hist.append((q.x, q.y))
            if len(q.hist) > hl:
                del q.hist[:len(q.hist) - hl]
            held = q.hold and (q.max - q.life) < q.hold
            dr = (q.hdrag if (q.hdrag and not held) else q.drag) ** k
            q.vx *= dr
            q.vy = q.vy * dr + (0 if held else q.grav) * k * u
            q.x += q.vx * k
            q.y += q.vy * k
            a = min(1.0, f * 1.6)
            if q.flicker and f < 0.35 and R.random() < 0.5:
                a *= 0.2
            c = _whiten(q.c, (f - 0.8) * 3.5) if f > 0.8 else q.c
            if len(q.hist) > 1 or q.line:
                pts = q.hist + [(q.x, q.y)]
                d.line(pts, fill=c + (int(255 * a * (0.75 if q.line else 0.45)),), width=max(1, int(round(q.size * u * (1.1 if q.line else 0.9)))),
                       joint="curve")
            if not q.line:
                r = q.size * (0.7 + 0.4 * f) * u
                d.ellipse((q.x - r, q.y - r, q.x + r, q.y + r), fill=c + (int(255 * a),))
            if q.glow and a > 0.15:
                gr = max(1.0, q.size * 3 * u / gs)
                g.ellipse((q.x / gs - gr, q.y / gs - gr, q.x / gs + gr, q.y / gs + gr), fill=c + (int(160 * a),))
            nxt.append(q)
        self.parts = nxt[-6000:]

        bolts = []
        for b in self.bolts:  # lightning
            b["life"] -= k
            if b["life"] <= 0:
                continue
            a = b["life"] / 14 * (0.4 if R.random() < 0.3 else 1)
            for pts, wk in ((b["pts"], 1.0), (b["br"], 0.6)):
                if len(pts) < 2:
                    continue
                g.line([(x / gs, y / gs) for x, y in pts], fill=(200, 220, 255, int(200 * a)), width=max(1, int(14 * wk * u / gs)))
                d.line(pts, fill=(200, 220, 255, int(140 * a)), width=max(1, int(5 * wk * u)), joint="curve")
                d.line(pts, fill=(245, 248, 255, int(255 * a)), width=max(1, int(2 * wk * u)), joint="curve")
            bolts.append(b)
        self.bolts = bolts

        out = p.finish()
        for fl in self.flashes:
            fl[3] *= 0.9 ** k
        self.flashes = [fl for fl in self.flashes if fl[3] > 0.02]
        return out

    def _sky_light(self, g, gs):
        """Soft light in the sky around fresh bursts (drawn into the blurred glow layer)."""
        if not self.flashes:
            self.light = None
            return
        tot = max(1.0, sum(f[3] for f in self.flashes) / 1.2)  # many at once must not wash everything out
        spr = _sprite(700 * self.u / gs)
        r = spr.width // 2
        for x, y, c, a in sorted(self.flashes, key=lambda f: -f[3])[:6]:
            lay = Image.new("RGBA", spr.size, c + (0,))
            lay.putalpha(spr.point(lambda v, m=0.2 * a / tot: int(v * m)))
            _paste(g, lay, x / gs - r, y / gs - r)
        self.light = [(x, y, c, a / tot) for x, y, c, a in self.flashes]


def _paste(fr, tile, x, y):
    from .drawn import paste
    paste(fr, tile, x, y)


# ---------------------------------------------------------------- foregrounds

def _seeded(seed):
    r = random.Random(seed)
    return lambda a, b: r.uniform(a, b)


def make_foreground(kind, w, h):
    """The foreground at w x h: a built-in one, or a transparent PNG (scaled to the width, sitting on the bottom)."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if not kind or kind == "none":
        return None
    if kind not in FOREGROUNDS:
        if not os.path.isfile(kind):
            return None
        try:
            src = Image.open(kind)
            src.load()
            src = src.convert("RGBA")
        except Exception:
            return None
        nh = max(1, int(src.height * w / src.width))
        src = src.resize((w, nh), Image.LANCZOS)
        if nh > h:
            src = src.crop((0, nh - h, w, nh))
            nh = h
        im.alpha_composite(src, (0, h - nh))
        return im
    s = h / 720
    d = ImageDraw.Draw(im)
    sr = _seeded(3)
    if kind == "skyline":
        for col, hmin, hmax, lit in (((20, 25, 54), 140, 330, 0.12), ((5, 6, 12), 70, 230, 0.28)):
            x = -20 * s
            while x < w:
                bw, bh = sr(38, 110) * s, sr(hmin, hmax) * s
                d.rectangle((x, h - bh, x + bw, h), fill=col + (255,))
                if sr(0, 1) < 0.25:
                    d.rectangle((x + bw / 2 - 1.5 * s, h - bh - sr(20, 60) * s, x + bw / 2 + 1.5 * s, h - bh), fill=col + (255,))
                wy = h - bh + 12 * s
                while wy < h - 14 * s:
                    wx = x + 8 * s
                    while wx < x + bw - 10 * s:
                        if sr(0, 1) < lit:
                            d.rectangle((wx, wy, wx + 6 * s, wy + 8 * s), fill=(255, int(sr(190, 225)), 120, int(sr(100, 230))))
                        wx += 13 * s
                    wy += 16 * s
                x += bw + sr(-6, 8) * s
    elif kind == "crowd":
        for sc, col, lift in ((1.25, (12, 14, 28), 70), (1.6, (3, 4, 9), 0)):
            x = -40 * s
            while x < w + 40 * s:
                k = sc * sr(0.88, 1.12) * s
                base = h - lift * s + sr(0, 10) * s
                c = col + (255,)
                d.rounded_rectangle((x - 30 * k, base - 66 * k, x + 30 * k, h + 10), radius=int(22 * k), fill=c)
                d.rectangle((x - 6 * k, base - 78 * k, x + 6 * k, base - 60 * k), fill=c)
                d.ellipse((x - 13 * k, base - 108 * k, x + 13 * k, base - 76 * k), fill=c)
                r = sr(0, 1)
                if r < 0.38:
                    side = -1 if sr(0, 1) < 0.5 else 1
                    ax, ay = x + side * 22 * k, base - 58 * k
                    ex, ey = ax + side * sr(6, 22) * k, ay - sr(38, 50) * k
                    hx, hy = ex + side * sr(-6, 10) * k, ey - sr(36, 46) * k
                    d.line([(ax, ay), (ex, ey), (hx, hy)], fill=c, width=max(2, int(11 * k)), joint="curve")
                    d.ellipse((hx - 6.5 * k, hy - 10.5 * k, hx + 6.5 * k, hy + 2.5 * k), fill=c)
                    if sr(0, 1) < 0.22 and 20 < hx < w - 20:  # a phone held up
                        d.rounded_rectangle((hx - 5 * k, hy - 22 * k, hx + 5 * k, hy - 6 * k), radius=int(2 * k) or 1,
                                            fill=(150, 185, 235, 170))
                elif r < 0.5:
                    for side in (-1, 1):
                        ax, ay = x + side * 22 * k, base - 58 * k
                        d.line([(ax, ay), (ax + side * 20 * k, ay - 44 * k), (ax + side * 26 * k, ay - 88 * k)], fill=c,
                               width=max(2, int(11 * k)), joint="curve")
                x += sr(44, 60) * sc * s
    elif kind == "mountains":
        def ridge(col, y0, amp, f):
            pts = [(0, h)] + [(x, y0 - amp * (math.sin(x / s * f + 3) * 0.6 + math.sin(x / s * f * 2.7 + 1) * 0.3 +
                                              math.sin(x / s * f * 7.1) * 0.1)) for x in range(0, w + 8, 8)] + [(w, h)]
            d.polygon(pts, fill=col + (255,))
        ridge((16, 21, 44), h * 0.62, 110 * s, 0.004)
        ridge((7, 10, 23), h * 0.78, 70 * s, 0.007)
        x = -10 * s
        while x < w + 10 * s:
            th, b = sr(70, 150) * s, h - sr(0, 14) * s
            tw = th * 0.42
            for kk in range(3):
                d.polygon([(x, b - th + kk * th * 0.22), (x - tw * (0.5 + kk * 0.25), b - th * 0.35 + kk * th * 0.22),
                           (x + tw * (0.5 + kk * 0.25), b - th * 0.35 + kk * th * 0.22)], fill=(2, 3, 6, 255))
            d.rectangle((x - 4 * s, b - th * 0.3, x + 4 * s, b + 14 * s), fill=(2, 3, 6, 255))
            x += sr(18, 46) * s
    return im


def lit_foreground(fg, light, gs=3):
    """The foreground with the edges facing the flashes lit up (light: [(x, y, rgb, strength)])."""
    if not light:
        return fg
    w, h = fg.size
    lw, lh = max(2, w // gs), max(2, h // gs)
    glow = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    spr = _sprite(900 * (min(w, h) / 720) / gs)
    r = spr.width // 2
    for x, y, c, a in sorted(light, key=lambda f: -f[3])[:6]:
        lay = Image.new("RGBA", spr.size, c + (0,))
        lay.putalpha(spr.point(lambda v, m=0.3 * a: int(v * m)))
        _paste(glow, lay, x / gs - r, y / gs - r)
    glow = glow.resize((w, h), Image.BILINEAR)
    a = np.asarray(glow).astype(np.float32)
    m = np.asarray(fg.getchannel("A")).astype(np.float32) / 255
    a[:, :, 3] *= m  # only where the foreground is
    out = fg.copy()
    out.alpha_composite(Image.fromarray(a.astype(np.uint8), "RGBA"))
    return out

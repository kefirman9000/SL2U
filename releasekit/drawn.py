"""Drawn visualizers: styles ffmpeg's built-in filters can't make (radial ring, radial waveform,
particles, geometric shapes, tunnel, synthwave grid).

The music is analysed once (spectrum, bass hits, waveform and a slow "section energy" that is
higher in a chorus than in a verse), then every frame is drawn with Pillow and streamed into
ffmpeg as a transparent layer over the background. The bumping cover/disc/logo/text is drawn
into the same layer, in the middle, so rings and particles sit around it.
"""
import math

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import audio, reactive

DRAWN = {"ring": "Radial ring (bars around the art)", "ringwave": "Radial waveform", "particles": "Particles",
         "geometric": "Geometric shapes", "tunnel": "Tunnel", "synthwave": "Synthwave grid",
         # retro Windows screensavers
         "starfield": "Starfield", "mystify": "Mystify", "pipes": "3D Pipes", "maze": "3D Maze"}
# styles built around a centre element: they get the cover in the middle if no bump is chosen
CENTERED = ("ring", "ringwave", "particles", "geometric", "tunnel", "starfield")
OPAQUE = ("maze",)  # scenes that cover the whole background
MAX_SHORT_SIDE = 1080  # draw at most this big, ffmpeg scales up (4K)


def layer_size(w, h):
    s = min(1.0, MAX_SHORT_SIDE / min(w, h))
    even = lambda v: max(2, int(round(v * s / 2)) * 2)
    return even(w), even(h)


# ---------------------------------------------------------------- analysis

def _ema(x, up, down):
    out = np.empty_like(x)
    prev = x[0]
    for i, v in enumerate(x):
        prev = prev + (v - prev) * np.where(v > prev, up, down)
        out[i] = prev
    return out


def analyse(audio_path, fps, start=0.0, dur=None, n_frames=None, bands=48):
    """Per video frame: spectrum (bands 0..1), bass (0..1), energy (slow, 0..1), onset flag, waveform."""
    sr = 22050
    x = audio._pcm(audio_path, sr, max_seconds=(start + dur) if dur else None)[int(start * sr):]
    if dur:
        x = x[: int(dur * sr)]
    n = n_frames or max(1, int(len(x) / sr * fps))
    if len(x) < 64:
        z = np.zeros(n, np.float32)
        return {"spec": np.zeros((n, bands), np.float32), "bass": z, "energy": z, "onset": z.astype(bool),
                "wave": np.zeros((n, 256), np.float32)}
    N, hop = 2048, sr / fps
    pad = np.pad(x, (N // 2, N + int(hop) + 2))
    win = np.hanning(N).astype(np.float32)
    freqs = np.fft.rfftfreq(N, 1 / sr)
    idx = np.searchsorted(freqs, np.geomspace(40, 11000, bands + 1))
    spec = np.zeros((n, bands), np.float32)
    wave = np.zeros((n, 256), np.float32)
    rms = np.zeros(n, np.float32)
    for c0 in range(0, n, 400):
        ii = np.arange(c0, min(n, c0 + 400))
        seg = np.stack([pad[int(i * hop): int(i * hop) + N] for i in ii])
        mag = np.abs(np.fft.rfft(seg * win, axis=1))
        for b in range(bands):
            lo = idx[b]
            spec[ii, b] = mag[:, lo: max(lo + 1, idx[b + 1])].mean(axis=1)
        wave[ii] = seg[:, N // 2 - 512: N // 2 + 512: 4]
        rms[ii] = np.sqrt((seg[:, N // 4: 3 * N // 4] ** 2).mean(axis=1))
    db = 20 * np.log10(spec + 1e-6)
    lo, hi = np.percentile(db, 25, axis=0), np.percentile(db, 99.5, axis=0)
    spec = np.clip((db - lo) / np.maximum(hi - lo, 9), 0, 1) ** 1.3
    spec = _ema(spec, 0.75, 0.22)
    # slow energy: ~2 s window, quiet verse low, loud chorus high
    k = max(1, int(fps * 2))
    slow = np.convolve(rms, np.ones(k) / k, mode="same")
    e_lo, e_hi = np.percentile(slow, 10), np.percentile(slow, 95)
    energy = np.clip((slow - e_lo) / max(e_hi - e_lo, 1e-6), 0, 1)
    bass = reactive.bass_envelope(audio_path, fps, start, dur, n)[:n]
    if len(bass) < n:
        bass = np.pad(bass, (0, n - len(bass)))
    onset = np.zeros(n, bool)
    onset[1:] = (bass[1:] - bass[:-1] > 0.18) & (bass[1:] > 0.45)
    peak = np.percentile(np.abs(wave), 99.5) or 1.0
    return {"spec": spec.astype(np.float32), "bass": bass.astype(np.float32), "energy": energy.astype(np.float32),
            "onset": onset, "wave": np.clip(wave / peak, -1, 1).astype(np.float32)}


# ---------------------------------------------------------------- drawing helpers

def _mix(rgb, other, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(rgb, other))


class Painter:
    """A sharp layer plus a 1/3-size glow layer that is blurred and scaled up under it."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.gs = 3
        self.gw, self.gh = max(2, w // self.gs), max(2, h // self.gs)

    def begin(self):
        self.img = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        self.glow = Image.new("RGBA", (self.gw, self.gh), (0, 0, 0, 0))
        self.d = ImageDraw.Draw(self.img)
        self.g = ImageDraw.Draw(self.glow)

    def line(self, pts, fill, width, glow=True, glow_width=None):
        self.d.line(pts, fill=fill, width=max(1, int(width)), joint="curve")
        if glow:
            s = self.gs
            self.g.line([(x / s, y / s) for x, y in pts], fill=fill[:3] + (min(255, fill[3]),),
                        width=max(1, int((glow_width or width * 2.5) / s)))

    def dot(self, x, y, r, fill, glow=True):
        self.d.ellipse((x - r, y - r, x + r, y + r), fill=fill)
        if glow:
            s, gr = self.gs, max(1.0, r * 2.2 / self.gs)
            self.g.ellipse((x / s - gr, y / s - gr, x / s + gr, y / s + gr), fill=fill)

    def finish(self, glow_radius=None):
        g = self.glow.filter(ImageFilter.GaussianBlur(glow_radius or max(2, self.gh // 90)))
        g = g.resize((self.w, self.h), Image.BILINEAR)
        g.alpha_composite(self.img)
        return g


# ---------------------------------------------------------------- styles

class Style:
    def __init__(self, w, h, fps, rgb, vertical, data, size=1.0):
        self.w, self.h, self.fps, self.rgb, self.data = w, h, fps, rgb, data
        self.S = min(w, h) * max(0.85, min(1.3, float(size or 1)))  # smaller would run into the art in the middle
        self.center = 1.0  # size of the cover/logo in the middle (rings make room for it)
        self.cx, self.cy = w / 2, h / 2 - (h * 0.06 if vertical else 0)
        self.light = _mix(rgb, (255, 255, 255), 0.55)
        self.p = Painter(w, h)
        self.rng = np.random.default_rng(7)

    def frame(self, i):
        self.p.begin()
        self.draw(i, i / self.fps, self.data["spec"][i], float(self.data["bass"][i]),
                  float(self.data["energy"][i]), bool(self.data["onset"][i]), self.data["wave"][i])
        return self.p.finish()


class Ring(Style):
    N = 90

    def draw(self, i, t, spec, bass, energy, onset, wave):
        S, n = self.S, self.N
        half = n // 2
        vals = np.interp(np.linspace(0, 1, half + 1), np.linspace(0, 1, 36), spec[:36])
        vals = np.concatenate([vals, vals[-2:0:-1]])  # mirror: bass at the top, left/right symmetric
        r0 = min(S * (0.31 + 0.025 * bass) * self.center, min(self.w, self.h) * 0.42)
        rot = t * 0.08
        width = 2 * math.pi * r0 / n * 0.55
        room = max(S * 0.03, min(self.w, self.h) * 0.49 - r0)  # keep the bars on screen with a big logo
        for k, v in enumerate(vals):
            a = rot + 2 * math.pi * k / n - math.pi / 2
            ln = min(room, S * (0.012 + v * (0.13 + 0.07 * energy)))
            ca, sa = math.cos(a), math.sin(a)
            col = _mix(self.rgb, (255, 255, 255), max(0.0, v - 0.75) * 2) + (235,)
            self.p.line([(self.cx + ca * r0, self.cy + sa * r0), (self.cx + ca * (r0 + ln), self.cy + sa * (r0 + ln))],
                        col, width)
        # thin inner circle that brightens on the beat
        rr = r0 - S * 0.012
        self.p.d.ellipse((self.cx - rr, self.cy - rr, self.cx + rr, self.cy + rr),
                         outline=self.light + (int(90 + 140 * bass),), width=max(1, int(S * 0.003)))


class RingWave(Style):
    def draw(self, i, t, spec, bass, energy, onset, wave):
        S = self.S
        n = len(wave)
        ramp = np.minimum(1, np.minimum(np.arange(n), n - 1 - np.arange(n)) / 24)  # close the loop smoothly
        w = wave * ramp
        for layer, (scale, alpha, off) in enumerate(((1.0, 240, 0.0), (0.6, 120, 0.7))):
            r0 = min(S * (0.32 + 0.02 * bass + 0.015 * layer) * self.center, min(self.w, self.h) * 0.4)
            amp = min(S * (0.06 + 0.07 * energy), min(self.w, self.h) * 0.49 - r0) * scale
            a = np.linspace(0, 2 * math.pi, n, endpoint=False) - math.pi / 2 + t * (0.15 if layer else -0.1) + off
            r = r0 + w * amp
            pts = list(zip(self.cx + np.cos(a) * r, self.cy + np.sin(a) * r))
            pts.append(pts[0])
            col = (self.rgb if layer == 0 else self.light) + (alpha,)
            self.p.line(pts, col, S * (0.006 if layer == 0 else 0.003))


class Particles(Style):
    def __init__(self, *a):
        super().__init__(*a)
        self.pos = np.zeros((0, 2))
        self.vel = np.zeros((0, 2))
        self.life = np.zeros(0)
        self.size = np.zeros(0)
        self.hue = np.zeros(0)

    def draw(self, i, t, spec, bass, energy, onset, wave):
        S, rng = self.S, self.rng
        n_new = int(2 + 10 * energy + 18 * bass) + (int(70 + 60 * energy) if onset else 0)
        if n_new:
            ang = rng.uniform(0, 2 * math.pi, n_new)
            r = S * rng.uniform(0.2, 0.26, n_new) * self.center
            spd = S * rng.uniform(0.002, 0.007, n_new) * (1 + 1.5 * onset + bass)
            tang = rng.uniform(-0.3, 0.3, n_new)
            dirs = np.stack([np.cos(ang), np.sin(ang)], 1)
            perp = np.stack([-np.sin(ang), np.cos(ang)], 1)
            self.pos = np.vstack([self.pos, np.stack([self.cx + np.cos(ang) * r, self.cy + np.sin(ang) * r], 1)])
            self.vel = np.vstack([self.vel, (dirs + perp * tang[:, None]) * spd[:, None]])
            self.life = np.concatenate([self.life, np.ones(n_new)])
            self.size = np.concatenate([self.size, S * rng.uniform(0.002, 0.007, n_new)])
            self.hue = np.concatenate([self.hue, rng.uniform(0, 1, n_new)])
        self.pos += self.vel * (0.7 + 1.6 * bass)
        self.vel *= 0.985
        self.life -= 1 / (self.fps * (2.2 + 1.5 * (1 - energy)))
        keep = (self.life > 0) & (self.pos[:, 0] > -20) & (self.pos[:, 0] < self.w + 20) & \
               (self.pos[:, 1] > -20) & (self.pos[:, 1] < self.h + 20)
        if keep.sum() > 2500:
            keep &= np.cumsum(keep[::-1])[::-1] <= 2500  # oldest go first
        self.pos, self.vel, self.life, self.size, self.hue = (self.pos[keep], self.vel[keep], self.life[keep],
                                                               self.size[keep], self.hue[keep])
        for (x, y), l, s, h in zip(self.pos, self.life, self.size, self.hue):
            col = (self.light if h > 0.75 else self.rgb) + (int(255 * min(1, l * 1.4)),)
            self.p.dot(x, y, s * (0.5 + 0.8 * l), col, glow=h > 0.4)


class Geometric(Style):
    SHAPES = ((3, 0.30, 9, (0, 4)), (4, 0.36, -6, (4, 12)), (6, 0.43, 4, (12, 26)), (5, 0.51, -11, (26, 48)))

    def __init__(self, *a):
        super().__init__(*a)
        self.kick = 0.0

    def draw(self, i, t, spec, bass, energy, onset, wave):
        S = self.S
        self.kick = 1.0 if onset else self.kick * 0.9
        for k, (sides, rad, speed, (b0, b1)) in enumerate(self.SHAPES):
            v = float(spec[b0:b1].mean())
            r = S * rad * (1 + 0.18 * v + 0.05 * bass) * max(1.0, self.center)
            a0 = math.radians(t * speed * (1 + energy) + self.kick * 12 * (1 if k % 2 else -1))
            pts = [(self.cx + r * math.cos(a0 + 2 * math.pi * j / sides - math.pi / 2),
                    self.cy + r * math.sin(a0 + 2 * math.pi * j / sides - math.pi / 2)) for j in range(sides + 1)]
            col = _mix(self.rgb, (255, 255, 255), 0.25 * k / 3) + (int(120 + 135 * v),)
            self.p.line(pts, col, S * (0.003 + 0.006 * v))
            for x, y in pts[:-1]:
                self.p.dot(x, y, S * (0.004 + 0.006 * v), self.light + (230,))


class Tunnel(Style):
    K = 24
    ZMIN, ZMAX = 0.3, 6.0

    def __init__(self, *a):
        super().__init__(*a)
        self.z = np.linspace(self.ZMIN, self.ZMAX, self.K)

    def draw(self, i, t, spec, bass, energy, onset, wave):
        S = self.S
        self.z -= (0.5 + 1.2 * energy + 2.2 * bass) / self.fps
        self.z[self.z < self.ZMIN] += self.ZMAX - self.ZMIN
        far = max(self.w, self.h) * 0.9
        for k in np.argsort(-self.z):
            z = self.z[k]
            r = S * 0.3 / z
            if r > far:
                continue
            v = float(spec[int(k * 3) % 48])
            sides = 8
            twist = z * 0.6 + t * 0.15
            pts = [(self.cx + r * math.cos(twist + 2 * math.pi * j / sides), self.cy + r * math.sin(twist + 2 * math.pi * j / sides))
                   for j in range(sides + 1)]
            near = min(1.0, 1.5 / z) * min(1.0, (self.ZMAX - z) * 1.5)
            col = _mix(self.rgb, (255, 255, 255), v * 0.5) + (int(255 * near * (0.35 + 0.65 * v)),)
            self.p.line(pts, col, max(1.0, S * 0.006 / z))
        # spokes give the depth
        for j in range(8):
            a = t * 0.15 + 2 * math.pi * j / 8
            self.p.line([(self.cx + math.cos(a) * S * 0.03, self.cy + math.sin(a) * S * 0.03),
                         (self.cx + math.cos(a) * far, self.cy + math.sin(a) * far)],
                        self.rgb + (int(40 + 60 * energy),), max(1.0, S * 0.0015), glow=False)


class Synthwave(Style):
    def __init__(self, *a):
        super().__init__(*a)
        self.phase = 0.0
        self.pink = (255, 60, 170) if self.rgb != (255, 60, 170) else (60, 200, 255)

    def draw(self, i, t, spec, bass, energy, onset, wave):
        w, h, S, d = self.w, self.h, self.S, self.p.d
        hy = h * (0.62 if w >= h else 0.66)
        # sun with stripes cut out of its lower half
        rs = S * (0.2 + 0.015 * bass)
        sun = Image.new("RGBA", (int(rs * 2) + 2, int(rs * 2) + 2), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sun)
        for yy in range(sun.height):
            tt = yy / sun.height
            sd.line([(0, yy), (sun.width, yy)], fill=_mix((255, 210, 80), self.pink, tt) + (235,))
        mask = Image.new("L", sun.size, 0)
        md = ImageDraw.Draw(mask)
        md.ellipse((0, 0, sun.width - 1, sun.height - 1), fill=255)
        for k in range(6):
            yy = sun.height * (0.55 + k * 0.075)
            md.rectangle((0, yy, sun.width, yy + sun.height * 0.012 * (k + 1)), fill=0)
        sun.putalpha(mask)
        self.p.img.alpha_composite(sun, (int(self.cx - rs), int(hy - rs * 1.25)))
        sg = self.p.gs
        self.p.g.ellipse(((self.cx - rs * 1.1) / sg, (hy - rs * 1.35) / sg, (self.cx + rs * 1.1) / sg, (hy + rs * 0.85) / sg),
                         fill=self.pink + (int(70 + 90 * bass),))
        # spectrum skyline on the horizon
        n = 64
        pos = np.abs(np.arange(n) - (n - 1) / 2) / ((n - 1) / 2)  # mirrored: bass in the middle, highs at the edges
        vals = np.interp(pos, np.linspace(0, 1, 48), spec)
        bw = w / n
        for k, vv in enumerate(vals):
            bh = h * 0.12 * float(vv) * (0.5 + 0.5 * energy)
            d.rectangle((k * bw + 1, hy - bh, (k + 1) * bw - 1, hy), fill=(10, 0, 25, 200))
        # floor
        d.rectangle((0, hy, w, h), fill=(8, 0, 20, 150))
        col = self.rgb + (230,)
        lw = max(1.0, S * 0.003)
        self.phase = (self.phase + (0.6 + 1.2 * energy + 2.5 * bass) / self.fps) % 1.0
        for k in range(14):  # horizontal lines rushing toward you
            z = (k + 1 - self.phase) / 14
            y = hy + (h - hy) * (0.04 / max(z, 0.02)) ** 0.8
            if hy < y < h:
                self.p.line([(0, y), (w, y)], self.rgb + (int(255 * min(1, (y - hy) / (h - hy) * 2.5)),), lw)
        for k in range(-16, 17):  # lines to the vanishing point
            xb = self.cx + k * w * 0.11
            self.p.line([(self.cx + k * w * 0.012, hy), (xb, h)], col, lw)
        self.p.line([(0, hy), (w, hy)], self.light + (255,), lw * 1.5)


class Starfield(Style):
    """Windows "Starfield": flying through stars; faster on the beat and in loud parts."""
    N = 700

    def __init__(self, *a):
        super().__init__(*a)
        r = self.rng
        self.x, self.y = r.uniform(-1, 1, self.N), r.uniform(-1, 1, self.N)
        self.z = r.uniform(0.05, 1, self.N)

    def draw(self, i, t, spec, bass, energy, onset, wave):
        w, h = self.w, self.h
        f = max(w, h) * 0.5
        speed = (0.12 + 0.5 * energy + 1.1 * bass) / self.fps
        z0 = self.z.copy()
        self.z -= speed
        gone = self.z < 0.03
        n = int(gone.sum())
        if n:
            self.x[gone], self.y[gone] = self.rng.uniform(-1, 1, n), self.rng.uniform(-1, 1, n)
            self.z[gone] = 1.0
            z0[gone] = 1.0
        sx, sy = self.cx + self.x / self.z * f * 0.5, self.cy + self.y / self.z * f * 0.5
        px, py = self.cx + self.x / z0 * f * 0.5, self.cy + self.y / z0 * f * 0.5
        streak = speed * self.fps > 0.6
        for k in np.argsort(-self.z):
            z = self.z[k]
            if not (-10 < sx[k] < w + 10 and -10 < sy[k] < h + 10):
                continue
            b = min(1.0, (1 - z) * 1.4)
            col = _mix(self.light, (255, 255, 255), 1 - z) + (int(255 * b),)
            r = max(0.6, self.S * 0.004 * (1 - z) ** 2 * 2)
            if streak and z < 0.9:
                self.p.line([(px[k], py[k]), (sx[k], sy[k])], col, r * 1.4, glow=z < 0.4)
            else:
                self.p.dot(sx[k], sy[k], r, col, glow=z < 0.3)


class Mystify(Style):
    """Windows "Mystify Your Mind": two bouncing shapes leaving trails of lines."""
    TRAIL = 9

    def __init__(self, *a):
        super().__init__(*a)
        r = self.rng
        self.pts = [np.stack([r.uniform(0, self.w, 4), r.uniform(0, self.h, 4)], 1) for _ in range(2)]
        self.vel = [r.uniform(-1, 1, (4, 2)) * self.S * 0.004 for _ in range(2)]
        self.hist = [[] for _ in range(2)]
        self.cols = [self.rgb, _mix(self.rgb, (255, 255, 255), 0.6) if self.rgb != (255, 255, 255) else (120, 200, 255)]

    def draw(self, i, t, spec, bass, energy, onset, wave):
        mult = 0.6 + 1.2 * energy + 2.0 * bass + (3.0 if onset else 0)
        for s in range(2):
            p, v = self.pts[s], self.vel[s]
            p += v * mult
            for ax, lim in ((0, self.w), (1, self.h)):
                lo, hi = p[:, ax] < 0, p[:, ax] > lim
                v[lo | hi, ax] *= -1
                p[:, ax] = np.clip(p[:, ax], 0, lim)
            self.hist[s].append(p.copy())
            self.hist[s] = self.hist[s][-self.TRAIL:]
            for k, q in enumerate(self.hist[s]):
                a = (k + 1) / len(self.hist[s])
                pts = [tuple(x) for x in q] + [tuple(q[0])]
                self.p.line(pts, self.cols[s] + (int(255 * a),), max(1.0, self.S * 0.0025), glow=k == len(self.hist[s]) - 1)


class Pipes(Style):
    """Windows "3D Pipes": pipes grow through a 3D grid; faster with the music. Starts over when full."""
    G = 9
    DIRS = [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)]

    def __init__(self, *a):
        super().__init__(*a)
        hues = [self.rgb, (230, 60, 60), (60, 200, 90), (60, 120, 240), (240, 200, 60), (200, 90, 230), (60, 210, 220)]
        self.palette = hues
        self.reset()
        self.acc = 0.0

    def reset(self):
        self.used = set()
        self.segs = []  # (p0, p1, colour)
        self.joints = []  # (p, colour)
        self.heads = []
        self.fade = 0

    def _new_head(self):
        r = self.rng
        for _ in range(40):
            p = tuple(int(v) for v in r.integers(0, self.G, 3))
            if p not in self.used:
                self.used.add(p)
                col = self.palette[(len(self.heads) + len(self.segs)) % len(self.palette)]
                self.heads.append([p, self.DIRS[r.integers(0, 6)], col])
                self.joints.append((p, col))
                return
        self.fade = 1

    def _step(self):
        if len(self.heads) < 2:
            self._new_head()
        for hd in list(self.heads):
            p, d, col = hd
            opts = [d] * 6 + [x for x in self.DIRS if x != d and x != tuple(-v for v in d)]
            self.rng.shuffle(opts)
            for nd in opts:
                q = (p[0] + nd[0], p[1] + nd[1], p[2] + nd[2])
                if all(0 <= v < self.G for v in q) and q not in self.used:
                    self.used.add(q)
                    self.segs.append((p, q, col))
                    if nd != d:
                        self.joints.append((p, col))
                    hd[0], hd[1] = q, nd
                    break
            else:
                self.joints.append((p, col))
                self.heads.remove(hd)
        if len(self.used) > self.G ** 3 * 0.45:
            self.fade = 1

    def _proj(self, p, t):
        g = (self.G - 1) / 2
        x, y, z = p[0] - g, p[1] - g, p[2] - g
        a = 0.35 + 0.08 * math.sin(t * 0.07)
        x, z = x * math.cos(a) - z * math.sin(a), x * math.sin(a) + z * math.cos(a)
        y2, z2 = y * math.cos(0.32) - z * math.sin(0.32), y * math.sin(0.32) + z * math.cos(0.32)
        d = z2 + self.G * 1.6
        f = self.S * 1.55 / d * self.G / 9
        return self.cx + x * f, self.cy + y2 * f, d

    def draw(self, i, t, spec, bass, energy, onset, wave):
        if self.fade:
            self.fade += 1
            if self.fade > self.fps * 0.8:
                self.reset()
        else:
            self.acc += (3 + 10 * energy + 12 * bass) / self.fps
            while self.acc >= 1:
                self.acc -= 1
                self._step()
        alpha = 1.0 if not self.fade else max(0.0, 1 - self.fade / (self.fps * 0.8))
        items = []
        for p0, p1, col in self.segs:
            a, b = self._proj(p0, t), self._proj(p1, t)
            items.append(((a[2] + b[2]) / 2, "s", a, b, col))
        for p, col in self.joints:
            a = self._proj(p, t)
            items.append((a[2] - 0.01, "j", a, None, col))
        unit = self.S * 0.03
        for depth, kind, a, b, col in sorted(items, key=lambda it: -it[0]):
            wpx = unit * self.G * 1.6 / depth
            shade = max(0.35, min(1.0, 1.9 - depth / self.G))
            base = tuple(int(c * shade * 0.55) for c in col) + (int(255 * alpha),)
            hi = _mix(tuple(int(c * shade) for c in col), (255, 255, 255), 0.25 + 0.25 * bass) + (int(255 * alpha),)
            if kind == "s":
                self.p.line([a[:2], b[:2]], base, wpx, glow=False)
                off = wpx * 0.18
                self.p.line([(a[0] - off, a[1] - off), (b[0] - off, b[1] - off)], hi, wpx * 0.35, glow=False)
            else:
                r = wpx * 0.62
                self.p.d.ellipse((a[0] - r, a[1] - r, a[0] + r, a[1] + r), fill=base)
                self.p.d.ellipse((a[0] - r * 0.55, a[1] - r * 0.6, a[0] + r * 0.15, a[1] + r * 0.1), fill=hi)


class Maze(Style):
    """Windows "3D Maze": walking the corridors of a brick maze, following the right-hand wall.
    Walks faster in loud parts, the walls light up on the beat."""
    N = 12

    def __init__(self, *a):
        super().__init__(*a)
        self.rw, self.rh = max(160, self.w // 3) // 2 * 2, max(90, self.h // 3) // 2 * 2  # a third of the size: retro and fast
        self.grid = self._make_maze()
        self.path = self._walk()
        self.pos = 0.0
        self.ang = None

    def _make_maze(self):
        n, r = self.N, self.rng
        m = np.ones((2 * n + 1, 2 * n + 1), np.uint8)
        stack, seen = [(0, 0)], {(0, 0)}
        m[1, 1] = 0
        while stack:
            cx, cy = stack[-1]
            nb = [(cx + dx, cy + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                  if 0 <= cx + dx < n and 0 <= cy + dy < n and (cx + dx, cy + dy) not in seen]
            if not nb:
                stack.pop()
                continue
            nx, ny = nb[r.integers(0, len(nb))]
            m[2 * cy + 1 + (ny - cy), 2 * cx + 1 + (nx - cx)] = 0
            m[2 * ny + 1, 2 * nx + 1] = 0
            seen.add((nx, ny))
            stack.append((nx, ny))
        return m

    def _walk(self):
        """Cell centres visited by keeping a hand on the right wall (it covers the whole maze, then repeats)."""
        m = self.grid
        dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]  # E S W N (x, y)
        x, y, d = 1, 1, 0
        pts = [(x + 0.5, y + 0.5)]
        for _ in range(self.N * self.N * 4):
            for turn in (1, 0, 3, 2):  # right, straight, left, back
                nd = (d + turn) % 4
                dx, dy = dirs[nd]
                if m[y + dy, x + dx] == 0:
                    x, y, d = x + 2 * dx, y + 2 * dy, nd
                    break
            pts.append((x + 0.5, y + 0.5))
            if (x, y) == (1, 1) and d == 0 and len(pts) > 4:
                break
        return np.array(pts, np.float32)

    def draw(self, i, t, spec, bass, energy, onset, wave):
        pth = self.path
        self.pos = (self.pos + (0.7 + 1.1 * energy + 0.8 * bass) / self.fps) % (len(pth) - 1)
        k = int(self.pos)
        f = self.pos - k
        p0, p1 = pth[k], pth[(k + 1) % len(pth)]
        px, py = p0 + (p1 - p0) * f
        want = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        if self.ang is None:
            self.ang = want
        diff = (want - self.ang + math.pi) % (2 * math.pi) - math.pi
        self.ang += diff * min(1.0, 7.0 / self.fps)  # turn smoothly into corners
        W, H = self.rw, self.rh
        # one ray per column (DDA), all columns at once
        camx = np.linspace(-1, 1, W, dtype=np.float32)
        fov = 0.66
        dx = math.cos(self.ang) - math.sin(self.ang) * fov * camx
        dy = math.sin(self.ang) + math.cos(self.ang) * fov * camx
        mx, my = np.full(W, int(px)), np.full(W, int(py))
        ddx, ddy = np.abs(1 / np.where(dx == 0, 1e-9, dx)), np.abs(1 / np.where(dy == 0, 1e-9, dy))
        sx, sy = np.where(dx < 0, -1, 1), np.where(dy < 0, -1, 1)
        side_x = np.where(dx < 0, (px - mx) * ddx, (mx + 1 - px) * ddx)
        side_y = np.where(dy < 0, (py - my) * ddy, (my + 1 - py) * ddy)
        hit, side = np.zeros(W, bool), np.zeros(W, np.int8)
        g = self.grid
        for _ in range(64):
            go_x = (side_x < side_y) & ~hit
            go_y = ~go_x & ~hit
            side_x = np.where(go_x, side_x + ddx, side_x)
            mx = np.where(go_x, mx + sx, mx)
            side_y = np.where(go_y, side_y + ddy, side_y)
            my = np.where(go_y, my + sy, my)
            side = np.where(go_x, 0, np.where(go_y, 1, side))
            inside = (mx >= 0) & (my >= 0) & (mx < g.shape[1]) & (my < g.shape[0])
            hit |= ~inside | (g[np.clip(my, 0, g.shape[0] - 1), np.clip(mx, 0, g.shape[1] - 1)] == 1)
            if hit.all():
                break
        dist = np.where(side == 0, side_x - ddx, side_y - ddy)
        dist = np.maximum(dist, 0.05)
        u = np.where(side == 0, py + dist * dy, px + dist * dx)
        u = u - np.floor(u)
        lh = H / dist
        top = (H - lh) / 2
        Y = np.arange(H, dtype=np.float32)[:, None]
        v = (Y - top[None, :]) / lh[None, :]
        wall = (v >= 0) & (v < 1)
        # bricks: mortar every 1/4 of the height, offset every other row
        row = np.floor(v * 4)
        mortar = ((v * 4) % 1 < 0.07) | (((u[None, :].astype(np.float32) * 2 + row * 0.5) % 1) < 0.035)
        fog = np.exp(-dist * 0.16)[None, :] * (np.where(side == 1, 0.72, 1.0))[None, :]
        glow = 0.75 + 0.5 * bass
        base = np.array(self.rgb, np.float32) * 0.85 + 20
        brick = np.where(mortar[..., None], base * 0.25, base * glow)
        img = np.zeros((H, W, 3), np.float32)
        # ceiling and floor: dark gradients
        horizon = np.abs(Y - H / 2) / (H / 2)
        img[:] = (np.where(Y < H / 2, 1, 0)[..., None] * np.array([12, 12, 22], np.float32) +
                  np.where(Y >= H / 2, 1, 0)[..., None] * (np.array(self.rgb, np.float32) * 0.18)) * horizon[..., None]
        img = np.where(wall[..., None], brick * fog[..., None], img)
        self.out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB").resize((self.w, self.h), Image.BILINEAR)

    def frame(self, i):  # fills the whole picture: no glow pass needed
        self.draw(i, i / self.fps, self.data["spec"][i], float(self.data["bass"][i]),
                  float(self.data["energy"][i]), bool(self.data["onset"][i]), self.data["wave"][i])
        return self.out.convert("RGBA")


STYLES = {"ring": Ring, "ringwave": RingWave, "particles": Particles, "geometric": Geometric, "tunnel": Tunnel,
          "synthwave": Synthwave, "starfield": Starfield, "mystify": Mystify, "pipes": Pipes, "maze": Maze}


# ---------------------------------------------------------------- the layer ffmpeg reads

def _element(opts, lw, lh, vertical):
    """Pre-rendered bump tiles at layer scale, or None."""
    bump = opts.get("bump", "none")
    if bump == "none":
        return None
    k = max(0.5, min(1.8, float(opts.get("center_size") or 1)))
    canvas = int(min(min(lw, lh) * 0.56 * k, min(lw, lh))) // 2 * 2  # same proportion in vertical videos
    rgb = reactive.pick_color(opts.get("vis_color", "white"), opts.get("image"))
    tiles = reactive.bump_frames(bump, canvas, float(opts.get("bump_amount") or 0.12), image=opts.get("image"),
                                 logo=opts.get("logo"), title=opts.get("title", ""), artist=opts.get("artist", ""),
                                 rgb=rgb)
    return bump, canvas, [Image.frombytes("RGBA", (canvas, canvas), t) for t in tiles]


def make_style(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, vertical, size=1.0):
    lw, lh = layer_size(w, h)
    data = analyse(audio_path, fps, start, dur, n_frames)
    return STYLES[kind](lw, lh, fps, rgb, vertical, data, size)


def feeder(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, opts, vertical, spin_period=12.0):
    """Returns (layer_w, layer_h, feed) where feed(write) writes n_frames RGBA frames for ffmpeg."""
    style = make_style(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, vertical, opts.get("vis_size") or 1.0)
    el = _element(opts, style.w, style.h, vertical)
    if el:
        style.center = max(0.5, min(1.8, float(opts.get("center_size") or 1)))

    def feed(write):
        for i in range(n_frames):
            fr = style.frame(i)
            if el:
                bump, canvas, tiles = el
                tile = tiles[int(round(float(style.data["bass"][i]) * (len(tiles) - 1)))]
                if bump == "disc":
                    tile = tile.rotate(-360.0 * (i / fps) / spin_period, resample=Image.BILINEAR)
                fr.alpha_composite(tile, (int(style.cx - canvas / 2), int(style.cy - canvas / 2)))
            write(fr.tobytes())
    return style.w, style.h, feed


def still(kind, w, h, rgb, opts, vertical, seconds=1.2, fps=30):
    """One representative frame (for the instant preview), driven by a made-up groove."""
    n = int(seconds * fps)
    t = np.arange(n) / fps
    beat = np.exp(-(t % 0.5) * 7)
    shape = np.exp(-np.linspace(0, 3, 48)) * 0.75 + 0.25
    spec = np.clip(shape[None, :] * (0.55 + 0.45 * np.sin(np.arange(48)[None, :] * 0.9 + t[:, None] * 5) ** 2)
                   * (0.7 + 0.3 * beat[:, None]), 0, 1).astype(np.float32)
    data = {"spec": spec, "bass": beat.astype(np.float32), "energy": np.full(n, 0.7, np.float32),
            "onset": (t % 0.5) < (1 / fps), "wave": (np.sin(np.linspace(0, 40, 256))[None, :] *
                                                    np.sin(np.linspace(0, 7, 256))[None, :] * beat[:, None]).astype(np.float32)}
    lw, lh = layer_size(w, h)
    style = STYLES[kind](lw, lh, fps, rgb, vertical, data, opts.get("vis_size") or 1.0)
    el = _element(opts, lw, lh, vertical)
    if el:
        style.center = max(0.5, min(1.8, float(opts.get("center_size") or 1)))
    for i in range(n - 1):  # let particles and tunnels fill in
        if kind in ("particles", "tunnel", "synthwave", "starfield", "mystify", "pipes"):
            style.frame(i)
    fr = style.frame(n - 1)
    if el:
        bump, canvas, tiles = el
        fr.alpha_composite(tiles[len(tiles) // 2], (int(style.cx - canvas / 2), int(style.cy - canvas / 2)))
    return fr.resize((w, h), Image.BILINEAR) if (lw, lh) != (w, h) else fr

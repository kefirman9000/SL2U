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
         "geometric": "Geometric shapes", "tunnel": "Tunnel", "synthwave": "Synthwave grid"}
# styles built around a centre element: they get the cover in the middle if no bump is chosen
CENTERED = ("ring", "ringwave", "particles", "geometric", "tunnel")
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
    def __init__(self, w, h, fps, rgb, vertical, data):
        self.w, self.h, self.fps, self.rgb, self.data = w, h, fps, rgb, data
        self.S = min(w, h)
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
        r0 = S * (0.31 + 0.025 * bass)
        rot = t * 0.08
        width = 2 * math.pi * r0 / n * 0.55
        for k, v in enumerate(vals):
            a = rot + 2 * math.pi * k / n - math.pi / 2
            ln = S * (0.012 + v * (0.13 + 0.07 * energy))
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
            r0 = S * (0.32 + 0.02 * bass + 0.015 * layer)
            amp = S * (0.06 + 0.07 * energy) * scale
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
            r = S * rng.uniform(0.2, 0.26, n_new)
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
            r = S * rad * (1 + 0.18 * v + 0.05 * bass)
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


STYLES = {"ring": Ring, "ringwave": RingWave, "particles": Particles, "geometric": Geometric, "tunnel": Tunnel,
          "synthwave": Synthwave}


# ---------------------------------------------------------------- the layer ffmpeg reads

def _element(opts, lw, lh, vertical):
    """Pre-rendered bump tiles at layer scale, or None."""
    bump = opts.get("bump", "none")
    if bump == "none":
        return None
    canvas = int(min(lw, lh) * 0.56) // 2 * 2  # same proportion in vertical videos, so rings clear it
    rgb = reactive.pick_color(opts.get("vis_color", "white"), opts.get("image"))
    tiles = reactive.bump_frames(bump, canvas, float(opts.get("bump_amount") or 0.12), image=opts.get("image"),
                                 logo=opts.get("logo"), title=opts.get("title", ""), artist=opts.get("artist", ""),
                                 rgb=rgb)
    return bump, canvas, [Image.frombytes("RGBA", (canvas, canvas), t) for t in tiles]


def make_style(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, vertical):
    lw, lh = layer_size(w, h)
    data = analyse(audio_path, fps, start, dur, n_frames)
    return STYLES[kind](lw, lh, fps, rgb, vertical, data)


def feeder(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, opts, vertical, spin_period=12.0):
    """Returns (layer_w, layer_h, feed) where feed(write) writes n_frames RGBA frames for ffmpeg."""
    style = make_style(kind, audio_path, w, h, fps, start, dur, n_frames, rgb, vertical)
    el = _element(opts, style.w, style.h, vertical)

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
    style = STYLES[kind](lw, lh, fps, rgb, vertical, data)
    for i in range(n - 1):  # let particles and tunnels fill in
        if kind in ("particles", "tunnel", "synthwave"):
            style.frame(i)
    fr = style.frame(n - 1)
    el = _element(opts, lw, lh, vertical)
    if el:
        bump, canvas, tiles = el
        fr.alpha_composite(tiles[len(tiles) // 2], (int(style.cx - canvas / 2), int(style.cy - canvas / 2)))
    return fr.resize((w, h), Image.BILINEAR) if (lw, lh) != (w, h) else fr

"""Effects: beat effects on the background, and overlay layers (weather, light, VHS).

Beat effects: the background zooms in a little on each kick ("punch"), shakes, flashes, and its
colour warms up in the loud parts. ffmpeg can't read our beat data directly, so we write a small
command file (sendcmd) that sets the crop / brightness / hue for every frame.

Overlays: rain, snow, dust, light leaks and a lens flare are drawn once as a seamless loop on
black, cached, and "screen"-blended over the video (black = no change, light = adds light).
VHS is a filter chain (colour bleed, noise, scan lines, soft picture).
"""
import hashlib
import math
import os
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from . import ff

OVERLAYS = {"rain": "Rain", "snow": "Snow", "dust": "Floating dust", "leaks": "Light leaks", "flare": "Lens flare",
            "vhs": "VHS tape"}
CACHE_DIR = os.path.join(tempfile.gettempdir(), "releasekit_cache")  # app.py points this at the data folder
LOOP_SECONDS = 8  # overlay loops repeat every 8 s, without a visible jump


def active(opts):
    return bool(float(opts.get("fx_zoom") or 0) or float(opts.get("fx_shake") or 0)
                or float(opts.get("fx_flash") or 0) or opts.get("fx_color"))


# ---------------------------------------------------------------- beat effects (background only)

def beat_filter(opts, env, w, h, fps, tmpdir, grid=(120.0, 0.0)):
    """ffmpeg filters (to append to the background chain).

    Zoom punch and shake follow the song's beat grid (grid = (bpm, first beat)) as zoompan formulas,
    so the picture size never changes mid-stream. Flash and colour follow the actual kick drum
    (`env`, 0..1 per frame) through a sendcmd file."""
    if not active(opts) or env is None or not len(env):
        return ""
    zoom = max(0.0, min(0.25, float(opts.get("fx_zoom") or 0)))
    shake = max(0.0, min(1.0, float(opts.get("fx_shake") or 0)))
    flash = max(0.0, min(1.0, float(opts.get("fx_flash") or 0)))
    colour = bool(opts.get("fx_color"))
    out = ""
    if zoom or shake:
        bpm, phase = grid
        beat = 60.0 / max(40.0, float(bpm))
        # 1 right on each beat, falling off quickly; mod() of a positive number, so offset by whole beats
        p = f"exp(-9*mod(in/{fps}-{phase:.4f}+{beat * 64:.4f}\\,{beat:.5f}))"
        base = 1.0 + (0.035 if shake else 0.0)  # room to shake without showing the edges
        z = f"{base:.3f}*(1+{zoom:.3f}*{p})"
        sx = f"iw*{0.012 * shake:.4f}*{p}*sin(in*2.31)" if shake else "0"
        sy = f"ih*{0.012 * shake:.4f}*{p}*cos(in*1.73)" if shake else "0"
        out += (f",zoompan=z='{z}':x='iw/2-(iw/zoom/2)+{sx}':y='ih/2-(ih/zoom/2)+{sy}'"
                f":d=1:s={w}x{h}:fps={fps}")
    if flash or colour:
        env = np.asarray(env, dtype=np.float32)
        k = max(1, int(fps * 3))  # loudness of the section (about 3 s): verse low, chorus high
        slow = np.convolve(env, np.ones(k) / k, mode="same")
        lo, hi = np.percentile(slow, 10), np.percentile(slow, 95)
        section = np.clip((slow - lo) / max(hi - lo, 1e-6), 0, 1)
        path = os.path.join(tmpdir, "beatfx.cmd")
        last = None
        with open(path, "w", encoding="utf-8") as f:
            for i, e in enumerate(env):
                b = round(flash * 0.3 * max(0.0, (float(e) - 0.55) / 0.45) ** 2, 3) if flash else 0.0
                hue = round(28 * float(section[i])) if colour else 0
                sat = round(1 + 0.35 * float(section[i]), 2) if colour else 1.0
                cur = (b, hue, sat)
                if cur != last:
                    f.write(f"{i / fps:.4f} eq@bfx brightness {b}, hue@bfx h {hue}, hue@bfx s {sat};\n")
                    last = cur
        out += f",sendcmd=f='{ff.esc_path(path)}',eq@bfx=brightness=0,hue@bfx=h=0:s=1"
    return out


# ---------------------------------------------------------------- overlay loops (drawn once, cached)

def _rng(seed):
    return np.random.default_rng(seed)


def _draw_rain(n, w, h, fps, T):
    r = _rng(3)
    N = int(w * h / 4500)
    x0 = r.uniform(0, w * 1.2, N) - w * 0.1
    y0 = r.uniform(0, h, N)
    laps = r.integers(2, 5, N)  # whole screen-heights per loop: the loop has no jump
    length = r.uniform(0.03, 0.07, N) * h
    bright = r.uniform(70, 170, N)
    for i in range(n):
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        p = i / n
        for k in range(N):
            y = (y0[k] + p * laps[k] * (h + length[k])) % (h + length[k]) - length[k]
            x = x0[k] + (y - y0[k]) * 0.12
            c = int(bright[k])
            d.line([(x, y), (x + length[k] * 0.12, y + length[k])], fill=(c, c, int(c * 1.1) if c < 230 else c),
                   width=max(1, w // 900))
        yield im


def _draw_snow(n, w, h, fps, T):
    r = _rng(5)
    N = int(w * h / 9000)
    x0, y0 = r.uniform(0, w, N), r.uniform(0, h, N)
    laps = r.integers(1, 3, N)
    size = r.uniform(0.0015, 0.0045, N) * w
    sway = r.uniform(5, 25, N) * w / 1280
    wob = r.integers(1, 4, N)
    for i in range(n):
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        p = i / n
        for k in range(N):
            y = (y0[k] + p * laps[k] * (h + 20)) % (h + 20) - 10
            x = x0[k] + sway[k] * math.sin(2 * math.pi * (p * wob[k] + k * 0.37))
            s = size[k]
            c = int(150 + 100 * s / (0.0045 * w))
            d.ellipse((x - s, y - s, x + s, y + s), fill=(c, c, c))
        yield im.filter(ImageFilter.GaussianBlur(max(0.6, w / 1600)))


def _draw_dust(n, w, h, fps, T):
    r = _rng(7)
    N = int(w * h / 14000)
    cx, cy = r.uniform(0, w, N), r.uniform(0, h, N)
    rad = r.uniform(10, 60, N) * w / 1280
    per = r.integers(1, 3, N)
    tw = r.integers(1, 4, N)
    size = r.uniform(0.8, 2.6, N) * w / 1280
    ph = r.uniform(0, 2 * math.pi, N)
    for i in range(n):
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        a = 2 * math.pi * i / n
        for k in range(N):
            x = cx[k] + rad[k] * math.cos(a * per[k] + ph[k])
            y = cy[k] + rad[k] * 0.6 * math.sin(a * per[k] + ph[k])
            c = int(60 + 140 * (0.5 + 0.5 * math.sin(a * tw[k] + ph[k] * 3)))
            s = size[k]
            d.ellipse((x - s, y - s, x + s, y + s), fill=(c, int(c * 0.95), int(c * 0.85)))
        yield im.filter(ImageFilter.GaussianBlur(max(0.5, w / 2200)))


def _draw_leaks(n, w, h, fps, T):
    blobs = [((255, 120, 40), 0.55, 1, 0.0), ((255, 60, 120), 0.45, 2, 1.7), ((255, 200, 90), 0.4, 1, 3.1)]
    sw, sh = max(64, w // 8), max(36, h // 8)  # tiny and very blurred: soft light, cheap to draw
    for i in range(n):
        im = Image.new("RGB", (sw, sh))
        d = ImageDraw.Draw(im)
        a = 2 * math.pi * i / n
        for col, rad, per, ph in blobs:
            x = sw * (0.15 + 0.7 * (0.5 + 0.5 * math.sin(a * per + ph)))
            y = sh * (0.1 + 0.3 * (0.5 + 0.5 * math.cos(a * per * 2 + ph)))
            rr = rad * sw * (0.8 + 0.2 * math.sin(a * 2 + ph))
            strength = 0.55 + 0.45 * math.sin(a * per + ph * 2)
            c = tuple(int(v * 0.6 * max(0.0, strength)) for v in col)  # soft: light, not paint
            d.ellipse((x - rr, y - rr * 0.7, x + rr, y + rr * 0.7), fill=c)
        im = im.filter(ImageFilter.GaussianBlur(sw / 10)).resize((w, h), Image.BILINEAR)
        yield im


def _draw_flare(n, w, h, fps, T):
    u = min(w, h)
    for i in range(n):
        a = 2 * math.pi * i / n
        sx, sy = w * (0.78 + 0.05 * math.sin(a)), h * (0.2 + 0.04 * math.cos(a))
        cx, cy = w / 2, h / 2
        im = Image.new("RGB", (w, h))
        d = ImageDraw.Draw(im)
        pulse = 0.85 + 0.15 * math.sin(a * 2)
        for k, (pos, rad, col) in enumerate([(0, 0.09, (255, 230, 190)), (0.35, 0.03, (120, 180, 255)),
                                             (0.6, 0.05, (255, 140, 80)), (0.9, 0.02, (160, 255, 200)),
                                             (1.25, 0.07, (120, 120, 255)), (1.5, 0.035, (255, 200, 120))]):
            x, y = sx + (cx - sx) * pos * 2, sy + (cy - sy) * pos * 2
            rr = rad * u * (pulse if k == 0 else 1)
            c = tuple(int(v * (1.0 if k == 0 else 0.35)) for v in col)
            d.ellipse((x - rr, y - rr, x + rr, y + rr), fill=c)
        d.line([(sx - u * 0.45, sy), (sx + u * 0.45, sy)], fill=(140, 170, 255), width=max(2, u // 300))
        yield im.filter(ImageFilter.GaussianBlur(u / 70))


_DRAW = {"rain": _draw_rain, "snow": _draw_snow, "dust": _draw_dust, "leaks": _draw_leaks, "flare": _draw_flare}


def overlay_loop(kind, w, h, fps, cancel=None):
    """A cached seamless loop (LOOP_SECONDS long) of the overlay on black, at w x h."""
    lw, lh = (w, h) if min(w, h) <= 720 else (int(w * 720 / min(w, h)) // 2 * 2, int(h * 720 / min(w, h)) // 2 * 2)
    key = hashlib.sha1(f"{kind}|{lw}x{lh}|{fps}|v2".encode()).hexdigest()[:12]
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"fx_{kind}_{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out
    n = int(LOOP_SECONDS * fps)
    frames = _DRAW[kind](n, lw, lh, fps, LOOP_SECONDS)

    def feed(write):
        for im in frames:
            write(im.tobytes())
    tmp = out + ".part.mp4"
    ff.run(["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{lw}x{lh}", "-framerate", str(fps), "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv444p", "-g", str(fps),
            "-frames:v", str(n), tmp], cancel=cancel, feed=feed)
    os.replace(tmp, out)
    return out


def overlay_filters(opts, cur, w, h, fps, cancel=None):
    """Filter-graph lines that blend the chosen overlays onto stream `cur`. Returns (lines, new label)."""
    want = opts.get("overlays") or []
    if isinstance(want, str):
        want = [w.strip() for w in want.split(",")]
    kinds = [k for k in want if k in OVERLAYS]
    strength = max(0.1, min(1.0, float(opts.get("overlay_amount") or 0.7)))
    lines = []
    for i, kind in enumerate(kinds):
        if kind == "vhs":
            continue
        src = overlay_loop(kind, w, h, fps, cancel)
        lines += [f"movie='{ff.esc_path(src)}':loop=0,setpts=N/({fps}*TB),fps={fps},scale={w}:{h},format=gbrp[ov{i}]",
                  f"[{cur}]format=gbrp[ob{i}]",
                  f"[ob{i}][ov{i}]blend=all_mode=screen:all_opacity={strength:.2f}:shortest=1,format=yuv420p[oo{i}]"]
        cur = f"oo{i}"
    if "vhs" in kinds:
        g = max(2, h // 240)
        lines.append(f"[{cur}]rgbashift=rh=-{max(1, w // 500)}:bh={max(1, w // 500)},"
                     f"noise=alls={int(8 + 10 * strength)}:allf=t,eq=saturation={1.1 + 0.2 * strength:.2f}:contrast=1.05,"
                     f"drawgrid=w=iw:h={g}:t=1:c=black@{0.06 + 0.1 * strength:.2f},"
                     f"gblur=sigma=0.6[vhs]")
        cur = "vhs"
    return lines, cur

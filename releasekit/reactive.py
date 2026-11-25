"""Audio-reactive layers: bar/wave/scope visualizers and a logo, cover or text that bumps to the bass.

The visualizers are ffmpeg filters. The bump layer is pre-rendered in a handful of sizes
(with a glow that grows with the beat) and streamed into ffmpeg frame by frame, picking the
size from the bass level of that frame.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import art, audio, ff

COLORS = {"white": (255, 255, 255), "green": (57, 255, 20), "cyan": (40, 220, 255), "pink": (255, 70, 170),
          "gold": (255, 200, 60), "red": (255, 60, 60)}

VISUALIZERS = {"none": "None", "bars": "Frequency bars", "mirror": "Mirrored bars", "waves": "Waveform",
               "line": "Line", "scope": "Circle scope", "spectrum": "Scrolling spectrum",
               # drawn frame by frame (see drawn.py)
               "ring": "Radial ring", "ringwave": "Radial waveform", "particles": "Particles",
               "geometric": "Geometric shapes", "tunnel": "Tunnel", "synthwave": "Synthwave grid",
               "starfield": "Starfield", "mystify": "Mystify", "pipes": "3D Pipes", "maze": "3D Maze"}
BOTTOM = ("bars", "mirror", "waves", "line", "spectrum")  # visualizers drawn along the bottom edge
BUMPS = {"none": "Nothing", "cover": "Cover art", "disc": "Spinning disc (cover)", "logo": "My logo (PNG)",
         "text": "Artist & title text"}


def pick_color(name, image=None):
    if isinstance(name, str) and len(name) == 7 and name.startswith("#"):  # custom colour from the colour picker
        try:
            return tuple(int(name[i:i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    if name == "cover" and image:
        try:
            pal = art.palette(art.open_rgb(image), 6)
            # most saturated, reasonably bright colour from the art
            def score(c):
                mx, mn = max(c), min(c)
                return (mx - mn) * 2 + mx
            c = max(pal, key=score)
            return tuple(int(v) for v in c)
        except Exception:
            pass
    return COLORS.get(name, COLORS["white"])


def _tint(rgb):
    r, g, b = rgb
    # grey -> boost (so dim high-frequency bars survive) -> tint
    return (f"hue=s=0,lutrgb=r='min(255\\,val*3)*{r}/255':g='min(255\\,val*3)*{g}/255'"
            f":b='min(255\\,val*3)*{b}/255'")


def vis_chain(kind, w, h, fps, vertical=False, rgb=(255, 255, 255), size=1.0, pos="bottom", lift=False):
    """ffmpeg filter text that turns an audio stream into a transparent visualizer layer, and where to put it.
    size: 0.6-1.6 times the normal size. pos: bottom, middle or top (bar and wave styles)."""
    hexc = "0x%02x%02x%02x" % rgb
    ev = lambda v: max(16, int(v) // 2 * 2)  # filters want even sizes
    size = max(0.5, min(1.8, float(size or 1)))
    vw = ev(min(w * 0.96, w * (0.86 if vertical else 0.7) * (1 + (size - 1) * 0.6)))
    vh = ev(h * (0.12 if vertical else 0.2) * size)
    bottom = int(h * (0.1 if vertical else 0.08))
    if lift:  # "Now playing" lives along the bottom: sit above it
        bottom = max(bottom, int(min(w, h) * 0.16))
    place = {"middle": "(W-w)/2:(H-h)/2", "top": f"(W-w)/2:{int(h * 0.16)}"}.get(pos, f"(W-w)/2:H-h-{bottom}")
    if kind in ("bars", "mirror"):
        vh = ev(h * (0.14 if vertical else 0.22) * (0.5 if kind == "mirror" else 1) * size)
        base = (f"showcqt=s={vw}x{vh}:r={fps}:sono_h=0:axis_h=0:bar_g=2:basefreq=40:endfreq=12000,"
                f"{_tint(rgb)},format=rgba,colorkey=black:0.03")
        if kind == "mirror":
            base += (",split=2[mt][mb];[mb]vflip,colorchannelmixer=aa=0.35[mbf];[mt][mbf]vstack")
            return base, place
        return base, place
    # waveforms need about one column per audio sample in a frame, or they break up into dots
    # on small videos: draw them wide, then scale down
    ww = vw if vw >= 1200 else 1600
    fit = "" if ww == vw else f",scale={vw}:{vh}:flags=area"
    if kind == "line":
        return f"showwaves=s={ww}x{vh}:mode=line:rate={fps}:colors={hexc}{fit}", place
    if kind == "spectrum":
        return (f"showspectrum=s={vw}x{vh}:mode=combined:slide=scroll:color=intensity:scale=log,fps={fps}",
                place)
    if kind == "scope":
        s = ev(min(w, h) * (0.62 if not vertical else 0.8) * min(size, 1.4))
        return (f"avectorscope=s={s}x{s}:r={fps}:draw=line:zoom=1.6:rc=40:gc=160:bc=80:rf=12:gf=8:bf=10,"
                f"{_tint(rgb)},format=rgba,colorkey=black:0.03"), "(W-w)/2:(H-h)/2"
    return f"showwaves=s={ww}x{vh}:mode=cline:rate={fps}:colors={hexc}{fit}", place


# ---------------------------------------------------------------- bass envelope

def bass_envelope(audio_path, fps, start=0.0, duration=None, n_frames=None):
    """0..1 per video frame: low-frequency energy with a fast attack and a slower release."""
    sr = 11025
    x = audio._pcm(audio_path, sr, max_seconds=(start + duration) if duration else None)
    x = x[int(start * sr):]
    if duration:
        x = x[: int(duration * sr)]
    if len(x) == 0:
        return np.zeros(n_frames or 1)
    k = int(sr / 140)  # ~140 Hz moving-average low-pass
    lp = np.convolve(x, np.ones(k) / k, mode="same")
    n = n_frames or int(len(x) / sr * fps)
    hop = len(lp) / max(n, 1)
    rms = np.array([np.sqrt(np.mean(lp[int(i * hop):int((i + 1) * hop) or 1] ** 2) + 1e-12) for i in range(n)])
    lo, hi = np.percentile(rms, 15), np.percentile(rms, 97)
    env = np.clip((rms - lo) / max(hi - lo, 1e-6), 0, 1) ** 1.4  # quiet parts rest, kicks hit the top
    out = np.zeros_like(env)
    a_up, a_down = 0.65, 0.12
    for i, v in enumerate(env):
        p = out[i - 1] if i else v
        out[i] = p + (v - p) * (a_up if v > p else a_down)
    return out


# ---------------------------------------------------------------- bump layer

def load_logo(path):
    """The logo as RGBA with its empty see-through margins cut off, so the size slider sizes the logo itself."""
    im = Image.open(path).convert("RGBA")
    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return im.crop(box) if box else im


def element_canvas(kind, w, h, vertical=False, scale=None, logo=None, amount=0.12):
    """(width, height) of the tile the bumping element is drawn on, for a w x h video.
    scale: the size slider, 0.5-3.0. Logos get a tile shaped like the logo, so wide logos can be big."""
    k = max(0.5, min(3.0, float(scale or 1)))
    room = (1 + float(amount or 0)) * 1.25  # space for the pulse and the glow
    even = lambda v: max(16, int(v) // 2 * 2)
    if kind == "logo" and logo:
        try:
            with Image.open(logo) as im0:
                lw, lh = load_logo(logo).size if im0.mode in ("RGBA", "LA", "P") else im0.size
        except Exception:
            lw, lh = 1, 1
        ar = lw / max(1, lh)
        longest = min(w, h) * 0.42 * k
        ew, eh = (longest, longest / ar) if ar >= 1 else (longest * ar, longest)
        fit = min(1.0, w * 0.96 / ew, h * 0.96 / eh)  # never bigger than the picture
        return even(ew * fit * room), even(eh * fit * room)
    side = min(min(w, h) * (0.56 if not vertical else 0.8) * k, min(w, h) * 1.3)
    return even(side), even(side)


def _element(kind, size, image=None, logo=None, title="", artist="", rgb=(255, 255, 255)):
    """The thing that bumps, as an RGBA image that fits in size (an int for a square, or (w, h))."""
    bw, bh = size if isinstance(size, tuple) else (size, size)
    if kind == "logo" and logo:
        im = load_logo(logo)
        f = min(bw / im.width, bh / im.height)  # scale up too: a small PNG still fills the size you chose
        return im.resize((max(1, int(im.width * f)), max(1, int(im.height * f))), Image.LANCZOS)
    size = min(bw, bh)
    if kind == "text":
        im = Image.new("RGBA", (size * 2, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        f_t = art.load_font(int(size * 0.2))
        while f_t.size > 12 and d.textlength(title or " ", font=f_t) > size * 1.9:
            f_t = art.load_font(int(f_t.size * 0.9))
        f_a = art.load_font(int(f_t.size * 0.5))
        aw = d.textlength(artist.upper(), font=f_a)
        tw = d.textlength(title, font=f_t)
        d.text(((size * 2 - aw) / 2, size * 0.28), artist.upper(), font=f_a, fill=rgb + (255,))
        d.text(((size * 2 - tw) / 2, size * 0.28 + f_a.size * 1.3), title, font=f_t, fill=(255, 255, 255, 255))
        return im.crop(im.getbbox() or (0, 0, 1, 1))
    src = art.center_square(art.open_rgb(image), size).convert("RGBA")
    if kind == "disc":
        mask = Image.new("L", (size, size), 0)
        md = ImageDraw.Draw(mask)
        md.ellipse((0, 0, size - 1, size - 1), fill=255)
        hole = size // 14
        md.ellipse((size / 2 - hole, size / 2 - hole, size / 2 + hole, size / 2 + hole), fill=0)
        src.putalpha(mask)
        d = ImageDraw.Draw(src)
        for r in range(int(size * 0.18), size // 2, max(3, size // 60)):  # faint grooves
            d.ellipse((size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r), outline=(0, 0, 0, 40), width=1)
        return src
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 24, fill=255)
    src.putalpha(mask)
    return src


def bump_frames(kind, canvas, amount=0.12, levels=24, image=None, logo=None, title="", artist="",
                rgb=(255, 255, 255)):
    """Pre-render `levels` sizes of the element (with growing glow) on a canvas RGBA tile.
    canvas: an int (square) or (width, height) from element_canvas()."""
    cw, ch = canvas if isinstance(canvas, tuple) else (canvas, canvas)
    if cw * ch > 900 * 900:
        levels = min(levels, 12)  # big tiles: fewer steps keeps memory in check
    base = (max(1, int(cw / (1 + amount) / 1.25)), max(1, int(ch / (1 + amount) / 1.25)))
    el = _element(kind, base if kind == "logo" else min(base), image, logo, title, artist, rgb)
    tiles = []
    m = min(cw, ch)
    for i in range(levels):
        t = i / max(1, levels - 1)
        s = 1 + amount * t
        e = el.resize((max(1, int(el.width * s)), max(1, int(el.height * s))), Image.LANCZOS)
        tile = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        glow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        alpha = e.split()[-1]
        col = Image.new("RGBA", e.size, rgb + (0,))
        col.putalpha(alpha.point(lambda a: int(a * (0.25 + 0.6 * t))))
        x, y = (cw - e.width) // 2, (ch - e.height) // 2
        glow.paste(col, (x, y), col)
        glow = glow.filter(ImageFilter.GaussianBlur(m * (0.025 + 0.03 * t)))
        shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        sh = Image.new("RGBA", e.size, (0, 0, 0, 0))
        sh.putalpha(alpha.point(lambda a: int(a * 0.55)))
        shadow.paste(sh, (x + m // 80, y + m // 60), sh)
        shadow = shadow.filter(ImageFilter.GaussianBlur(m // 50))
        tile = Image.alpha_composite(Image.alpha_composite(glow, shadow), tile)
        tile.alpha_composite(e, (x, y))
        tiles.append(tile.tobytes())
    return tiles


def frame_feeder(env, tiles):
    """Returns a function that writes one tile per frame into ffmpeg's stdin."""
    n = len(tiles)
    idx = np.clip((env * (n - 1)).round().astype(int), 0, n - 1)

    def feed(write):
        for i in idx:
            write(tiles[i])
    return feed


def beat_grid(audio_path, start=0.0, dur=None):
    """(beats per minute, time of the first beat) from the low end, for cutting clips on the beat."""
    fps = 100
    try:
        total = dur or (ff.duration(audio_path) - start)
        n = int(min(total, 90) * fps)  # 90 s is plenty to find the tempo
        env = bass_envelope(audio_path, fps, start, min(total, 90), n)
    except Exception:
        return 120.0, 0.0
    if len(env) < fps * 4:
        return 120.0, 0.0
    onset = np.maximum(0, np.diff(env, prepend=env[0]))
    onset = onset - onset.mean()
    best, best_lag = -1e9, 50
    for bpm in np.arange(70, 181, 0.5):
        lag = 60 * fps / bpm
        m = len(onset) - int(np.ceil(lag * 4)) - 1
        a = onset[:m]
        score = sum(float(np.dot(a, onset[int(round(lag * k)): int(round(lag * k)) + m])) for k in (1, 2, 4))
        if score > best:
            best, best_lag = score, lag
    bpm = 60 * fps / best_lag
    if bpm < 90:
        bpm *= 2
    # fine-tune: line up a comb of beats over the whole stretch (a small error adds up over a song)
    t = np.arange(len(onset)) / fps
    weight = np.maximum(0, onset)
    best_r = -1.0
    for cand in np.arange(bpm - 2, bpm + 2.001, 0.02):
        ph = 2 * np.pi * t * cand / 60
        r = float(np.abs(np.sum(weight * np.exp(1j * ph))))
        if r > best_r:
            best_r, fine = r, cand
    bpm = fine
    lag = 60 * fps / bpm
    raw = np.maximum(0, np.diff(env, prepend=env[0]))
    phases = [raw[int(p)::max(1, int(round(lag)))].sum() for p in range(int(lag))]
    return float(bpm), float(int(np.argmax(phases)) / fps)

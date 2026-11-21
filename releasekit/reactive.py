"""Audio-reactive layers: bar/wave/scope visualizers and a logo, cover or text that bumps to the bass.

The visualizers are ffmpeg filters. The bump layer is pre-rendered in a handful of sizes
(with a glow that grows with the beat) and streamed into ffmpeg frame by frame, picking the
size from the bass level of that frame.
"""
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from . import art, audio

COLORS = {"white": (255, 255, 255), "green": (57, 255, 20), "cyan": (40, 220, 255), "pink": (255, 70, 170),
          "gold": (255, 200, 60), "red": (255, 60, 60)}

VISUALIZERS = {"none": "None", "bars": "Frequency bars", "mirror": "Mirrored bars", "waves": "Waveform",
               "line": "Line", "scope": "Circle scope", "spectrum": "Scrolling spectrum"}
BUMPS = {"none": "Nothing", "cover": "Cover art", "disc": "Spinning disc (cover)", "logo": "My logo (PNG)",
         "text": "Artist & title text"}


def pick_color(name, image=None):
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


def vis_chain(kind, w, h, fps, vertical=False, rgb=(255, 255, 255)):
    """ffmpeg filter text that turns an audio stream into a transparent visualizer layer, and where to put it."""
    hexc = "0x%02x%02x%02x" % rgb
    ev = lambda v: int(v) // 2 * 2  # filters want even sizes
    vw = ev(w * (0.86 if vertical else 0.7))
    vh = ev(h * (0.12 if vertical else 0.2))
    bottom = int(h * (0.1 if vertical else 0.08))
    if kind in ("bars", "mirror"):
        vh = ev(h * (0.14 if vertical else 0.22) * (0.5 if kind == "mirror" else 1))
        base = (f"showcqt=s={vw}x{vh}:r={fps}:sono_h=0:axis_h=0:bar_g=2:basefreq=40:endfreq=12000,"
                f"{_tint(rgb)},format=rgba,colorkey=black:0.03")
        if kind == "mirror":
            base += (",split=2[mt][mb];[mb]vflip,colorchannelmixer=aa=0.35[mbf];[mt][mbf]vstack")
            return base, f"(W-w)/2:H-h-{bottom}"
        return base, f"(W-w)/2:H-h-{bottom}"
    if kind == "line":
        return f"showwaves=s={vw}x{vh}:mode=line:rate={fps}:colors={hexc}", f"(W-w)/2:H-h-{bottom}"
    if kind == "spectrum":
        return (f"showspectrum=s={vw}x{vh}:mode=combined:slide=scroll:color=intensity:scale=log,fps={fps}",
                f"(W-w)/2:H-h-{bottom}")
    if kind == "scope":
        s = ev(min(w, h) * (0.62 if not vertical else 0.8))
        return (f"avectorscope=s={s}x{s}:r={fps}:draw=line:zoom=1.6:rc=40:gc=160:bc=80:rf=12:gf=8:bf=10,"
                f"{_tint(rgb)},format=rgba,colorkey=black:0.03"), "(W-w)/2:(H-h)/2"
    return f"showwaves=s={vw}x{vh}:mode=cline:rate={fps}:colors={hexc}", f"(W-w)/2:H-h-{bottom}"


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

def _element(kind, size, image=None, logo=None, title="", artist="", rgb=(255, 255, 255)):
    """The thing that bumps, as an RGBA image that fits in size x size."""
    if kind == "logo" and logo:
        im = Image.open(logo).convert("RGBA")
        im.thumbnail((size, size), Image.LANCZOS)
        return im
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
    """Pre-render `levels` sizes of the element (with growing glow) on a canvas x canvas RGBA tile."""
    base = int(canvas / (1 + amount) / 1.25)
    el = _element(kind, base, image, logo, title, artist, rgb)
    tiles = []
    for i in range(levels):
        t = i / (levels - 1)
        s = 1 + amount * t
        e = el.resize((max(1, int(el.width * s)), max(1, int(el.height * s))), Image.LANCZOS)
        tile = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        glow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        alpha = e.split()[-1]
        col = Image.new("RGBA", e.size, rgb + (0,))
        col.putalpha(alpha.point(lambda a: int(a * (0.25 + 0.6 * t))))
        x, y = (canvas - e.width) // 2, (canvas - e.height) // 2
        glow.paste(col, (x, y), col)
        glow = glow.filter(ImageFilter.GaussianBlur(canvas * (0.025 + 0.03 * t)))
        shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        sh = Image.new("RGBA", e.size, (0, 0, 0, 0))
        sh.putalpha(alpha.point(lambda a: int(a * 0.55)))
        shadow.paste(sh, (x + canvas // 80, y + canvas // 60), sh)
        shadow = shadow.filter(ImageFilter.GaussianBlur(canvas // 50))
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

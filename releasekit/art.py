"""Album art: photo folders, smart crops, generated art, text, platform variants."""
import glob
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageStat

IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".bmp")

FONT_CANDIDATES = {
    "sans": ["Inter-Bold.otf", "Inter-SemiBold.otf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf", "arialbd.ttf",
             "Helvetica.ttc", "LiberationSans-Bold.ttf"],
    "serif": ["DejaVuSerif-Bold.ttf", "Georgia Bold.ttf", "georgiab.ttf", "Times New Roman Bold.ttf",
              "LiberationSerif-Bold.ttf"],
    "mono": ["DejaVuSansMono-Bold.ttf", "Courier New Bold.ttf", "courbd.ttf", "LiberationMono-Bold.ttf"],
}
FONT_DIRS = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts"),
             "/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts"),
             "C:/Windows/Fonts"]
_font_cache = {}


def find_font(style="sans"):
    if style in _font_cache:
        return _font_cache[style]
    if style and os.path.isfile(style):
        _font_cache[style] = style
        return style
    for name in FONT_CANDIDATES.get(style, FONT_CANDIDATES["sans"]):
        for d in FONT_DIRS:
            hits = glob.glob(os.path.join(d, "**", name), recursive=True)
            if hits:
                _font_cache[style] = hits[0]
                return hits[0]
    _font_cache[style] = None
    return None


def load_font(size, style="sans"):
    path = find_font(style)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


# ---------------------------------------------------------------- photo folders

def list_images(folder):
    out = []
    for root, _, files in os.walk(folder):
        for f in sorted(files):
            if f.lower().endswith(IMAGE_EXT) and not f.startswith("."):
                out.append(os.path.join(root, f))
    return out


def open_rgb(path):
    im = Image.open(path)
    im = ImageOps.exif_transpose(im)
    if im.mode != "RGB":
        bg = Image.new("RGB", im.size, (0, 0, 0))
        im = im.convert("RGBA")
        bg.paste(im, mask=im.split()[-1])
        im = bg
    return im


def sharpness(im):
    g = np.asarray(im.convert("L").resize((512, int(512 * im.height / im.width) or 1)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


def score_photos(folder, limit=500):
    """Rank photos by usefulness as cover art: resolution, sharpness, contrast."""
    res = []
    for p in list_images(folder)[:limit]:
        try:
            im = open_rgb(p)
            w, h = im.size
            short = min(w, h)
            sharp = sharpness(im)
            contrast = ImageStat.Stat(im.convert("L")).stddev[0]
            s = min(short / 3000, 1.0) * 50 + min(sharp / 400, 1.0) * 35 + min(contrast / 70, 1.0) * 15
            res.append({"path": p, "width": w, "height": h, "score": round(s, 1),
                        "upscale_needed": short < 3000})
        except Exception as e:  # unreadable file
            res.append({"path": p, "error": str(e), "score": 0})
    res.sort(key=lambda r: -r["score"])
    return res


def thumbnail(path, size=360):
    im = open_rgb(path)
    im.thumbnail((size, size))
    return im


def palette(im, n=5):
    small = im.convert("RGB").resize((120, 120))
    q = small.quantize(colors=n, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()[: n * 3]
    counts = sorted(q.getcolors(), reverse=True)
    return [tuple(pal[i * 3:i * 3 + 3]) for _, i in counts]


# ---------------------------------------------------------------- crops

def smart_square(im, size=3000):
    """Square crop centred on the most detailed region."""
    w, h = im.size
    side = min(w, h)
    if w == h:
        crop = im
    else:
        g = np.asarray(im.convert("L").resize((max(1, w // 8), max(1, h // 8))), dtype=np.float32)
        gx = np.abs(np.diff(g, axis=1))[:-1, :]
        gy = np.abs(np.diff(g, axis=0))[:, :-1]
        detail = gx + gy
        s = side // 8
        if w > h:
            col = detail.sum(axis=0)
            win = np.convolve(col, np.ones(max(1, s - 1)), mode="valid")
            x = int(np.argmax(win)) * 8
            x = max(0, min(w - side, x))
            crop = im.crop((x, 0, x + side, side))
        else:
            row = detail.sum(axis=1)
            win = np.convolve(row, np.ones(max(1, s - 1)), mode="valid")
            y = int(np.argmax(win)) * 8
            y = max(0, min(h - side, y))
            crop = im.crop((0, y, side, y + side))
    return crop.resize((size, size), Image.LANCZOS)


def center_square(im, size=3000):
    return ImageOps.fit(im, (size, size), Image.LANCZOS, centering=(0.5, 0.5))


def blurred_bg(im, w, h, blur=40, dim=0.55):
    small = ImageOps.fit(im, (max(1, w // 4), max(1, h // 4)), Image.LANCZOS)  # blur at low res: fast
    bg = small.filter(ImageFilter.GaussianBlur(max(1, blur // 4))).resize((w, h), Image.BICUBIC)
    return Image.blend(Image.new("RGB", (w, h), (0, 0, 0)), bg, dim)


def blur_fill(im, w, h, blur=40, dim=0.55):
    """Fit image inside w x h over a blurred, darkened copy of itself (no cropping)."""
    bg = blurred_bg(im, w, h, blur, dim)
    fg = im.copy()
    fg.thumbnail((w, h), Image.LANCZOS)
    bg.paste(fg, ((w - fg.width) // 2, (h - fg.height) // 2))
    return bg


# ---------------------------------------------------------------- generated art

def _hsv(h, s, v):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return np.array([r, g, b]) * 255


def generate(style="aurora", size=3000, seed=None, colors=None):
    """Procedural cover art. Styles: aurora, gradient, grain, geometric, sunset, mono."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    base_h = rng.random()
    if colors:
        cols = [np.array(c, dtype=np.float32) for c in colors]
    else:
        cols = [_hsv(base_h + k * rng.uniform(0.08, 0.25), rng.uniform(0.5, 0.95), rng.uniform(0.35, 1.0))
                for k in range(4)]
    s = 600  # render small, upscale smoothly, then add full-res grain
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32) / s

    if style == "gradient":
        ang = rng.uniform(0, math.pi)
        t = (xx * math.cos(ang) + yy * math.sin(ang))
        t = (t - t.min()) / (t.max() - t.min())
        img = cols[0] * (1 - t[..., None]) + cols[1] * t[..., None]
    elif style == "sunset":
        t = yy
        img = cols[0] * (1 - t[..., None]) + cols[1] * t[..., None]
        cx, cy, r = 0.5, rng.uniform(0.45, 0.65), rng.uniform(0.18, 0.3)
        d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        sun = np.clip((r - d) * 60, 0, 1)[..., None]
        img = img * (1 - sun) + cols[2] * sun
        for k in range(6):
            band = ((yy > cy + k * 0.025) & (yy < cy + k * 0.025 + 0.008 + k * 0.002) & (d < r)).astype(np.float32)
            img = img * (1 - band[..., None]) + cols[0] * band[..., None]
    elif style == "geometric":
        img = np.ones((s, s, 3), np.float32) * cols[0] * 0.25
        pil = Image.fromarray(img.astype(np.uint8))
        dr = ImageDraw.Draw(pil, "RGBA")
        for _ in range(rng.randint(5, 11)):
            c = tuple(int(v) for v in cols[rng.randint(1, len(cols) - 1)]) + (rng.randint(120, 230),)
            kind = rng.choice(["circle", "rect", "tri", "ring"])
            x, y, r = rng.uniform(0, s), rng.uniform(0, s), rng.uniform(s * 0.05, s * 0.35)
            if kind == "circle":
                dr.ellipse((x - r, y - r, x + r, y + r), fill=c)
            elif kind == "ring":
                dr.ellipse((x - r, y - r, x + r, y + r), outline=c, width=int(r * 0.15) + 2)
            elif kind == "rect":
                dr.rectangle((x - r, y - r / 2, x + r, y + r / 2), fill=c)
            else:
                dr.polygon([(x, y - r), (x - r, y + r), (x + r, y + r)], fill=c)
        img = np.asarray(pil, dtype=np.float32)
    elif style == "mono":
        v = rng.uniform(0.05, 0.2)
        img = np.ones((s, s, 3), np.float32) * 255 * v
        d = np.sqrt((xx - 0.5) ** 2 + (yy - 0.5) ** 2)
        rings = (np.sin(d * rng.uniform(40, 90)) > 0.92).astype(np.float32)
        img += rings[..., None] * cols[0] * 0.8
    else:  # aurora / grain: soft blobs of colour
        img = np.zeros((s, s, 3), np.float32) + cols[0] * 0.15
        for k in range(rng.randint(4, 7)):
            cx, cy = rng.uniform(-0.1, 1.1), rng.uniform(-0.1, 1.1)
            sx, sy = rng.uniform(0.15, 0.5), rng.uniform(0.1, 0.4)
            g = np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))
            img += g[..., None] * cols[k % len(cols)] * rng.uniform(0.5, 0.9)
        img = img / max(1.0, img.max() / 255)

    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8)).resize((size, size), Image.BICUBIC)
    im = im.filter(ImageFilter.GaussianBlur(2))
    grain_amt = 22 if style == "grain" else 9
    noise = nprng.normal(0, grain_amt, (size, size, 1)).astype(np.float32)
    arr = np.clip(np.asarray(im, dtype=np.float32) + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


# ---------------------------------------------------------------- text

def _contrast_color(im, box):
    region = im.crop(box).convert("L")
    return (20, 20, 20) if ImageStat.Stat(region).mean[0] > 150 else (250, 250, 250)


def add_text(im, title="", artist="", position="bottom", style="sans", color=None, scale=1.0, shadow=True):
    """Overlay title/artist. position: top, bottom, center, bottom-left, top-left."""
    im = im.copy()
    W, H = im.size
    pad = int(W * 0.06)
    t_font = load_font(int(W * 0.075 * scale), style)
    a_font = load_font(int(W * 0.042 * scale), style)
    d = ImageDraw.Draw(im)

    def fit(text, font, maxw):
        size = font.size
        while size > 10 and d.textlength(text, font=font) > maxw:
            size = int(size * 0.92)
            font = load_font(size, style)
        return font

    lines = []
    if artist:
        lines.append((artist.upper() if style == "sans" else artist, fit(artist, a_font, W - 2 * pad)))
    if title:
        lines.append((title, fit(title, t_font, W - 2 * pad)))
    if not lines:
        return im
    heights = [d.textbbox((0, 0), t, font=f)[3] for t, f in lines]
    gap = int(W * 0.015)
    block = sum(heights) + gap * (len(lines) - 1)
    if position.startswith("top"):
        y = pad
    elif position == "center":
        y = (H - block) // 2
    else:
        y = H - pad - block
    left = position.endswith("left")
    col = tuple(color) if color else _contrast_color(im, (0, max(0, y - 10), W, min(H, y + block + 10)))
    shadow_col = (0, 0, 0, 150) if sum(col) > 380 else (255, 255, 255, 110)
    overlay = Image.new("RGBA", im.size, (0, 0, 0, 0))
    shadow_l = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od, sd = ImageDraw.Draw(overlay), ImageDraw.Draw(shadow_l)
    for (text, font), hgt in zip(lines, heights):
        tw = d.textlength(text, font=font)
        x = pad if left else (W - tw) / 2
        if shadow:
            sd.text((x + W * 0.003, y + W * 0.003), text, font=font, fill=shadow_col)
        od.text((x, y), text, font=font, fill=col + (255,))
        y += hgt + gap
    im = im.convert("RGBA")
    if shadow:  # blur only the shadow; keep the letters crisp
        im = Image.alpha_composite(im, shadow_l.filter(ImageFilter.GaussianBlur(max(1, W * 0.003))))
    return Image.alpha_composite(im, overlay).convert("RGB")


def parental_advisory(im):
    """Small generic explicit-content label in the bottom-right corner."""
    im = im.copy()
    W = im.width
    w, h = int(W * 0.16), int(W * 0.1)
    x, y = W - w - int(W * 0.03), im.height - h - int(W * 0.03)
    d = ImageDraw.Draw(im)
    d.rectangle((x, y, x + w, y + h), fill=(0, 0, 0), outline=(255, 255, 255), width=max(2, W // 400))
    f1, f2 = load_font(int(h * 0.2)), load_font(int(h * 0.38))
    d.rectangle((x + w * 0.06, y + h * 0.36, x + w * 0.94, y + h * 0.8), fill=(255, 255, 255))
    for txt, f, yy, fill in (("PARENTAL", f1, y + h * 0.08, (255, 255, 255)),
                             ("EXPLICIT", f2, y + h * 0.37, (0, 0, 0))):
        tw = d.textlength(txt, font=f)
        d.text((x + (w - tw) / 2, yy), txt, font=f, fill=fill)
    return im


# ---------------------------------------------------------------- validation & variants

def validate_cover(path):
    issues, ok = [], []
    im = Image.open(path)
    w, h = im.size
    if w != h:
        issues.append(f"Not square ({w}x{h}). Stores require 1:1.")
    if min(w, h) < 3000:
        issues.append(f"{w}x{h} is under 3000x3000 (most distributors' minimum).")
    else:
        ok.append(f"{w}x{h} px")
    if im.mode not in ("RGB",):
        issues.append(f"Colour mode is {im.mode}; use RGB (no CMYK, no transparency).")
    else:
        ok.append("RGB")
    size_mb = os.path.getsize(path) / 1e6
    if size_mb > 20:
        issues.append(f"File is {size_mb:.1f} MB; some distributors cap uploads, export a lighter JPG.")
    fmt = (im.format or "").upper()
    if fmt not in ("JPEG", "PNG", "TIFF"):
        issues.append(f"Format {fmt} - use JPG or PNG.")
    else:
        ok.append(fmt)
    return {"ok": ok, "issues": issues, "width": w, "height": h}


VARIANTS = {
    "cover_3000.jpg": ("square", 3000, 3000),
    "cover_1400_bandcamp.jpg": ("square", 1400, 1400),
    "social_square_1080.jpg": ("square", 1080, 1080),
    "story_1080x1920.jpg": ("fill", 1080, 1920),
    "youtube_thumbnail_1280x720.jpg": ("fill", 1280, 720),
    "banner_2560x1440.jpg": ("fill", 2560, 1440),
}


def export_variants(cover_path, out_dir, title="", artist=""):
    os.makedirs(out_dir, exist_ok=True)
    im = open_rgb(cover_path)
    out = []
    for name, (mode, w, h) in VARIANTS.items():
        if mode == "square":
            v = center_square(im, w)
        else:
            v = blur_fill(im, w, h)
            if "thumbnail" in name and (title or artist):
                v = add_text(v, title, artist, position="bottom-left", scale=0.9)
        p = os.path.join(out_dir, name)
        q = 92
        v.save(p, "JPEG", quality=q, optimize=True)
        while "thumbnail" in name and os.path.getsize(p) > 2_000_000 and q > 50:  # YouTube 2 MB limit
            q -= 8
            v.save(p, "JPEG", quality=q, optimize=True)
        out.append(p)
    return out


def contact_sheet(paths, out, cols=5, cell=300):
    rows = math.ceil(len(paths) / cols) or 1
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 30)), (18, 18, 22))
    d = ImageDraw.Draw(sheet)
    f = load_font(14)
    for i, p in enumerate(paths):
        try:
            t = thumbnail(p, cell - 10)
        except Exception:
            continue
        x, y = (i % cols) * cell, (i // cols) * (cell + 30)
        sheet.paste(t, (x + (cell - t.width) // 2, y + 5))
        d.text((x + 6, y + cell + 6), f"{i + 1}. {os.path.basename(p)[:34]}", font=f, fill=(200, 200, 210))
    sheet.save(out, "JPEG", quality=85)
    return out


def make_cover(source=None, mode="smart", out=None, title="", artist="", text_position="bottom",
               font_style="sans", generate_style="aurora", seed=None, explicit_label=False,
               palette_from=None, add_title=False):
    """One call that builds a 3000x3000 cover from a photo or from scratch."""
    warnings = []
    if source and mode != "generate":
        im = open_rgb(source)
        if min(im.size) < 3000:
            warnings.append(f"Source photo is {im.width}x{im.height}; it will be upscaled. Spotify advises against upscaled art - use a larger original if you have one.")
        if mode == "blurfill":
            im = blur_fill(im, 3000, 3000) if im.width != im.height else im.resize((3000, 3000), Image.LANCZOS)
        elif mode == "center":
            im = center_square(im)
        else:
            im = smart_square(im)
    else:
        cols = palette(open_rgb(palette_from), 4) if palette_from else None
        im = generate(generate_style, 3000, seed=seed, colors=cols)
    if add_title and (title or artist):
        im = add_text(im, title, artist, text_position, font_style)
    if explicit_label:
        im = parental_advisory(im)
    im.save(out, "JPEG", quality=95, subsampling=0)
    return {"path": out, "warnings": warnings, "validation": validate_cover(out)}

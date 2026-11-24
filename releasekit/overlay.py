"""Text and graphics drawn over a video with ffmpeg's subtitle renderer (libass).

One .ass file per render holds everything that changes over time: the progress bar and timer,
"Now playing" titles in playlist videos, and (later) lyrics and animated titles. libass draws it
crisply at any size, with fades, and ffmpeg burns it into the picture.
"""
import os

from PIL import ImageFont

from . import art


def _ts(sec):
    sec = max(0.0, float(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clock(sec):
    sec = int(max(0, sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _bgr(rgb, alpha=0):
    """ASS colour: &HAABBGGRR (alpha 0 = solid)."""
    r, g, b = rgb
    return f"&H{alpha:02X}{b:02X}{g:02X}{r:02X}"


def _esc(text):
    return str(text).replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def font():
    """(family name, folder) of the font Release Kit uses for titles, for libass."""
    path = art.find_font("sans") or ""
    if not path:
        return "Arial", None
    try:
        family = ImageFont.truetype(path, 20).getname()[0]
    except Exception:
        family = "Arial"
    return family, os.path.dirname(path)


def build(path, w, h, duration, rgb=(255, 255, 255), progress="off", chapters=None, now_playing=False,
          extra_events=None):
    """Write the overlay file. Returns path, or None when there's nothing to draw.

    progress: off | bar | bar_time. chapters: [(start_seconds, title)] for playlist videos.
    extra_events: more 'Dialogue:' lines (lyrics, titles) added by other features.
    """
    family, _ = font()
    u = min(w, h)
    events = []
    margin = int(w * 0.04)
    bar_h = max(4, int(u * 0.008))
    bar_y = h - int(u * 0.05)
    dur_ms = int(duration * 1000)
    if progress in ("bar", "bar_time") and duration > 0:
        x0, x1, y0, y1 = margin, w - margin, bar_y, bar_y + bar_h
        shape = f"m {x0} {y0} l {x1} {y0} {x1} {y1} {x0} {y1}"
        # the empty track, then the fill that grows from left to right over the whole song
        events.append(f"Dialogue: 0,{_ts(0)},{_ts(duration)},Shape,,0,0,0,,"
                      f"{{\\an7\\pos(0,0)\\p1\\1c{_bgr((255, 255, 255))}\\1a&HB0&}}{shape}")
        events.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Shape,,0,0,0,,"
                      f"{{\\an7\\pos(0,0)\\p1\\1c{_bgr(rgb)}\\clip({x0},{y0 - 2},{x0},{y1 + 2})"
                      f"\\t(0,{dur_ms},\\clip({x0},{y0 - 2},{x1},{y1 + 2}))}}{shape}")
        if progress == "bar_time":
            total = _clock(duration)
            for s in range(int(duration) + 1):
                end = min(duration, s + 1)
                if end <= s:
                    break
                events.append(f"Dialogue: 2,{_ts(s)},{_ts(end)},Time,,0,0,0,,"
                              f"{{\\an3\\pos({x1},{y0 - int(u * 0.012)})}}{_clock(s)} / {total}")
    if chapters and now_playing:
        y = (bar_y - int(u * 0.05)) if progress != "off" else h - int(u * 0.06)
        n = len(chapters)
        for i, (start, title) in enumerate(chapters):
            end = chapters[i + 1][0] if i + 1 < n else duration
            if end - start < 0.5:
                continue
            events.append(f"Dialogue: 3,{_ts(start)},{_ts(end)},Now,,0,0,0,,"
                          f"{{\\an1\\pos({margin},{y})\\fad(500,400)}}{{\\1c{_bgr(rgb)}}}NOW PLAYING  "
                          f"{{\\1c&H00FFFFFF&}}{_esc(title)}"
                          + (f"{{\\1a&H60&}}  ·  {i + 1}/{n}" if n > 1 else ""))
    events += list(extra_events or [])
    if not events:
        return None
    fs_time = max(12, int(u * 0.026))
    fs_now = max(14, int(u * 0.034))
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Shape,{family},20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Time,{family},{fs_time},&H00FFFFFF,&H00FFFFFF,&H80000000,&H80000000,0,0,0,0,100,100,0,0,1,1,1,3,0,0,0,1
Style: Now,{family},{fs_now},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,1,0,0,0,100,100,0.5,0,1,1.5,1.5,1,0,0,0,1
Style: Lyric,{family},{max(18, int(u * 0.055))},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,1,0,0,0,100,100,0,0,1,2,2,2,0,0,0,1
Style: Big,{family},{max(24, int(u * 0.085))},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,1,0,0,0,100,100,0,0,1,2,2,5,0,0,0,1
Style: Small,{family},{max(14, int(u * 0.036))},&H00FFFFFF,&H00FFFFFF,&H90000000,&H90000000,0,0,0,0,100,100,1,0,1,1.5,1.5,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(events) + "\n")
    return path


def filter_for(ass_path, esc_path):
    """The ffmpeg filter that burns the overlay in (esc_path: ff.esc_path)."""
    _, fontdir = font()
    extra = f":fontsdir='{esc_path(fontdir)}'" if fontdir else ""
    return f"subtitles=filename='{esc_path(ass_path)}'{extra}"

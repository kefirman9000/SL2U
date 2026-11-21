"""Video renderers: release videos, visualizers, full-album videos with chapters,
10-hour (or any length) loop videos, Shorts/Reels/TikTok clips and Spotify Canvas loops."""
import math
import os
import tempfile

from PIL import Image, ImageOps

from . import art, audio, ff

RES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160), "720p": (1280, 720)}
VIS_COLORS = "white@0.85"


def _stage(progress, a, b):
    """Map a sub-task's 0..1 progress into the a..b slice of the overall bar."""
    if not progress:
        return None
    return lambda f: progress(a + (b - a) * f)


def _x264(fps, still=False, crf=20):
    args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p",
            "-r", str(fps), "-g", str(int(fps * 2))]
    if still:
        args += ["-tune", "stillimage"]
    return args


AAC = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"]


def make_frame(image, out, w, h, title="", artist="", layout="cover", text=True):
    """Background frame. layout: cover (square art centred on blurred bg), full (photo fills frame)."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im = art.open_rgb(image)
    if layout == "full":
        frame = ImageOps.fit(im, (w, h), Image.LANCZOS)
    else:
        bg = art.blurred_bg(im, w, h, blur=max(20, w // 40), dim=0.45)
        side = int(min(w, h) * (0.62 if w > h else 0.8))
        cover = art.center_square(im, side)
        frame = bg
        if w > h:
            frame.paste(cover, ((w - side) // 2 - (int(w * 0.18) if text and (title or artist) else 0), (h - side) // 2))
        else:
            frame.paste(cover, ((w - side) // 2, int(h * 0.18)))
    if text and (title or artist):
        if w > h and layout != "full":
            # text block to the right of the cover
            region = frame.crop((int(w * 0.6), 0, w, h))
            txt = art.add_text(region, title, artist, position="center", scale=1.6)
            frame.paste(txt, (int(w * 0.6), 0))
        elif w > h:
            frame = art.add_text(frame, title, artist, position="bottom-left", scale=0.6)
        else:
            region = frame.crop((0, int(h * 0.18) + int(min(w, h) * 0.8), w, h))
            txt = art.add_text(region, title, artist, position="top", scale=1.1)
            frame.paste(txt, (0, int(h * 0.18) + int(min(w, h) * 0.8)))
    frame.save(out, "PNG")
    return out


def _vis_filter(kind, w, h, fps, vertical=False):
    vw = int(w * (0.86 if vertical else 0.7))
    vh = int(h * (0.12 if vertical else 0.2))
    if kind == "bars":
        vh = int(h * (0.16 if vertical else 0.26))
        src = (f"showcqt=s={vw}x{vh}:r={fps}:sono_h=0:axis_h=0:bar_g=2:basefreq=40:endfreq=12000,"
               f"colorkey=black:0.08")
    elif kind == "line":
        src = f"showwaves=s={vw}x{vh}:mode=line:rate={fps}:colors={VIS_COLORS}"
    elif kind == "spectrum":
        src = f"showspectrum=s={vw}x{vh}:mode=combined:slide=scroll:color=intensity:scale=log,fps={fps}"
    else:  # waves
        src = f"showwaves=s={vw}x{vh}:mode=cline:rate={fps}:colors={VIS_COLORS}"
    y = f"H-h-{int(h * 0.08)}" if not vertical else f"H-h-{int(h * 0.1)}"
    return src, f"(W-w)/2:{y}"


# ---------------------------------------------------------------- standard release video

def release_video(audio_path, image, out, title="", artist="", resolution="1080p", visualizer="none",
                  layout="cover", text=True, fps=None, start=0.0, length=None, fade=True,
                  vertical=False, progress=None, cancel=None):
    w, h = (1080, 1920) if vertical else RES[resolution]
    fps = fps or (30 if visualizer != "none" or vertical else 12)
    tmp = tempfile.mkdtemp(prefix="rk_")
    frame = make_frame(image, os.path.join(tmp, "frame.png"), w, h, title, artist, layout, text)
    dur = ff.duration(audio_path) - start
    if length:
        dur = min(dur, length)
    afade = ""
    if fade:
        fo = 1.5 if dur < 90 else 3
        afade = f"afade=t=in:st=0:d={0.3 if start else 0.05},afade=t=out:st={max(0, dur - fo)}:d={fo}"
    if visualizer == "none":
        # fast path: encode a 10 s still clip once, stream-copy it for the full length, encode audio once
        clip = os.path.join(tmp, "clip.mp4")
        ff.run(["-loop", "1", "-framerate", str(fps), "-i", frame, "-t", "10"] + _x264(fps, True) + ["-an", clip],
               cancel=cancel)
        lst = os.path.join(tmp, "list.txt")
        with open(lst, "w") as f:
            f.write(f"file '{clip}'\n" * (math.ceil(dur / 10) + 1))
        a_args = (["-ss", str(start)] if start else []) + ["-t", str(dur), "-i", audio_path]
        ff.run(["-f", "concat", "-safe", "0", "-i", lst] + a_args +
               (["-af", afade] if afade else []) +
               ["-map", "0:v", "-map", "1:a", "-c:v", "copy"] + AAC +
               ["-t", str(dur), "-movflags", "+faststart", out], dur, progress, cancel)
        return {"path": out, "duration": dur}
    args = ["-loop", "1", "-framerate", str(fps), "-i", frame]
    if start:
        args += ["-ss", str(start)]
    args += ["-t", str(dur), "-i", audio_path]
    if visualizer != "none":
        src, pos = _vis_filter(visualizer, w, h, fps, vertical)
        a_chain = f"[1:a]{afade + ',' if afade else ''}asplit=2[aout][av]"
        fc = f"{a_chain};[av]{src},format=rgba[vis];[0:v][vis]overlay={pos}:shortest=1,format=yuv420p[v]"
        args += ["-filter_complex", fc, "-map", "[v]", "-map", "[aout]"]
    else:
        if afade:
            args += ["-af", afade]
        args += ["-map", "0:v", "-map", "1:a"]
    args += _x264(fps, still=visualizer == "none") + AAC + ["-t", str(dur), "-shortest", "-movflags", "+faststart", out]
    ff.run(args, dur, progress, cancel)
    return {"path": out, "duration": dur}


# ---------------------------------------------------------------- full album with chapters

def fmt_ts(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def album_video(tracks, image, out, titles=None, album_title="", artist="", resolution="1080p",
                visualizer="none", gap=1.0, progress=None, cancel=None):
    titles = titles or [os.path.splitext(os.path.basename(t))[0] for t in tracks]
    tmp = tempfile.mkdtemp(prefix="rk_")
    joined = os.path.join(tmp, "album.wav")
    audio.build_cycle(tracks, joined, crossfade=0, gap=gap, progress=_stage(progress, 0, 0.15), cancel=cancel)
    chapters, t = [], 0.0
    for path, name in zip(tracks, titles):
        chapters.append((t, name))
        t += ff.duration(path) + gap
    tmp_out = os.path.join(tmp, "album_nochap.mp4")
    release_video(joined, image, tmp_out, album_title, artist, resolution, visualizer, fade=False,
                  progress=_stage(progress, 0.15, 0.97), cancel=cancel)
    # embed chapter markers into the MP4 as well
    meta = os.path.join(tmp, "chapters.txt")
    total = ff.duration(tmp_out)
    with open(meta, "w", encoding="utf-8") as f:
        f.write(";FFMETADATA1\n")
        for i, (st, name) in enumerate(chapters):
            end = chapters[i + 1][0] if i + 1 < len(chapters) else total
            f.write(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(st * 1000)}\nEND={int(end * 1000)}\ntitle={name}\n")
    ff.run(["-i", tmp_out, "-i", meta, "-map_metadata", "1", "-map_chapters", "1", "-c", "copy", out])
    chapter_text = "\n".join(f"{fmt_ts(st)} {name}" for st, name in chapters)
    if progress:
        progress(1.0)
    return {"path": out, "chapters": chapter_text, "duration": total}


# ---------------------------------------------------------------- loop clips (visual part of long videos)

def _clip_static(image, out, w, h, title, artist, text, seconds=60, fps=12, cancel=None):
    frame = make_frame(image, out + ".png", w, h, title, artist, "cover", text)
    ff.run(["-loop", "1", "-framerate", str(fps), "-i", frame, "-t", str(seconds)] + _x264(fps, True) + ["-an", out],
           cancel=cancel)
    return seconds


def _clip_zoom(image, out, w, h, title, artist, text, seconds=20, fps=24, amount=0.08, layout="full", cancel=None):
    """Slow breathe-in/out zoom that starts and ends on the same frame (seamless loop)."""
    big = out + ".png"
    tmpw, tmph = w * 2, h * 2  # render the frame large so zoompan stays smooth
    make_frame(image, big, tmpw, tmph, title, artist, layout, text)
    n = int(seconds * fps)
    z = f"1+{amount}*(1-cos(2*PI*on/{n}))/2"
    vf = (f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n}:s={w}x{h}:fps={fps}")
    ff.run(["-i", big, "-vf", vf, "-frames:v", str(n)] + _x264(fps) + ["-an", out], n / fps, cancel=cancel)
    return n / fps


def _clip_slideshow(images, out, w, h, title, artist, text, per=8.0, trans=1.5, fps=24, cancel=None):
    tmp = os.path.dirname(out)
    frames = []
    for i, p in enumerate(images):
        frames.append(make_frame(p, os.path.join(tmp, f"slide_{i:03d}.png"), w, h, title, artist, "cover", text))
    seq = frames + [frames[0]]  # end on the first slide -> seamless when looped
    args, fc = [], []
    for i, f in enumerate(seq):
        d = trans if i == len(seq) - 1 else per + trans
        args += ["-loop", "1", "-framerate", str(fps), "-t", f"{d:.3f}", "-i", f]
        fc.append(f"[{i}:v]settb=AVTB,fps={fps},format=yuv420p[s{i}]")
    cur, offset = "s0", 0.0
    for i in range(1, len(seq)):
        offset += per
        fc.append(f"[{cur}][s{i}]xfade=transition=fade:duration={trans}:offset={offset:.3f}[x{i}]")
        cur = f"x{i}"
    total = per * len(frames) + trans
    ff.run(args + ["-filter_complex", ";".join(fc), "-map", f"[{cur}]"] + _x264(fps) + ["-an", out],
           total, cancel=cancel)
    return total


# ---------------------------------------------------------------- long / 10-hour videos

def long_video(tracks, out, hours=10.0, visual="static", image=None, images=None, title="", artist="",
               text=True, resolution="1080p", crossfade=4.0, ambient=None, ambient_db=-18.0,
               fade_in=3.0, fade_out=8.0, slide_seconds=8.0, progress=None, cancel=None):
    """Render an N-hour loop video fast.

    The audio cycle is built once (seamless crossfade loop), then looped and encoded
    in a single pass. The picture is a short clip rendered once and stream-copied
    over and over, so a 10-hour video takes minutes, not hours.
    """
    w, h = RES[resolution]
    target = float(hours) * 3600
    tmp = tempfile.mkdtemp(prefix="rk_long_")
    cyc = os.path.join(tmp, "cycle.wav")
    audio.build_cycle(tracks, cyc, crossfade=crossfade, ambient=ambient, ambient_db=ambient_db,
                      progress=_stage(progress, 0, 0.05), cancel=cancel)
    cycle_len = ff.duration(cyc)
    loops = math.ceil(target / cycle_len)

    # visual clip
    clip = os.path.join(tmp, "clip.mp4")
    if visual == "slideshow" and images:
        clip_len = _clip_slideshow(images[:60], clip, w, h, title, artist, text, per=slide_seconds, cancel=cancel)
    elif visual == "zoom":
        clip_len = _clip_zoom(image, clip, w, h, title, artist, text, cancel=cancel)
    else:
        clip_len = _clip_static(image or (images or [None])[0], clip, w, h, title, artist, text, cancel=cancel)
    if progress:
        progress(0.12)
    reps = math.ceil(target / clip_len) + 1
    lst = os.path.join(tmp, "clips.txt")
    with open(lst, "w") as f:
        for _ in range(reps):
            f.write(f"file '{clip}'\n")

    # audio: loop cycle and encode once
    long_audio = os.path.join(tmp, "long.m4a")
    af = f"afade=t=in:st=0:d={fade_in},afade=t=out:st={max(0, target - fade_out)}:d={fade_out}"
    ff.run(["-stream_loop", str(loops - 1), "-i", cyc, "-t", str(target), "-af", af] + AAC + [long_audio],
           target, _stage(progress, 0.12, 0.9), cancel)

    # mux (no re-encode)
    ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-i", long_audio, "-map", "0:v", "-map", "1:a",
            "-c", "copy", "-t", str(target), "-movflags", "+faststart", out],
           target, _stage(progress, 0.9, 1.0), cancel)
    return {"path": out, "duration": target, "cycle_seconds": cycle_len, "loops": loops}


# ---------------------------------------------------------------- Shorts / Reels / TikTok

def short_clip(audio_path, image, out, title="", artist="", seconds=30, start=None, visualizer="waves",
               progress=None, cancel=None):
    if start is None:
        start = audio.loudest_window(audio_path, seconds)
    res = release_video(audio_path, image, out, title, artist, visualizer=visualizer, start=start,
                        length=seconds, vertical=True, progress=progress, cancel=cancel)
    res["start"] = start
    return res


# ---------------------------------------------------------------- Spotify Canvas

def canvas(image, out, seconds=7.5, progress=None, cancel=None):
    """3-8 s vertical 1080x1920 seamless loop, no audio (upload in Spotify for Artists).
    Kept just under 8 s because uploads that are even slightly long get rejected."""
    seconds = max(3, min(7.9, float(seconds)))
    _clip_zoom(image, out, 1080, 1920, "", "", False, seconds=seconds, fps=30, amount=0.06, cancel=cancel)
    if progress:
        progress(1.0)
    return {"path": out, "duration": seconds}

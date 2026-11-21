"""Video renderers: release videos, visualizers, full-album videos with chapters,
10-hour (or any length) loop videos, Shorts/Reels/TikTok clips and Spotify Canvas loops."""
import math
import os
import tempfile

from PIL import Image, ImageOps

from . import art, audio, ff, reactive

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
    if layout == "blur":  # soft background only, for a bumping logo/cover in the middle
        frame = art.blurred_bg(im, w, h, blur=max(20, w // 40), dim=0.5)
        if text and (title or artist):
            frame = art.add_text(frame, title, artist, position="top-left", scale=0.42)
        frame.save(out, "PNG")
        return out
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
    return reactive.vis_chain(kind, w, h, fps, vertical)


def reactive_render(bg_args, audio_path, out, w, h, fps, dur, opts, start=0.0, afade="", with_audio=True,
                    vertical=False, n_frames=None, spin_period=12.0, progress=None, cancel=None):
    """Background + visualizer + bumping element, all locked to the audio."""
    vis, bump = opts.get("visualizer", "none"), opts.get("bump", "none")
    rgb = reactive.pick_color(opts.get("vis_color", "white"), opts.get("image"))
    n_frames = n_frames or int(round(dur * fps))
    args = list(bg_args) + (["-ss", str(start)] if start else []) + ["-t", str(dur), "-i", audio_path]
    fc = [f"[0:v]fps={fps},scale={w}:{h},setsar=1[bg]",
          f"[1:a]{afade + ',' if afade else ''}asplit=2[aout][av]" if with_audio else "[1:a]anull[av]"]
    cur = "bg"
    if vis != "none":
        chain, pos = reactive.vis_chain(vis, w, h, fps, vertical, rgb)
        fc += [f"[av]{chain}[vis]", f"[{cur}][vis]overlay={pos}:shortest=1[v1]"]
        cur = "v1"
    else:
        fc.append("[av]anullsink")
    feed = None
    if bump != "none":
        canvas = int(min(w, h) * (0.56 if not vertical else 0.8)) // 2 * 2
        tiles = reactive.bump_frames(bump, canvas, float(opts.get("bump_amount", 0.12)), image=opts.get("image"),
                                     logo=opts.get("logo"), title=opts.get("title", ""),
                                     artist=opts.get("artist", ""), rgb=rgb)
        env = reactive.bass_envelope(audio_path, fps, start, dur, n_frames)
        feed = reactive.frame_feeder(env, tiles)
        args += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{canvas}x{canvas}", "-framerate", str(fps), "-i", "pipe:0"]
        b = "[2:v]"
        if bump == "disc":
            fc.append(f"{b}rotate=a='2*PI*t/{spin_period:.4f}':c=none:ow={canvas}:oh={canvas}[bp]")
            b = "[bp]"
        lift = 0.07 if vis in ("bars", "mirror", "waves", "line", "spectrum") else 0.0
        y = f"(H-h)/2-H*{lift + (0.06 if vertical else 0):.2f}"
        fc.append(f"[{cur}]{b}overlay=(W-w)/2:{y}[v2]")
        cur = "v2"
    fc.append(f"[{cur}]format=yuv420p[v]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]"]
    args += (["-map", "[aout]"] + AAC) if with_audio else ["-an"]
    args += _x264(fps) + ["-frames:v", str(n_frames), "-movflags", "+faststart", out]
    ff.run(args, dur, progress, cancel, feed=feed)
    return out


def _reactive_on(opts):
    return bool(opts) and (opts.get("visualizer", "none") != "none" or opts.get("bump", "none") != "none")


# ---------------------------------------------------------------- standard release video

def release_video(audio_path, image, out, title="", artist="", resolution="1080p", visualizer="none",
                  layout="cover", text=True, fps=None, start=0.0, length=None, fade=True,
                  vertical=False, progress=None, cancel=None, opts=None):
    """opts: extra reactive options {bump, logo, vis_color, bump_amount}."""
    opts = dict(opts or {}, visualizer=visualizer, image=image, title=title, artist=artist)
    if opts.get("bump", "none") != "none" or visualizer == "scope":
        layout = "blur"
        text = text and opts.get("bump") != "text"
    w, h = (1080, 1920) if vertical else RES[resolution]
    fps = fps or (30 if _reactive_on(opts) or vertical else 12)
    tmp = tempfile.mkdtemp(prefix="rk_")
    frame = make_frame(image, os.path.join(tmp, "frame.png"), w, h, title, artist, layout, text)
    dur = ff.duration(audio_path) - start
    if length:
        dur = min(dur, length)
    afade = ""
    if fade:
        fo = 1.5 if dur < 90 else 3
        afade = f"afade=t=in:st=0:d={0.3 if start else 0.05},afade=t=out:st={max(0, dur - fo)}:d={fo}"
    if not _reactive_on(opts):
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
    reactive_render(["-loop", "1", "-framerate", str(fps), "-i", frame], audio_path, out, w, h, fps, dur, opts,
                    start=start, afade=afade, vertical=vertical, progress=progress, cancel=cancel)
    return {"path": out, "duration": dur}


# ---------------------------------------------------------------- full album with chapters

def fmt_ts(sec):
    sec = int(round(sec))
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def album_video(tracks, image, out, titles=None, album_title="", artist="", resolution="1080p",
                visualizer="none", gap=1.0, progress=None, cancel=None, opts=None):
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
                  progress=_stage(progress, 0.15, 0.97), cancel=cancel, opts=opts)
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

def _clip_static(image, out, w, h, title, artist, text, seconds=60, fps=12, cancel=None, layout="cover"):
    frame = make_frame(image, out + ".png", w, h, title, artist, layout, text)
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


def _clip_pan(image, out, w, h, title, artist, text, seconds=30, fps=24, amount=0.12, cancel=None):
    """Slow side-to-side drift across the picture that returns to its start (seamless)."""
    big = out + ".png"
    W, H = int(w * (1 + amount)), int(h * (1 + amount))
    make_frame(image, big, W, H, title, artist, "full", text)
    x = f"(iw-ow)/2*(1-cos(2*PI*t/{seconds}))"
    y = f"(ih-oh)/2*(1-cos(2*PI*t/{seconds}))/2"
    ff.run(["-loop", "1", "-framerate", str(fps), "-i", big, "-t", str(seconds),
            "-vf", f"crop={w}:{h}:x='{x}':y='{y}'"] + _x264(fps) + ["-an", out], seconds, cancel=cancel)
    return seconds


def _clip_effect(image, out, w, h, title, artist, text, effect="grain", seconds=12, fps=24, cancel=None,
                 layout="cover"):
    """Still picture with a living texture: grain (film/VHS noise + soft flicker) or pulse (breathing glow)."""
    frame = make_frame(image, out + ".png", w, h, title, artist, layout, text)
    if effect == "pulse":
        vf = f"eq=eval=frame:brightness='0.035*sin(2*PI*t/{seconds})':saturation='1+0.08*sin(2*PI*t/{seconds})'"
    else:  # grain / VHS
        vf = ("noise=alls=14:allf=t,eq=eval=frame:brightness='0.012*sin(2*PI*t*1.7)',"
              "vignette=PI/5,format=yuv420p")
    ff.run(["-loop", "1", "-framerate", str(fps), "-i", frame, "-t", str(seconds), "-vf", vf]
           + _x264(fps, crf=23) + ["-an", out], seconds, cancel=cancel)
    return seconds


def _text_png(path, w, h, title, artist, pos="bottom"):
    """Transparent overlay with title/artist (used on top of video clips)."""
    from PIL import ImageDraw, ImageFilter
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if title or artist:
        pad = int(w * 0.04)
        f_a, f_t = art.load_font(int(h * 0.035)), art.load_font(int(h * 0.065))
        d = ImageDraw.Draw(ov)
        block = int(h * 0.065) + int(h * 0.05)
        y = pad if pos == "top" else h - pad - block
        # soft dark band for legibility on busy footage
        band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle((0, 0, w, y + block + pad) if pos == "top" else (0, y - pad, w, h),
                                       fill=(0, 0, 0, 110))
        ov = Image.alpha_composite(ov, band.filter(ImageFilter.GaussianBlur(pad)))
        d = ImageDraw.Draw(ov)
        if artist:
            d.text((pad, y), artist.upper(), font=f_a, fill=(255, 255, 255, 230))
        d.text((pad, y + int(h * 0.045)), title, font=f_t, fill=(255, 255, 255, 255))
    ov.save(path)
    return path


def _norm_clip(src, out, w, h, fps, fit="fill", max_seconds=None, cancel=None):
    """Scale/crop any video or GIF to w x h at a steady frame rate, no audio."""
    t = ["-t", str(max_seconds)] if max_seconds else []
    if fit == "fit":
        fc = (f"[0:v]fps={fps},split=2[a][b];"
              f"[a]scale={w//4}:{h//4}:force_original_aspect_ratio=increase,crop={w//4}:{h//4},boxblur=8,scale={w}:{h},eq=brightness=-0.12[bg];"
              f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,format=yuv420p[v]")
    else:
        fc = (f"[0:v]fps={fps},scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
              f"setsar=1,format=yuv420p[v]")
    ff.run(t + ["-i", src, "-filter_complex", fc, "-map", "[v]"] + _x264(fps) + ["-an", out], cancel=cancel)
    return ff.duration(out)


def _clip_video(src, out, w, h, title, artist, text, mode="crossfade", fit="fill", fps=30, xf=1.0, cancel=None,
                text_pos="bottom"):
    """Turn one video clip (or GIF) into a seamless loop.
    mode: loop (as is), pingpong (forward then backward), crossfade (end dissolves into the start)."""
    tmp = os.path.dirname(out)
    norm = os.path.join(tmp, "clip_norm.mp4")
    length = _norm_clip(src, norm, w, h, fps, fit, max_seconds=20 if mode == "pingpong" else 180, cancel=cancel)
    looped = os.path.join(tmp, "clip_loop.mp4")
    if mode == "pingpong":
        fc = "[0:v]split=2[a][b];[b]reverse,trim=start_frame=1,setpts=PTS-STARTPTS[r];[a][r]concat=n=2:v=1:a=0[v]"
        ff.run(["-i", norm, "-filter_complex", fc, "-map", "[v]"] + _x264(fps) + ["-an", looped], cancel=cancel)
    elif mode == "crossfade" and length > xf * 3:
        fc = (f"[0:v]trim=start={xf},setpts=PTS-STARTPTS[body];[1:v]trim=end={xf},setpts=PTS-STARTPTS[head];"
              f"[body][head]xfade=transition=fade:duration={xf}:offset={length - 2 * xf:.3f}[v]")
        ff.run(["-i", norm, "-i", norm, "-filter_complex", fc, "-map", "[v]"] + _x264(fps) + ["-an", looped],
               cancel=cancel)
    else:
        looped = norm
    if text and (title or artist):
        png = _text_png(os.path.join(tmp, "text.png"), w, h, title, artist, text_pos)
        ff.run(["-i", looped, "-i", png, "-filter_complex", "[0:v][1:v]overlay=0:0,format=yuv420p[v]",
                "-map", "[v]"] + _x264(fps) + ["-an", out], cancel=cancel)
    else:
        os.replace(looped, out)
    return ff.duration(out)


def _clip_folder(paths, out, w, h, title, artist, text, fit="fill", per=None, fps=30, trans=1.0, cancel=None,
                 text_pos="bottom"):
    """Several clips one after another with dissolves, looping back to the first."""
    tmp = os.path.dirname(out)
    parts, lens = [], []
    for i, pth in enumerate(paths[:40]):
        n = os.path.join(tmp, f"part_{i:02d}.mp4")
        lens.append(_norm_clip(pth, n, w, h, fps, fit, max_seconds=per or 60, cancel=cancel))
        parts.append(n)
    if len(parts) == 1:
        return _clip_video(parts[0], out, w, h, title, artist, text, "crossfade", "fill", fps, trans, cancel, text_pos)
    args, fc, cur, offset = [], [], "0:v", 0.0
    for p_ in parts:
        args += ["-i", p_]
    for i in range(1, len(parts)):
        offset += lens[i - 1] - trans
        fc.append(f"[{cur}][{i}:v]xfade=transition=fade:duration={trans}:offset={offset:.3f}[x{i}]")
        cur = f"x{i}"
    joined = os.path.join(tmp, "joined.mp4")
    ff.run(args + ["-filter_complex", ";".join(fc), "-map", f"[{cur}]"] + _x264(fps) + ["-an", joined], cancel=cancel)
    return _clip_video(joined, out, w, h, title, artist, text, "crossfade", "fill", fps, trans, cancel, text_pos)


VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".gif")


def list_clips(folder):
    out = []
    for root, _, files in os.walk(folder):
        out += [os.path.join(root, f) for f in sorted(files) if f.lower().endswith(VIDEO_EXT) and not f.startswith(".")]
    return out


VISUALS = {
    "static": "Still cover", "zoom": "Slow zoom", "pan": "Slow pan", "grain": "Film grain / VHS",
    "pulse": "Breathing glow", "slideshow": "Photo slideshow", "clip": "My video clip", "clips": "Folder of video clips",
}


def make_loop_clip(visual, clip, w, h, image=None, images=None, title="", artist="", text=True,
                   slide_seconds=8.0, video_clip=None, clip_folder=None, clip_mode="crossfade", clip_fit="fill",
                   cancel=None, text_pos="bottom", layout="cover"):
    if visual == "slideshow" and images:
        return _clip_slideshow(images[:60], clip, w, h, title, artist, text, per=slide_seconds, cancel=cancel)
    if visual == "clip" and video_clip:
        return _clip_video(video_clip, clip, w, h, title, artist, text, clip_mode, clip_fit, cancel=cancel,
                           text_pos=text_pos)
    if visual == "clips" and clip_folder:
        paths = list_clips(clip_folder)
        if not paths:
            raise ValueError("No video clips found in that folder.")
        return _clip_folder(paths, clip, w, h, title, artist, text, clip_fit, cancel=cancel, text_pos=text_pos)
    img = image or (images or [None])[0]
    if not img:
        raise ValueError("This visual needs a cover or an image.")
    if visual == "zoom":
        return _clip_zoom(img, clip, w, h, title, artist, text, cancel=cancel)
    if visual == "pan":
        return _clip_pan(img, clip, w, h, title, artist, text, cancel=cancel)
    if visual in ("grain", "pulse"):
        return _clip_effect(img, clip, w, h, title, artist, text, visual, cancel=cancel, layout=layout)
    return _clip_static(img, clip, w, h, title, artist, text, cancel=cancel, layout=layout)


# ---------------------------------------------------------------- long / 10-hour videos

def long_video(tracks, out, hours=10.0, visual="static", image=None, images=None, title="", artist="",
               text=True, resolution="1080p", crossfade=4.0, ambient=None, ambient_db=-18.0,
               fade_in=3.0, fade_out=8.0, slide_seconds=8.0, visualizer="none", video_clip=None,
               clip_folder=None, clip_mode="crossfade", clip_fit="fill", progress=None, cancel=None, opts=None):
    """Render an N-hour loop video fast.

    The audio cycle is built once (seamless crossfade loop), then looped and encoded
    in a single pass. The picture is a short clip rendered once and stream-copied
    over and over, so a 10-hour video takes minutes, not hours.

    With a visualizer, one full audio cycle of picture is rendered (background clip + bars/waves
    reacting to the music) and repeated. The cycle is trimmed to a whole number of frames so
    picture and sound stay locked together for the whole 10 hours.
    """
    opts = dict(opts or {}, visualizer=visualizer, image=image or (images or [None])[0], title=title, artist=artist)
    reactive_mode = _reactive_on(opts)
    bump = opts.get("bump", "none")
    if bump == "text":
        text = False
    w, h = RES[resolution]
    target = float(hours) * 3600
    tmp = tempfile.mkdtemp(prefix="rk_long_")
    cyc = os.path.join(tmp, "cycle.wav")
    audio.build_cycle(tracks, cyc, crossfade=crossfade, ambient=ambient, ambient_db=ambient_db,
                      progress=_stage(progress, 0, 0.05), cancel=cancel)
    fps_vis = 30
    if reactive_mode:
        frames = int(ff.duration(cyc) * fps_vis)
        exact = os.path.join(tmp, "cycle_exact.wav")
        ff.run(["-i", cyc, "-af", f"atrim=end_sample={frames * 48000 // fps_vis}", "-c:a", "pcm_s16le", exact])
        cyc = exact
    cycle_len = ff.duration(cyc)
    loops = math.ceil(target / cycle_len)

    clip = os.path.join(tmp, "clip.mp4")
    clip_len = make_loop_clip(visual, clip, w, h, image, images, title, artist,
                              text, slide_seconds, video_clip, clip_folder, clip_mode, clip_fit, cancel,
                              text_pos="top" if reactive_mode else "bottom",
                              layout="blur" if bump != "none" or visualizer == "scope" else "cover")
    if progress:
        progress(0.12)

    seg, seg_len = clip, clip_len
    if reactive_mode:
        # one audio cycle of picture with the visualizer / bumping element on the looping background
        seg = os.path.join(tmp, "cycle_video.mp4")
        spins = max(1, round(cycle_len / 12.0))  # whole turns per cycle -> the disc never jumps at the loop
        reactive_render(["-stream_loop", "-1", "-i", clip], cyc, seg, w, h, fps_vis, cycle_len, opts,
                        with_audio=False, n_frames=int(round(cycle_len * fps_vis)), spin_period=cycle_len / spins,
                        progress=_stage(progress, 0.12, 0.45), cancel=cancel)
        seg_len = cycle_len
    a0 = 0.45 if reactive_mode else 0.12

    reps = math.ceil(target / seg_len) + 1
    lst = os.path.join(tmp, "clips.txt")
    with open(lst, "w") as f:
        f.write(f"file '{seg}'\n" * reps)

    # audio: loop cycle and encode once
    long_audio = os.path.join(tmp, "long.m4a")
    af = f"afade=t=in:st=0:d={fade_in},afade=t=out:st={max(0, target - fade_out)}:d={fade_out}"
    ff.run(["-stream_loop", str(loops - 1), "-i", cyc, "-t", str(target), "-af", af] + AAC + [long_audio],
           target, _stage(progress, a0, 0.9), cancel)

    # mux (no re-encode)
    ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-i", long_audio, "-map", "0:v", "-map", "1:a",
            "-c", "copy", "-t", str(target), "-movflags", "+faststart", out],
           target, _stage(progress, 0.9, 1.0), cancel)
    return {"path": out, "duration": target, "cycle_seconds": cycle_len, "loops": loops}


# ---------------------------------------------------------------- Shorts / Reels / TikTok

def short_clip(audio_path, image, out, title="", artist="", seconds=30, start=None, visualizer="waves",
               progress=None, cancel=None, opts=None):
    if start is None:
        start = audio.loudest_window(audio_path, seconds)
    res = release_video(audio_path, image, out, title, artist, visualizer=visualizer, start=start,
                        length=seconds, vertical=True, progress=progress, cancel=cancel, opts=opts)
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

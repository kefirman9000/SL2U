"""Video renderers: release videos, visualizers, full-album videos with chapters,
10-hour (or any length) loop videos, Shorts/Reels/TikTok clips and Spotify Canvas loops."""
import math
import os
import tempfile

from PIL import Image, ImageOps

from . import art, audio, drawn, ff, fireworks, fx, overlay, reactive

RES = {"1080p": (1920, 1080), "1440p": (2560, 1440), "4k": (3840, 2160), "720p": (1280, 720)}
VIS_COLORS = "white@0.85"


# ---------------------------------------------------------------- temporary files
# Every working folder made while a video is rendered is removed when that video is finished (or fails),
# so clip pieces, audio cycles and frames don't pile up on the disk.
import functools
import shutil
import threading

_TMP = threading.local()
TMP_PREFIX = "rk_"


def _mkdtemp(prefix=TMP_PREFIX):
    d = tempfile.mkdtemp(prefix=prefix)
    stack = getattr(_TMP, "dirs", None)
    if stack is not None:
        stack.append(d)
    return d


def cleans_temp(fn):
    @functools.wraps(fn)
    def wrapper(*a, **k):
        outer = getattr(_TMP, "dirs", None) is None
        if outer:
            _TMP.dirs = []
        try:
            return fn(*a, **k)
        finally:
            if outer:
                for d in _TMP.dirs:
                    shutil.rmtree(d, ignore_errors=True)
                _TMP.dirs = None
    return wrapper


def clean_old_temp(max_age_hours=12):
    """Remove working folders left behind by older versions (or a crash). Returns how many."""
    import time
    root, n = tempfile.gettempdir(), 0
    for name in os.listdir(root):
        if not name.startswith(TMP_PREFIX) or name.startswith("rk_update_"):
            continue
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path) and time.time() - os.path.getmtime(path) > max_age_hours * 3600:
                shutil.rmtree(path, ignore_errors=True)
                n += 1
        except OSError:
            pass
    return n


def _stage(progress, a, b):
    """Map a sub-task's 0..1 progress into the a..b slice of the overall bar."""
    if not progress:
        return None
    return lambda f: progress(a + (b - a) * f)


def _x264(fps, still=False, crf=20):
    """H.264 settings for the YouTube videos: graphics card if available (see ff.h264), else libx264."""
    q = 2 if crf <= 17 else 1 if crf <= 23 else 0
    return ff.h264(fps, q, still=still, cpu_preset="veryfast", crf=crf)


AAC = ["-c:a", "aac", "-b:a", "320k", "-ar", "48000"]


# ---------------------------------------------------------------- your video clips (and photos) as the background

CACHE_DIR = os.path.join(tempfile.gettempdir(), "releasekit_cache")  # app.py points this at the data folder
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".gif")


def _prep_media(src, w, h, fps, fit="fill", cancel=None):
    """A clip (or photo, as a slow zoom) scaled to w x h at fps, cached so previews and re-renders are quick."""
    import hashlib
    st = os.stat(src)
    key = hashlib.sha1(f"{os.path.abspath(src)}|{st.st_mtime}|{st.st_size}|{w}x{h}|{fps}|{fit}".encode()).hexdigest()[:16]
    os.makedirs(CACHE_DIR, exist_ok=True)
    out = os.path.join(CACHE_DIR, f"clip_{key}.mp4")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out, ff.duration(out)
    tmp = out + ".part.mp4"
    if src.lower().endswith(art.IMAGE_EXT):
        _clip_zoom(src, tmp, w, h, "", "", False, seconds=16, fps=fps, amount=0.06, cancel=cancel)
    else:
        _norm_clip(src, tmp, w, h, fps, fit, max_seconds=120, cancel=cancel)
    os.replace(tmp, out)
    return out, ff.duration(out)


MIN_PIECE = 3.0   # seconds: shortest piece cut from one clip
MAX_PIECES = 160  # different pieces made at most; after that they repeat


def clips_background(clips, w, h, fps, dur, audio_path=None, start=0.0, switch="beat", bars=4, fit="fill",
                     tmp=None, progress=None, cancel=None):
    """One background video at least `dur` long from your clips and photos.
    switch: beat = cut to the next clip every `bars` bars, on the beat; full = play each clip through."""
    tmp = tmp or _mkdtemp(prefix="rk_bg_")
    clips = [c for c in clips if c and os.path.isfile(c)]
    if not clips:
        raise ValueError("None of the chosen clips could be found.")
    preps = []
    for i, c in enumerate(clips):
        preps.append(_prep_media(c, w, h, fps, fit, cancel))
        if progress:
            progress(0.5 * (i + 1) / len(clips))
    if switch == "beat":
        bpm, phase = reactive.beat_grid(audio_path, start, dur) if audio_path else (120.0, 0.0)
        seg = max(2.0, min(30.0, int(bars) * 4 * 60.0 / bpm))
        lengths = [phase + seg] if phase > 0.05 else [seg]
        while sum(lengths) < dur + 0.5:
            lengths.append(seg)
    else:
        lengths = []
        i = 0
        while sum(lengths) < dur + 0.5:
            # a very short clip (a GIF, a one-frame video) is looped for a few seconds, not cut 12 times a second
            plen = preps[i % len(preps)][1]
            lengths.append(plen if plen >= MIN_PIECE else plen * math.ceil(MIN_PIECE / max(plen, 0.04)))
            i += 1
    if len(preps) == 1 and switch != "beat" and preps[0][1] >= dur:
        return preps[0][0]
    # Make each different piece once. When every clip has been used up (or after MAX_PIECES) the order
    # just repeats, so the rest of the video re-uses the same pieces instead of making thousands more.
    total_material = sum(p[1] for p in preps)
    made, used = 0, 0.0
    for length in lengths:
        made += 1
        used += length
        at_turn = made % len(preps) == 1 % len(preps)  # the next piece would start the clip order again
        if at_turn and (made >= MAX_PIECES or (made > len(preps) and used >= total_material)):
            break
    order = list(range(made))
    while sum(lengths[j] for j in order) < dur + 0.5:  # repeat (skipping the first, off-beat piece)
        order += list(range(1 if made > 1 else 0, made))
        if len(order) > 200000:
            break
    lengths = lengths[:made]
    pieces, offsets = [], [0.0] * len(preps)
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "17", "-pix_fmt", "yuv420p", "-r", str(fps), "-g", str(fps)]
    for i, length in enumerate(lengths):
        k = i % len(preps)
        src, plen = preps[k]
        off = offsets[k] % max(0.1, plen)
        piece = os.path.join(tmp, f"piece_{i:04d}.mp4")
        ff.run(["-stream_loop", "-1", "-ss", f"{off:.3f}", "-i", src, "-t", f"{length:.3f}", "-an"] + enc + [piece],
               cancel=cancel)
        offsets[k] = off + length  # next time this clip comes round, carry on where it left off
        pieces.append(piece)
        if progress:
            progress(0.5 + 0.5 * (i + 1) / len(lengths))
    if len(pieces) == 1:
        return pieces[0]
    lst = os.path.join(tmp, "pieces.txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.writelines(f"file '{pieces[j].replace(chr(92), '/')}'\n" for j in order)
    out = os.path.join(tmp, "background.mp4")
    ff.run(["-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", out], cancel=cancel)
    return out


def make_frame(image, out, w, h, title="", artist="", layout="cover", text=True, vis_room=False, dim=None,
               text_top=False):
    """Background frame. layout: cover (square art centred on blurred bg), full (photo fills frame),
    blur (blurred picture only), dark (near-black with a hint of the art's colours).
    vis_room: keep the bottom free for a bar/wave visualizer (smaller cover, moved up).
    dim: 0 (bright) .. 0.9 (very dark) for the background; None = the usual."""
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    im = art.open_rgb(image)
    bright = None if dim is None else max(0.08, 1 - float(dim))
    if layout == "dark":
        frame = art.blurred_bg(im, w, h, blur=max(40, w // 10), dim=0.12 if bright is None else bright * 0.3)
        if text and (title or artist):
            frame = art.add_text(frame, title, artist, position="top-left", scale=0.42)
        frame.save(out, "PNG")
        return out
    if layout == "blur":  # soft background only, for a bumping logo/cover in the middle
        frame = art.blurred_bg(im, w, h, blur=max(20, w // 40), dim=0.5 if bright is None else bright)
        if text and (title or artist):
            frame = art.add_text(frame, title, artist, position="top-left", scale=0.42)
        frame.save(out, "PNG")
        return out
    if layout == "full":
        frame = ImageOps.fit(im, (w, h), Image.LANCZOS)
        if bright is not None and bright < 1:
            frame = Image.blend(Image.new("RGB", (w, h), (0, 0, 0)), frame, bright)
    elif 0.7 <= w / h <= 1.4:  # square and 4:5 (Instagram feed): cover on top, text under it
        frame = art.blurred_bg(im, w, h, blur=max(20, w // 40), dim=0.45 if bright is None else bright)
        side = int(min(h * 0.5, w * 0.7))
        top = int(h * 0.08)
        frame.paste(art.center_square(im, side), ((w - side) // 2, top))
        if text and (title or artist):
            y0 = top + side
            region = frame.crop((0, y0, w, min(h, y0 + int(h * 0.16))))
            frame.paste(art.add_text(region, title, artist, position="center", scale=0.9), (0, y0))
        frame.save(out, "PNG")
        return out
    else:
        bg = art.blurred_bg(im, w, h, blur=max(20, w // 40), dim=0.45 if bright is None else bright)
        side = int(min(w, h) * ((0.5 if vis_room else 0.62) if w > h else 0.8))
        cover = art.center_square(im, side)
        frame = bg
        if w > h:
            y = int(h * 0.1) if vis_room else (h - side) // 2
            frame.paste(cover, ((w - side) // 2 - (int(w * 0.18) if text and (title or artist) else 0), y))
        else:
            frame.paste(cover, ((w - side) // 2, int(h * 0.18)))
    if text and (title or artist):
        if w > h and layout != "full":
            # text block to the right of the cover
            band = (int(h * 0.1), int(h * 0.1) + side) if vis_room else (0, h)  # line up with the cover
            region = frame.crop((int(w * 0.6), band[0], w, band[1]))
            txt = art.add_text(region, title, artist, position="center", scale=1.6 if not vis_room else 1.25)
            frame.paste(txt, (int(w * 0.6), band[0]))
        elif w > h:
            frame = art.add_text(frame, title, artist, position="top-left" if vis_room or text_top else "bottom-left",
                                 scale=0.6)
        else:
            region = frame.crop((0, int(h * 0.18) + int(min(w, h) * 0.8), w, h))
            txt = art.add_text(region, title, artist, position="top", scale=1.1)
            frame.paste(txt, (0, int(h * 0.18) + int(min(w, h) * 0.8)))
    frame.save(out, "PNG")
    return out


def _vis_filter(kind, w, h, fps, vertical=False):
    return reactive.vis_chain(kind, w, h, fps, vertical)


# settings from the editor ("the look") that every video uses
STYLE_KEYS = ("bump", "logo", "vis_color", "bump_amount", "vis_size", "vis_pos", "bg", "dim", "motion", "center_size",
              "clips", "clip_switch", "clip_bars", "clip_fit", "progress", "now_playing",
              "fx_zoom", "fx_shake", "fx_flash", "fx_color", "overlays", "overlay_amount", "vis2", "vis2_color",
              # fireworks show and foreground (1.8)
              "fireworks", "fw_effect", "fw_palette", "fw_colors", "fw_mix", "fw_shape", "fw_exact", "fw_snow", "fw_size",
              "fw_trail", "fw_every", "fw_rockets", "fw_order", "fw_tl", "fw_parts", "fw_finale", "fw_finale_len",
              "fw_build", "fw_blend", "fg", "fg_image", "fg_light")

MOTIONS = {"still": "Still", "drift": "Slow drift", "zoom": "Slow zoom in and out", "breathe": "Breathing light",
           "grain": "Film grain"}


def _motion_filter(motion, w, h, fps):
    """Movement for a still background, as ffmpeg filters (after it's scaled to w x h)."""
    if motion == "drift":  # slide sideways and back over 40 s, from a slightly bigger picture
        W, H = int(w * 1.08) // 2 * 2, int(h * 1.08) // 2 * 2
        return f",scale={W}:{H},crop={w}:{h}:x='(iw-ow)/2*(1+sin(2*PI*t/40))':y='(ih-oh)/2*(1+sin(2*PI*t/57))'"
    if motion == "zoom":  # 0-7 % zoom over 30 s
        return (f",zoompan=z='1+0.035*(1-cos(2*PI*on/({fps}*30)))':d=1:s={w}x{h}:fps={fps}"
                f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
    if motion == "breathe":
        return ",eq=eval=frame:brightness='0.04*sin(2*PI*t/8)':saturation='1+0.1*sin(2*PI*t/8)'"
    if motion == "grain":
        return ",noise=alls=12:allf=t,vignette=PI/5"
    return ""


def _still_once(bg_args):
    """["-loop", "1", ("-framerate", N,) "-i", pic] -> (["-i", pic], True): ffmpeg would otherwise decode
    and rescale the picture for every frame, which made long videos several times slower."""
    a = list(bg_args)
    if a[:2] != ["-loop", "1"] or "-i" not in a:
        return a, False
    i = a.index("-i")
    if any(x not in ("-loop", "1", "-framerate") and not str(x).isdigit() for x in a[:i]):
        return a, False
    return a[i:i + 2] + a[i + 2:], True


def reactive_render(bg_args, audio_path, out, w, h, fps, dur, opts, start=0.0, afade="", with_audio=True,
                    vertical=False, n_frames=None, spin_period=12.0, progress=None, cancel=None, enc=None,
                    motion="still", text_png=None, ass=None, bg_dim=None):
    """Background + visualizer + bumping element, all locked to the audio.
    enc: export settings from encoder(); None = the YouTube defaults (H.264 MP4, AAC 320k).
    motion: movement for a still background (see MOTIONS). text_png: title overlay kept still on top."""
    vis, bump = opts.get("visualizer", "none"), opts.get("bump", "none")
    rgb = reactive.pick_color(opts.get("vis_color", "white"), opts.get("image"))
    vis2 = opts.get("vis2") or "none"
    rgb2 = reactive.pick_color(opts.get("vis2_color") or opts.get("vis_color", "white"), opts.get("image"))
    n_frames = n_frames or int(round(dur * fps))
    args = list(_still_once(bg_args)[0]) + (["-ss", str(start)] if start else []) + ["-t", str(dur), "-i", audio_path]
    darken = ""
    if bg_dim:  # your clips, made darker so the visualizer and text stand out
        k = max(0.1, 1 - float(bg_dim))
        darken = f",colorchannelmixer=rr={k:.3f}:gg={k:.3f}:bb={k:.3f}"
    env = None
    beat = ""
    if fx.active(opts):  # zoom punch, shake, flash, colour by section: on the background only
        env = reactive.bass_envelope(audio_path, fps, start, dur, n_frames)
        grid = reactive.beat_grid(audio_path, start, dur) if (opts.get("fx_zoom") or opts.get("fx_shake")) else (120.0, 0.0)
        beat = fx.beat_filter(opts, env, w, h, fps, _mkdtemp(prefix="rk_fx_"), grid)
    head = f"[0:v]fps={fps},scale={w}:{h}"
    bg_args, still = _still_once(bg_args)
    if still:  # a picture: decode and scale it once, then repeat that frame (not once per frame)
        head = f"[0:v]scale={w}:{h},loop=loop=-1:size=1:start=0,setpts=N/{fps}/TB,fps={fps}"
    fc = [f"{head}{_motion_filter(motion, w, h, fps)}{darken},setsar=1,format=yuv420p{beat}[bg]",
          f"[1:a]{afade + ',' if afade else ''}asplit=2[aout][av]" if with_audio else "[1:a]anull[av]"]
    cur = "bg"
    feed = None
    # up to two visualizers: drawn ones go in one streamed layer, ffmpeg ones are filters on the audio
    kinds = [k for k in dict.fromkeys([vis2, vis]) if k and k != "none"]  # second one sits behind
    drawn_k = [k for k in kinds if k in drawn.DRAWN]
    classic_k = [k for k in kinds if k not in drawn.DRAWN]
    if opts.get("fireworks"):
        drawn_k = ["fireworks"] + drawn_k  # in the sky, behind the visualizers
    fg_kind = _fg_kind(opts)
    fg_in_layer = bool(drawn_k)  # in front of fireworks and drawn scenes; bars and waves stay in front of it
    if drawn_k:
        # drawn in Python (with the bump element in the middle) and streamed in as one transparent layer
        lw, lh, feed = drawn.feeder(drawn_k, audio_path, w, h, fps, start, dur, n_frames, rgb, opts, vertical,
                                    spin_period, rgb2=rgb2 if vis2 in drawn_k and len([k for k in drawn_k if k != "fireworks"]) > 1 else None,
                                    with_fg=fg_in_layer)
        args += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{lw}x{lh}", "-framerate", str(fps), "-i", "pipe:0"]
        scale = "" if (lw, lh) == (w, h) else f"scale={w}:{h}:flags=bicubic,"
        fc += [f"[2:v]{scale}format=yuva420p[lay]", f"[{cur}][lay]overlay=0:0:format=yuv420[v1]"]  # no RGB round trip
        cur = "v1"
        bump = "none"  # already in the layer
    if fg_kind != "none" and not fg_in_layer:  # foreground over the background, under bars and waves
        fg = fireworks.make_foreground(fg_kind, w, h)
        if fg is not None:
            fg_png = os.path.join(_mkdtemp(prefix="rk_fg_"), "foreground.png")
            fg.save(fg_png)
            fc += [f"movie='{ff.esc_path(fg_png)}',format=rgba[fgI]", f"[{cur}][fgI]overlay=0:0[vfg]"]
            cur = "vfg"
    if classic_k:
        srcs = ["av"] if len(classic_k) == 1 else ["av1", "av2"]
        if len(classic_k) == 2:
            fc.append("[av]asplit=2[av1][av2]")
        pos_main = opts.get("vis_pos") or "bottom"
        for j, (k, a) in enumerate(zip(classic_k, srcs)):
            is_main = k == vis
            pos_k = pos_main if is_main else (opts.get("vis2_pos") or pos_main)  # layered behind the main one
            chain, pos = reactive.vis_chain(k, w, h, fps, vertical, rgb if is_main else rgb2,
                                            opts.get("vis_size") or 1.0, pos_k, lift=bool(opts.get("_lift")))
            fc += [f"[{a}]{chain}[vis{j}]", f"[{cur}][vis{j}]overlay={pos}:shortest=1[vc{j}]"]
            cur = f"vc{j}"
    else:
        fc.append("[av]anullsink")
    if bump != "none":
        amount = float(opts.get("bump_amount", 0.12))
        cw, ch = reactive.element_canvas(bump, w, h, vertical, opts.get("center_size"), opts.get("logo"), amount)
        tiles = reactive.bump_frames(bump, (cw, ch), amount, image=opts.get("image"),
                                     logo=opts.get("logo"), title=opts.get("title", ""),
                                     artist=opts.get("artist", ""), rgb=rgb)
        env = reactive.bass_envelope(audio_path, fps, start, dur, n_frames)
        feed = reactive.frame_feeder(env, tiles)
        args += ["-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{cw}x{ch}", "-framerate", str(fps), "-i", "pipe:0"]
        b = "[2:v]"
        if bump == "disc":
            fc.append(f"{b}rotate=a='2*PI*t/{spin_period:.4f}':c=none:ow={cw}:oh={ch}[bp]")
            b = "[bp]"
        lift = 0.07 if vis in reactive.BOTTOM and (opts.get("vis_pos") or "bottom") == "bottom" else 0.0
        y = f"(H-h)/2-H*{lift + (0.06 if vertical else 0):.2f}"
        fc.append(f"[{cur}]{b}overlay=(W-w)/2:{y}[v2]")
        cur = "v2"
    if opts.get("overlays"):  # rain, snow, dust, light leaks, lens flare, VHS
        lines, cur = fx.overlay_filters(opts, cur, w, h, fps, cancel)
        fc += lines
    if text_png:  # title that stays put while the background moves
        fc += [f"movie='{ff.esc_path(text_png)}',format=rgba[txt]", f"[{cur}][txt]overlay=0:0[vt]"]
        cur = "vt"
    if ass:  # progress bar, timer, "now playing" (and lyrics and titles) drawn on top of everything
        fc.append(f"[{cur}]{overlay.filter_for(ass, ff.esc_path)}[vo]")
        cur = "vo"
    fc.append(f"[{cur}]format={enc['pix_fmt'] if enc else 'yuv420p'}[v]")
    args += ["-filter_complex", ";".join(fc), "-map", "[v]"]
    args += (["-map", "[aout]"] + (enc["audio"] if enc else AAC)) if with_audio else ["-an"]
    args += (enc["video"] if enc else _x264(fps)) + ["-frames:v", str(n_frames)]
    args += (["-movflags", "+faststart"] if not enc or enc["faststart"] else []) + [out]
    ff.run(args, dur, progress, cancel, feed=feed)
    return out


def centre_canvas(w, h, vertical=False, scale=None):
    """Square tile size for the cover/disc in the middle (kept for older callers)."""
    return reactive.element_canvas("cover", w, h, vertical, scale)[0]


def style_defaults(opts):
    """Fill in what a visualizer needs: drawn ring/particle styles are built around the cover,
    so they get it in the middle when no bump was chosen. Returns the layout to use."""
    vis, bump = opts.get("visualizer", "none"), opts.get("bump", "none")
    if (vis in drawn.CENTERED or opts.get("vis2") in drawn.CENTERED) and bump == "none" and opts.get("image"):
        opts["bump"] = bump = "cover"
        opts["bump_amount"] = min(float(opts.get("bump_amount") or 0.06), 0.06)
    return "blur" if bump != "none" or vis == "scope" or vis in drawn.DRAWN or opts.get("vis2") in drawn.DRAWN else None


def _reactive_on(opts):
    return bool(opts) and (opts.get("visualizer", "none") != "none" or opts.get("bump", "none") != "none"
                           or (opts.get("vis2") or "none") != "none" or fx.active(opts) or bool(opts.get("overlays"))
                           or bool(opts.get("fireworks")) or _fg_kind(opts) != "none")


def _fg_kind(opts):
    k = (opts or {}).get("fg") or "none"
    if k == "custom":
        k = opts.get("fg_image") or "none"
        if k != "none" and not os.path.isfile(k):
            k = "none"
    return k


# ---------------------------------------------------------------- standard release video

@cleans_temp
def release_video(audio_path, image, out, title="", artist="", resolution="1080p", visualizer="none",
                  layout="cover", text=True, fps=None, start=0.0, length=None, fade=True,
                  vertical=False, progress=None, cancel=None, opts=None, size=None, enc=None, chapters=None):
    """opts: extra reactive options {bump, logo, vis_color, bump_amount}.
    size: (w, h) to override resolution/vertical. enc: export settings from encoder().
    chapters: [(seconds, title)] for "now playing" titles in playlist videos."""
    opts = dict(opts or {}, visualizer=visualizer, image=image, title=title, artist=artist)
    centre_taken = style_defaults(opts)
    bg = opts.get("bg")  # chosen in the editor: cover, full, blur, dark
    if bg in ("full", "blur", "dark"):
        layout = bg
    elif centre_taken:
        layout = "blur"
    if bg == "cover" and not centre_taken:
        layout = "cover"
    if opts.get("bump", "none") == "text":
        text = False
    motion = opts.get("motion") if opts.get("motion") in MOTIONS else "still"
    dim = opts.get("dim")
    dim = None if dim in (None, "") else float(dim)
    if enc and enc.get("fps"):
        fps = int(enc["fps"])  # the export's frame rate decides, or the length comes out wrong
    if size:
        w, h = size
        vertical = h > w
    else:
        w, h = (1080, 1920) if vertical else RES[resolution]
    fps = fps or (30 if _reactive_on(opts) or vertical else 12)
    tmp = _mkdtemp(prefix="rk_")
    vis_room = (bool(enc) or layout == "full") and (visualizer in reactive.BOTTOM or opts.get("vis2") in reactive.BOTTOM) \
        and (opts.get("vis_pos") or "bottom") == "bottom"
    clips = [c for c in (opts.get("clips") or []) if c and os.path.isfile(c)] if bg == "clips" else []
    prog_bar = opts.get("progress") if opts.get("progress") in ("bar", "bar_time") else "off"
    bottom_taken = prog_bar != "off" or bool(chapters)  # progress bar / "now playing" live at the bottom
    # the title must not slide around with the picture, or vanish behind a full-screen scene / your clips
    moving = motion in ("drift", "zoom") or visualizer in drawn.OPAQUE or bool(clips) \
        or bool(float(opts.get("fx_zoom") or 0) or float(opts.get("fx_shake") or 0))
    frame = make_frame(image, os.path.join(tmp, "frame.png"), w, h, title, artist, layout, text and not moving,
                       vis_room=vis_room, dim=dim, text_top=bottom_taken)
    text_png = None
    if moving and text and (title or artist):
        text_png = _text_png(os.path.join(tmp, "text.png"), w, h, title, artist,
                             "top" if centre_taken or visualizer in reactive.BOTTOM or opts.get("vis2") in reactive.BOTTOM
                             or bottom_taken else "bottom")
    dur = ff.duration(audio_path) - start
    if length:
        dur = min(dur, length)
    afade = ""
    if fade:
        fo = 1.5 if dur < 90 else 3
        afade = f"afade=t=in:st=0:d={0.3 if start else 0.05},afade=t=out:st={max(0, dur - fo)}:d={fo}"
    ass = None
    if prog_bar != "off" or chapters:
        shifted = [(max(0.0, t0 - start), name) for t0, name in (chapters or []) if t0 - start < dur]
        ass = overlay.build(os.path.join(tmp, "overlay.ass"), w, h, dur,
                            reactive.pick_color(opts.get("vis_color", "white"), image), prog_bar, shifted,
                            now_playing=bool(chapters) and opts.get("now_playing", True) is not False)
        if chapters and opts.get("now_playing", True) is not False:
            opts = dict(opts, _lift=True)  # bars and waves sit above the "Now playing" line
    if enc or motion != "still" or ass or clips:
        # export settings / moving background / overlays / clips: one pass with everything
        if (motion != "still" or clips) and not _reactive_on(opts):
            fps = max(fps, 24)
        bg_args = ["-loop", "1", "-framerate", str(fps), "-i", frame]
        if clips:
            bg = clips_background(clips, w, h, fps, dur, audio_path, start, opts.get("clip_switch", "beat"),
                                  int(opts.get("clip_bars") or 4), opts.get("clip_fit", "fill"), tmp=tmp,
                                  progress=_stage(progress, 0, 0.25), cancel=cancel)
            bg_args = ["-stream_loop", "-1", "-i", bg]
            progress = _stage(progress, 0.25, 1.0)
        reactive_render(bg_args, audio_path, out, w, h, fps, dur, opts,
                        start=start, afade=afade, vertical=vertical, progress=progress, cancel=cancel, enc=enc,
                        motion="still" if clips else motion, text_png=text_png, ass=ass,
                        bg_dim=(dim if dim is not None else 0.25) if clips else None)
        return {"path": out, "duration": dur}
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


@cleans_temp
def album_video(tracks, image, out, titles=None, album_title="", artist="", resolution="1080p",
                visualizer="none", gap=1.0, progress=None, cancel=None, opts=None, size=None, enc=None, text=True):
    """All tracks back to back in one video, with chapters, and "now playing" titles if the look has them on."""
    titles = titles or [os.path.splitext(os.path.basename(t))[0] for t in tracks]
    tmp = _mkdtemp(prefix="rk_")
    joined = os.path.join(tmp, "album.wav")
    audio.build_cycle(tracks, joined, crossfade=0, gap=gap, progress=_stage(progress, 0, 0.15), cancel=cancel)
    chapters, t = [], 0.0
    for path, name in zip(tracks, titles):
        chapters.append((t, name))
        t += ff.duration(path) + gap
    tmp_out = os.path.join(tmp, "album_nochap" + os.path.splitext(out)[1])
    release_video(joined, image, tmp_out, album_title, artist, resolution, visualizer, text=text, fade=False,
                  progress=_stage(progress, 0.15, 0.97), cancel=cancel, opts=opts, size=size, enc=enc,
                  chapters=chapters if len(chapters) > 1 else None)
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
        u = min(w, h)  # text sized by the short side, so vertical videos don't get giant titles
        f_a, f_t = art.load_font(int(u * 0.035)), art.load_font(int(u * 0.065))
        d = ImageDraw.Draw(ov)
        block = int(u * 0.065) + int(u * 0.05)
        y = pad if pos == "top" else h - pad - block
        # soft dark band for legibility on busy footage
        band = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle((0, 0, w, y + block + pad) if pos == "top" else (0, y - pad, w, h),
                                       fill=(0, 0, 0, 110))
        ov = Image.alpha_composite(ov, band.filter(ImageFilter.GaussianBlur(pad)))
        d = ImageDraw.Draw(ov)
        if artist:
            d.text((pad, y), artist.upper(), font=f_a, fill=(255, 255, 255, 230))
        d.text((pad, y + int(u * 0.045)), title, font=f_t, fill=(255, 255, 255, 255))
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

@cleans_temp
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
    blur_layout = style_defaults(opts)
    reactive_mode = _reactive_on(opts)
    bump = opts.get("bump", "none")
    if bump == "text":
        text = False
    w, h = RES[resolution]
    target = float(hours) * 3600
    tmp = _mkdtemp(prefix="rk_long_")
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
                              layout=blur_layout or "cover")
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


# ---------------------------------------------------------------- export settings

EXPORT_FORMATS = {
    "mp4": {"label": "MP4 (H.264): plays everywhere", "ext": ".mp4"},
    "mov": {"label": "MOV (ProRes 422): for editing in Premiere, Resolve, Final Cut", "ext": ".mov"},
    "webm": {"label": "WebM (VP9): small files for websites", "ext": ".webm"},
}
ASPECTS = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5)}
SIZES = (720, 1080, 1440, 2160)  # the short side
FPS_CHOICES = (24, 25, 30, 50, 60)
QUALITIES = ("draft", "standard", "high")


def export_size(aspect="16:9", size=1080):
    """(w, h) for an aspect ratio, `size` being the short side. Always even numbers."""
    a, b = ASPECTS.get(aspect, (16, 9))
    short = int(size) if 144 <= int(size) <= 4320 else 1080  # SIZES are the choices offered; previews go smaller
    even = lambda v: int(round(v / 2)) * 2
    return (even(short * a / b), short) if a >= b else (short, even(short * b / a))


def encoder(fmt="mp4", quality="standard", fps=30):
    """ffmpeg output settings for an export: video args, audio args, pixel format, file extension."""
    fmt = fmt if fmt in EXPORT_FORMATS else "mp4"
    q = QUALITIES.index(quality) if quality in QUALITIES else 1
    fps = int(fps)
    gop = ["-r", str(fps), "-g", str(fps * 2)]
    if fmt == "mov":
        video = ["-c:v", "prores_ks", "-profile:v", ("0", "2", "3")[q], "-vendor", "apl0", "-r", str(fps)]
        return {"video": video, "audio": ["-c:a", "pcm_s16le", "-ar", "48000"], "pix_fmt": "yuv422p10le",
                "ext": ".mov", "faststart": True, "format": fmt, "fps": fps}
    if fmt == "webm":
        video = (["-c:v", "libvpx-vp9", "-crf", ("40", "32", "24")[q], "-b:v", "0", "-row-mt", "1",
                  "-deadline", ("realtime", "good", "good")[q], "-cpu-used", ("8", "4", "2")[q]] + gop)
        return {"video": video, "audio": ["-c:a", "libopus", "-b:a", "256k", "-ar", "48000"],
                "pix_fmt": "yuv420p", "ext": ".webm", "faststart": False, "format": fmt, "fps": fps}
    video = ff.h264(fps, q)  # graphics card when available
    return {"video": video, "audio": AAC, "pix_fmt": "yuv420p", "ext": ".mp4", "faststart": True, "format": fmt, "fps": fps}


def export_bytes(fmt, w, h, fps, seconds, quality="standard"):
    """Rough file size, for the disk-space check."""
    scale = (w * h * int(fps)) / (1920 * 1080 * 30)
    mbps = {"mp4": 8, "webm": 5, "mov": (45, 147, 220)[QUALITIES.index(quality) if quality in QUALITIES else 1]}
    audio_kbps = 1536 if fmt == "mov" else 320
    return int(seconds * (mbps.get(fmt, 8) * 1e6 * scale + audio_kbps * 1000) / 8)


@cleans_temp
def export_video(audio_path, image, out, title="", artist="", visualizer="none", layout="cover", text=True,
                 fmt="mp4", aspect="16:9", size=1080, fps=30, quality="standard", start=0.0, length=None,
                 fade=True, progress=None, cancel=None, opts=None):
    """A song video with your own export settings (format, shape, size, frame rate, quality, section)."""
    enc = encoder(fmt, quality, fps)
    res = release_video(audio_path, image, out, title, artist, visualizer=visualizer, layout=layout, text=text,
                        fps=int(fps), start=float(start or 0), length=length, fade=fade,
                        size=export_size(aspect, size), enc=enc, progress=progress, cancel=cancel, opts=opts)
    res.update(format=enc["format"], size="%dx%d" % export_size(aspect, size), fps=int(fps))
    return res


# ---------------------------------------------------------------- visualizer samples

def demo_beat(out, seconds=12, bpm=120):
    """A short synthetic stereo groove (kick, hats, bass, chord pad, arpeggio) so the samples have
    something to react to before you've added any music."""
    p = 60.0 / bpm
    tb = f"mod(t,{p:.5f})"
    root = f"if(lt(mod(t,{8 * p:.5f}),{4 * p:.5f}),55,43.65)"  # A then F, two bars each
    def side(ch):  # ch 0 = left, 1 = right: phase and pan differences make the stereo scope open up
        ph = 0.35 * ch
        kick = f"0.85*sin(2*PI*(45*{tb}+3.2*(1-exp(-{tb}*28)))+{0.9 * ch})*exp(-{tb}*7)"
        bass = f"0.28*sin(2*PI*{root}*t+{1.4 * ch})*(0.55+0.45*exp(-{tb}*3))"
        hat = f"0.16*(random({ch})*2-1)*exp(-mod(t+{p / 2:.5f},{p:.5f})*45)"
        shaker = f"0.05*(random({ch + 2})*2-1)*exp(-mod(t,{p / 4:.5f})*70)"
        # chord pad: three detuned saw-like voices (a few harmonics each), swelling every two beats
        voices = []
        for v, (mult, det) in enumerate(((4, 1.0), (5.04, 1.003), (6, 0.997))):
            f = f"{root}*{mult * (det + 0.002 * ch):.4f}"
            gain = (1.4, 1.0, 0.6)[v] if ch == 0 else (0.6, 1.0, 1.4)[v]  # spread the voices left to right
            voices.append(f"{gain}*(" + "+".join(f"sin(2*PI*{f}*{k}*t+{ph * k:.2f})/{k}" for k in (1, 2, 3, 4, 5)) + ")")
        pad = f"0.035*({'+'.join(voices)})*(0.55+0.45*sin(2*PI*t/{2 * p:.5f}+{ph:.2f}))"
        step = f"floor(t/{p / 4:.5f})"
        arp_f = f"{root}*8*if(eq(mod({step},4),0),1,if(eq(mod({step},4),1),1.5,if(eq(mod({step},4),2),2,1.26)))"
        pan = f"if(eq(mod({step},2),{ch}),1,0.25)"  # the arpeggio bounces between the speakers
        arp = f"0.12*{pan}*sin(2*PI*{arp_f}*t)*exp(-mod(t,{p / 4:.5f})*9)"
        return f"({kick})+({bass})+({hat})+({shaker})+({pad})+({arp})"

    ff.run(["-f", "lavfi", "-i", f"aevalsrc=exprs='{side(0)}|{side(1)}':s=48000:d={seconds}",
            "-af", "alimiter=limit=0.9,afade=t=in:d=0.05", "-c:a", "pcm_s16le", out])
    return out


SAMPLE_KINDS = ("bars", "mirror", "waves", "line", "scope", "spectrum",
                "ring", "ringwave", "particles", "geometric", "tunnel", "synthwave",
                "starfield", "mystify", "pipes", "maze")


@cleans_temp
def visualizer_samples(audio_path, image, out_dir, title="", artist="", seconds=6.0, start=None, opts=None,
                       kinds=SAMPLE_KINDS, aspect="16:9", progress=None, cancel=None):
    """Short low-res clips of every visualizer with your music, cover and style, to compare side by side.
    With no track (or no cover) a demo beat (or generated art) stands in."""
    os.makedirs(out_dir, exist_ok=True)
    tmp = _mkdtemp(prefix="rk_samples_")
    demo = not audio_path
    if demo:
        audio_path = demo_beat(os.path.join(tmp, "demo.wav"), seconds=max(8, seconds + 2))
        start = 0.0 if start is None else start
    if not image:
        image = art.make_cover(None, "generate", os.path.join(tmp, "demo_cover.jpg"), generate_style="aurora",
                               seed=7)["path"]
    if start is None:
        start = audio.loudest_window(audio_path, seconds)
    w, h = export_size(aspect, 720)
    w, h = (640, int(round(640 * h / w / 2)) * 2) if w >= h else (int(round(640 * w / h / 2)) * 2, 640)
    enc = encoder("mp4", "draft", 30)
    from .reactive import VISUALIZERS
    samples = []
    for i, kind in enumerate(kinds):
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        path = os.path.join(out_dir, f"sample_{kind}.mp4")
        release_video(audio_path, image, path, title or ("Demo beat" if demo else ""), artist, visualizer=kind,
                      fps=30, start=start, length=seconds, size=(w, h), enc=enc, opts=opts, cancel=cancel,
                      progress=_stage(progress, i / len(kinds), (i + 1) / len(kinds)))
        samples.append({"visualizer": kind, "label": VISUALIZERS.get(kind, kind), "path": path})
    if progress:
        progress(1.0)
    return {"samples": samples, "files": [s["path"] for s in samples], "start": start, "demo": demo,
            "aspect": aspect, "track": None if demo else audio_path}


# ---------------------------------------------------------------- Shorts / Reels / TikTok

@cleans_temp
def short_clip(audio_path, image, out, title="", artist="", seconds=30, start=None, visualizer="waves",
               progress=None, cancel=None, opts=None):
    if start is None:
        start = audio.loudest_window(audio_path, seconds)
    res = release_video(audio_path, image, out, title, artist, visualizer=visualizer, start=start,
                        length=seconds, vertical=True, progress=progress, cancel=cancel, opts=opts)
    res["start"] = start
    return res


# ---------------------------------------------------------------- Spotify Canvas

@cleans_temp
def canvas(image, out, seconds=7.5, progress=None, cancel=None):
    """3-8 s vertical 1080x1920 seamless loop, no audio (upload in Spotify for Artists).
    Kept just under 8 s because uploads that are even slightly long get rejected."""
    seconds = max(3, min(7.9, float(seconds)))
    _clip_zoom(image, out, 1080, 1920, "", "", False, seconds=seconds, fps=30, amount=0.06, cancel=cancel)
    if progress:
        progress(1.0)
    return {"path": out, "duration": seconds}

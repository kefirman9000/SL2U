"""Release Kit - local web app. Run:  python -m releasekit  (then open http://127.0.0.1:5577)"""
import io
import json
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
import traceback
import uuid
import zlib

from flask import Flask, abort, jsonify, request, send_file
from PIL import Image

from . import __version__, art, audio, drawn, ff, fx, links, meta, packages, reactive, schedule, security, sl2link, update, video, youtube

DATA_DIR = os.environ.get("RELEASEKIT_HOME", os.path.join(os.path.expanduser("~"), "ReleaseKit"))
PROJECTS = os.path.join(DATA_DIR, "projects")
INBOX = os.path.join(DATA_DIR, "inbox")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
HAD_SETTINGS = os.path.exists(SETTINGS)
for d in (DATA_DIR, PROJECTS):
    os.makedirs(d, exist_ok=True)

AUDIO_EXT = (".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus")
VIDEO_EXT = (".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi")

app = Flask(__name__, static_folder=None)


def port():
    return int(os.environ.get("RELEASEKIT_PORT", 5577))


GUARD = security.Guard(app, DATA_DIR, port)
video.CACHE_DIR = fx.CACHE_DIR = os.path.join(DATA_DIR, "cache")  # resized clips, effect loops, thumbnails


# ---------------------------------------------------------------- settings / projects

def load_settings():
    if os.path.exists(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    return {"output_dir": os.path.join(DATA_DIR, "output"), "profile": {}, "signups": {}, "stock_author": ""}


_settings_lock = threading.Lock()


def save_settings(s):
    with _settings_lock:
        with open(SETTINGS + ".tmp", "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        os.replace(SETTINGS + ".tmp", SETTINGS)


def update_settings(**kw):
    s = load_settings()
    s.update(kw)
    save_settings(s)
    return s


def out_dir_for(r):
    s = load_settings()
    d = os.path.join(s.get("output_dir") or os.path.join(DATA_DIR, "output"),
                     packages.safe(f"{r.get('artist', 'Artist')} - {r.get('title', 'Release')}"))
    os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- job queue

JOBS = {}
Q = queue.Queue()
LOCK = threading.Lock()
JOBS_FILE = os.path.join(DATA_DIR, "jobs.json")


def persist_jobs():
    """Keep the last 200 jobs on disk so the list survives closing the app."""
    with LOCK:
        keep = sorted(JOBS.values(), key=lambda j: -j["created"])[:200]
        data = [{k: v for k, v in j.items() if k not in ("cancel", "trace")} for j in keep]
    try:
        with open(JOBS_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, default=str)
        os.replace(JOBS_FILE + ".tmp", JOBS_FILE)
    except OSError:
        pass


def load_jobs():
    if not os.path.exists(JOBS_FILE):
        return
    try:
        with open(JOBS_FILE, encoding="utf-8") as f:
            for j in json.load(f):
                if j["status"] in ("queued", "running"):
                    j["status"] = "interrupted"
                    j["error"] = "Release Kit was closed before this finished. Run it again."
                j["cancel"] = False
                JOBS[j["id"]] = j
    except Exception:
        pass


def new_job(kind, label, fn, status="queued", result=None):
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "kind": kind, "label": label, "status": status, "progress": 1.0 if status == "done" else 0.0,
           "created": time.time(), "result": result, "error": None, "cancel": False}
    with LOCK:
        JOBS[jid] = job
    if fn:
        Q.put((jid, fn))
    persist_jobs()
    return job


def worker():
    while True:
        jid, fn = Q.get()
        with LOCK:
            job = JOBS.get(jid)
            if job is None:  # cleared from the list before it started
                continue
            if job["cancel"]:
                job["status"] = "cancelled"
            else:
                job["status"] = "running"
                job["started"] = time.time()
        if job["status"] == "cancelled":
            persist_jobs()
            continue
        persist_jobs()

        def prog(f, job=job):
            job["progress"] = round(float(f), 4)

        try:
            job["result"] = fn(prog, lambda job=job: job["cancel"])
            job["status"] = "done"
            job["progress"] = 1.0
        except Exception as e:
            job["status"] = "cancelled" if job["cancel"] else "error"
            job["error"] = str(e)
            job["hint"] = ff.friendly(e)
            job["trace"] = traceback.format_exc()[-3000:]
        job["finished"] = time.time()
        persist_jobs()


load_jobs()


def estimate_long_bytes(hours, resolution="1080p", visual="static"):
    video_kbps = {"720p": 150, "1080p": 250, "1440p": 450, "4k": 900}.get(resolution, 250)
    if visual != "static":
        video_kbps *= 6
    return int(float(hours) * 3600 * (320 + video_kbps) * 1000 / 8)


def check_space(path, need):
    free = shutil.disk_usage(path).free
    if free < need * 1.3:
        raise ValueError(f"Not enough disk space: this needs about {need / 1e9:.1f} GB plus working room, "
                         f"but only {free / 1e9:.1f} GB is free in {path}.")


# Two renders at a time, so a quick Short or cover isn't stuck for hours behind a 10-hour loop.
# RELEASEKIT_WORKERS changes it (1 = strictly one after the other).
WORKERS = max(1, min(4, int(os.environ.get("RELEASEKIT_WORKERS", "2") or 2)))
for _ in range(WORKERS):
    threading.Thread(target=worker, daemon=True).start()


def _first_image(r, p):
    return p.get("image") or r.get("art_path")


def _require(cond, msg):
    if not cond:
        raise ValueError(msg)


def build_job(kind, r, p):
    """Return (label, fn(progress, cancel)) for a job type."""
    out = out_dir_for(r)
    tracks = [t for t in r.get("tracks", []) if t.get("path")]
    title, artist = r.get("title", ""), r.get("artist", "")
    sel = p.get("track_index")
    track = tracks[int(sel)] if sel not in (None, "") and tracks else (tracks[0] if tracks else None)
    t_title = (track or {}).get("title") or title
    text = p.get("text", True)
    style = p.get("vstyle") if isinstance(p.get("vstyle"), dict) else {}
    vopts = {k: style.get(k) for k in video.STYLE_KEYS if style.get(k) not in (None, "")}
    # words shown on the video (the editor can override them; file names keep the real titles)
    say = lambda real: style.get("text_title") or real
    by = style.get("text_artist") or artist
    vis_global = style.get("visualizer") or "none"
    if vopts.get("bump") == "logo" and not (vopts.get("logo") and os.path.isfile(vopts["logo"])):
        vopts["bump"] = "cover"  # no logo chosen (or it was moved): use the cover rather than refuse

    if kind == "analyze":
        def fn(prog, cancel):
            res = []
            for i, t in enumerate(tracks):
                a = audio.analyze(t["path"])
                a["title"] = t.get("title")
                res.append(a)
                prog((i + 1) / len(tracks))
            return {"tracks": res}
        return "Analyze audio", fn

    if kind == "cover":
        def fn(prog, cancel):
            path = os.path.join(out, "artwork", f"cover_{int(time.time())}.jpg")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return art.make_cover(source=p.get("source"), mode=p.get("mode", "smart"), out=path,
                                  title=title, artist=artist, text_position=p.get("text_position", "bottom"),
                                  font_style=p.get("font", "sans"), generate_style=p.get("style", "aurora"),
                                  seed=p.get("seed"), explicit_label=bool(p.get("explicit_label")),
                                  palette_from=p.get("palette_from"), add_title=bool(p.get("add_title")))
        return "Make cover", fn

    if kind == "variants":
        _require(r.get("art_path"), "Set a cover first (Artwork tab).")
        return "Artwork variants", lambda prog, cancel: {
            "files": art.export_variants(r["art_path"], os.path.join(out, "artwork", "variants"), title, artist)}

    if kind == "contact_sheet":
        def fn(prog, cancel):
            ranked = art.score_photos(p["folder"])
            top = [x["path"] for x in ranked if not x.get("error")][: int(p.get("count", 25))]
            path = os.path.join(out, "artwork", "contact_sheet.jpg")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            return {"path": art.contact_sheet(top, path), "ranked": top}
        return "Photo contact sheet", fn

    if kind == "release_video":
        _require(track, "Add a track first.")
        img = _first_image(r, p)
        _require(img, "Set a cover or choose an image.")
        vis = p.get("visualizer") or vis_global
        name = packages.safe(f"{artist} - {t_title}{' (Visualizer)' if vis != 'none' else ''}") + ".mp4"
        return f"YouTube video: {t_title}", lambda prog, cancel: video.release_video(
            track["path"], img, os.path.join(out, "youtube", name), say(t_title), by, p.get("resolution", "1080p"),
            vis, p.get("layout", "cover"), text, progress=prog, cancel=cancel, opts=vopts)

    if kind == "export_video":
        _require(track, "Add a track first.")
        img = _first_image(r, p)
        _require(img, "Set a cover or choose an image.")
        ex = p.get("export") if isinstance(p.get("export"), dict) else {}
        fmt = ex.get("format") if ex.get("format") in video.EXPORT_FORMATS else "mp4"
        aspect = ex.get("aspect") if ex.get("aspect") in video.ASPECTS else "16:9"
        size = int(ex.get("size") or 1080)
        _require(size in video.SIZES, "Choose a video size.")
        fps = int(ex.get("fps") or 30)
        _require(fps in video.FPS_CHOICES, "Choose a frame rate.")
        quality = ex.get("quality") if ex.get("quality") in video.QUALITIES else "standard"
        start = float(ex.get("start") or 0)
        length = float(ex["length"]) if ex.get("length") not in (None, "", 0, "0") else None
        full = ff.duration(track["path"])
        _require(0 <= start < full, f"Start is past the end of the track ({full:.0f} s long).")
        seconds = min(full - start, length or full)
        w, h = video.export_size(aspect, size)
        check_space(out, video.export_bytes(fmt, w, h, fps, seconds, quality))
        vis = p.get("visualizer") or vis_global
        vis_name = reactive.VISUALIZERS.get(vis, vis) if vis != "none" else ""
        tag = " - ".join(x for x in (vis_name, aspect.replace(":", "x"), f"{size}p" if size != 2160 else "4K") if x)
        name = packages.safe(f"{artist} - {t_title} ({tag})") + video.EXPORT_FORMATS[fmt]["ext"]
        return f"Export video: {t_title} ({fmt.upper()} {w}×{h} {fps} fps)", lambda prog, cancel: video.export_video(
            track["path"], img, os.path.join(out, "videos", name), say(t_title), by, vis, ex.get("layout", "cover"),
            text, fmt, aspect, size, fps, quality, start, length, bool(ex.get("fade", True)), prog, cancel, opts=vopts)

    if kind == "vis_samples":
        img = _first_image(r, p)  # optional: generated art stands in
        if img and not os.path.isfile(img):
            img = None
        seconds = max(3.0, min(15.0, float(p.get("seconds") or 6)))
        aspect = p.get("aspect") if p.get("aspect") in video.ASPECTS else "16:9"
        start = p.get("start")
        start = float(start) if start not in (None, "") else None
        folder = os.path.join(out if track else os.path.join(DATA_DIR, "samples"), "visualizer samples")
        label = f"Visualizer samples: {t_title}" if track else "Visualizer samples (demo beat)"
        return label, lambda prog, cancel: video.visualizer_samples(
            track["path"] if track else None, img, folder, t_title if track else "", artist, seconds, start, vopts,
            aspect=aspect, progress=prog, cancel=cancel)

    if kind == "album_video":
        _require(len(tracks) > 0, "Add tracks first.")
        img = _first_image(r, p)
        _require(img, "Set a cover first.")
        ex = p.get("export") if isinstance(p.get("export"), dict) else None
        size = enc = None
        ext = ".mp4"
        if ex:  # chosen in the Playlist video form ("Made for")
            fmt = ex.get("format") if ex.get("format") in video.EXPORT_FORMATS else "mp4"
            aspect = ex.get("aspect") if ex.get("aspect") in video.ASPECTS else "16:9"
            sz = int(ex.get("size") or 1080)
            fps = int(ex.get("fps") or 30)
            _require(sz in video.SIZES and fps in video.FPS_CHOICES, "Choose a size and frame rate.")
            size, enc, ext = video.export_size(aspect, sz), video.encoder(fmt, ex.get("quality", "standard"), fps), \
                video.EXPORT_FORMATS[fmt]["ext"]
        name = p.get("name") or (f"{artist} - {title} (Full Album)" if not p.get("playlist") else f"{artist} - {title} (Playlist)")
        label = ("Playlist video" if p.get("playlist") else "Full album video") + f": {len(tracks)} tracks"
        return label, lambda prog, cancel: video.album_video(
            [t["path"] for t in tracks], img, os.path.join(out, "youtube" if not ex else "videos", packages.safe(name) + ext),
            [t.get("title") or os.path.basename(t["path"]) for t in tracks], say(title), by,
            p.get("resolution", "1080p"), p.get("visualizer") or vis_global, float(p.get("gap", 1.0)), prog, cancel,
            opts=vopts, size=size, enc=enc)

    if kind == "long_video":
        idx = p.get("tracks")  # list of indexes, or None for selected track
        chosen = [tracks[int(i)] for i in idx] if idx else ([track] if track else [])
        _require(chosen, "Choose at least one track.")
        hours = float(p.get("hours", 10))
        visual = p.get("visual", "static")
        images = art.list_images(p["photo_folder"]) if visual == "slideshow" and p.get("photo_folder") else None
        if visual == "slideshow":
            _require(images, "Slideshow needs a photo folder with images.")
        img = _first_image(r, p)
        if visual == "clip":
            _require(p.get("video_clip") and os.path.isfile(p["video_clip"]), "Choose a video clip for the loop.")
        if visual == "clips":
            _require(p.get("clip_folder") and os.path.isdir(p["clip_folder"]), "Choose a folder of video clips.")
        if visual not in ("slideshow", "clip", "clips") or vopts.get("bump") in ("cover", "disc"):
            _require(img, "Set a cover or choose an image.")
        check_space(out, estimate_long_bytes(hours, p.get("resolution", "1080p"), visual))
        name_title = chosen[0].get("title") if len(chosen) == 1 else title
        label_h = f"{hours:g}"
        name = packages.safe(f"{artist} - {name_title} ({label_h} Hours)") + ".mp4"
        return f"{label_h}h loop: {name_title}", lambda prog, cancel: video.long_video(
            [t["path"] for t in chosen], os.path.join(out, "youtube", name), hours, visual, img, images,
            say(name_title), by, text, p.get("resolution", "1080p"), float(p.get("crossfade", 4)),
            p.get("ambient") or None, float(p.get("ambient_db", -18)), slide_seconds=float(p.get("slide_seconds", 8)),
            visualizer=p.get("visualizer") or vis_global, video_clip=p.get("video_clip"),
            clip_folder=p.get("clip_folder"), clip_mode=p.get("clip_mode", "crossfade"),
            clip_fit=p.get("clip_fit", "fill"), progress=prog, cancel=cancel, opts=vopts)

    if kind == "batch_item":
        # one track of an overnight batch: (cover) + loop video + paste-ready text file
        hours = float(p.get("hours", 10))
        res = p.get("resolution", "1080p")
        check_space(out, estimate_long_bytes(hours, res, p.get("visual", "static")))
        t = track

        def fn(prog, cancel):
            img = p.get("image")
            slug = packages.safe(t["title"])
            if not img:
                cov = os.path.join(out, "artwork", f"cover_{slug}.jpg")
                os.makedirs(os.path.dirname(cov), exist_ok=True)
                art.make_cover(None, "generate", cov, generate_style=p.get("art_style", "aurora"),
                               seed=zlib.crc32(t["path"].encode()))
                img = cov
            name = packages.safe(f"{artist} - {t['title']} ({hours:g} Hours)")
            vid = os.path.join(out, "youtube", name + ".mp4")
            res_v = video.long_video([t["path"]], vid, hours, p.get("visual", "static"), img, None, t["title"],
                                     artist, p.get("text", True), res, float(p.get("crossfade", 4)),
                                     p.get("ambient") or None, float(p.get("ambient_db", -18)),
                                     visualizer=vis_global, video_clip=p.get("video_clip"),
                                     clip_folder=p.get("clip_folder"), clip_mode=p.get("clip_mode", "crossfade"),
                                     progress=prog, cancel=cancel, opts=vopts)
            rr = dict(r, title=t["title"])
            kind_t = p.get("title_kind", "ten_hours")
            txt = os.path.join(out, "youtube", name + " - YouTube text.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write(meta.youtube_title(rr, kind_t, hours=f"{hours:g}") + "\n\n" +
                        meta.youtube_description(rr, load_settings().get("profile"), kind_t, None, f"{hours:g}") +
                        "\n\nTAGS: " + ", ".join(meta.tags(rr)))
            res_v["text_file"] = txt
            # thumbnail next to the video, picked up automatically by the upload schedule
            thumb = os.path.join(out, "youtube", name + " - thumbnail.jpg")
            th = art.blur_fill(art.open_rgb(img), 1280, 720)
            th = art.add_text(th, f"{t['title']}", f"{hours:g} HOURS", position="bottom-left", scale=1.0)
            th.save(thumb, "JPEG", quality=88)
            res_v["thumbnail"] = thumb
            return res_v
        return f"Batch {hours:g}h: {t['title']}", fn

    if kind == "short":
        _require(track, "Add a track first.")
        img = _first_image(r, p)
        _require(img, "Set a cover first.")
        secs = float(p.get("seconds", 30))
        start = p.get("start")
        start = float(start) if start not in (None, "") else None
        return f"Short/Reel/TikTok: {t_title}", lambda prog, cancel: video.short_clip(
            track["path"], img, os.path.join(out, "social", packages.safe(f"{t_title} short {int(time.time())}") + ".mp4"),
            say(t_title), by, secs, start, p.get("visualizer") or (vis_global if vis_global != "none" else "waves"),
            prog, cancel, opts=vopts)

    if kind == "canvas":
        img = _first_image(r, p)
        _require(img, "Set a cover or choose an image.")
        return "Spotify Canvas", lambda prog, cancel: video.canvas(
            img, os.path.join(out, "spotify", packages.safe(f"{t_title} canvas") + ".mp4"),
            float(p.get("seconds", 7.5)), prog, cancel)

    if kind == "pkg_distro":
        _require(tracks, "Add tracks first.")
        return "Distributor package", lambda prog, cancel: packages.distributor(
            r, out, int(p.get("bits", 16)), int(p.get("sample_rate", 44100)), prog, cancel)

    if kind == "pkg_direct":
        _require(tracks, "Add tracks first.")
        return "Bandcamp + SoundCloud package", lambda prog, cancel: packages.direct(
            r, out, load_settings().get("profile"), prog, cancel)

    if kind == "pkg_stock":
        _require(tracks, "Add tracks first.")
        return "Stock music package", lambda prog, cancel: packages.stock(
            r, out, load_settings().get("stock_author") or artist, bool(p.get("cutdowns", True)), prog, cancel)

    if kind == "pkg_photos":
        _require(p.get("folder"), "Choose a photo folder.")
        return "Photo stock pack", lambda prog, cancel: packages.photo_stock(
            p["folder"], out, p.get("keywords", ""), p.get("description", ""), float(p.get("min_mp", 4)), prog, cancel)

    if kind == "yt_upload":
        _require(youtube.available(), "Install the YouTube extras: pip install -r requirements-youtube.txt")
        _require(p.get("video") and os.path.exists(p["video"]), "Choose a rendered video file.")
        tags = p.get("tags")
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        return f"Upload to YouTube: {p.get('title', '')[:40]}", lambda prog, cancel: youtube.upload(
            DATA_DIR, p["video"], p.get("title", ""), p.get("description", ""), tags, p.get("privacy", "private"),
            p.get("publish_at") or None, p.get("thumbnail") or None, p.get("playlist_id") or None,
            False, prog, cancel)

    raise ValueError(f"Unknown job type {kind}")


# ---------------------------------------------------------------- routes

@app.get("/")
def index():
    return send_file(os.path.join(os.path.dirname(__file__), "templates", "index.html"))


@app.get("/static/<path:name>")
def static_file(name):
    base = os.path.join(os.path.dirname(__file__), "static")
    path = os.path.realpath(os.path.join(base, name))
    if os.path.commonpath([path, os.path.realpath(base)]) != os.path.realpath(base) or not os.path.isfile(path):
        abort(404)
    return send_file(path, max_age=86400)


@app.get("/api/init")
def api_init():
    return jsonify({
        "settings": load_settings(), "directory": links.DIRECTORY, "missing_tools": ff.check_tools(),
        "version": __version__,
        "youtube_available": youtube.available(), "data_dir": DATA_DIR,
        "has_client_secret": os.path.exists(os.path.join(DATA_DIR, "client_secret.json")),
        "projects": sorted(f[:-5] for f in os.listdir(PROJECTS) if f.endswith(".json")),
        "home": os.path.expanduser("~"), "title_kinds": meta.TITLE_TEMPLATES,
        "sl2_default_home": sl2link.default_home(), "inbox": INBOX,
    })


# ---------------------------------------------------------------- your logos and pictures
# Every logo or picture you use is copied into the data folder and remembered (newest first),
# so it's one click next time, even if the original file moves.
ASSET_KINDS = {"logo": "logos", "picture": "pictures", "clip": "clips"}
ASSET_KEEP = 24


def _asset_dir(kind):
    d = os.path.join(DATA_DIR, ASSET_KINDS[kind])
    os.makedirs(d, exist_ok=True)
    return d


def _assets(kind):
    return [p for p in load_settings().get(ASSET_KINDS[kind], []) if os.path.isfile(p)]


def _remember_asset(kind, path):
    with _settings_lock:
        s = load_settings()
        lst = [p for p in s.get(ASSET_KINDS[kind], []) if os.path.normcase(p) != os.path.normcase(path)
               and os.path.isfile(p)]
        s[ASSET_KINDS[kind]] = ([path] + lst)[:ASSET_KEEP]
    save_settings(s)


def _store_asset(kind, data, name):
    """Check it's a picture, save it in the library as PNG (logos keep transparency) or JPG. Returns the path."""
    import hashlib
    try:
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError("That file isn't a picture Release Kit can read. Try a PNG or JPG.")
    stem = packages.safe(os.path.splitext(os.path.basename(name or kind))[0])[:60] or kind
    tag = hashlib.sha1(data).hexdigest()[:8]
    if kind == "logo":
        im = im.convert("RGBA")
        im.thumbnail((2048, 2048))
        path = os.path.join(_asset_dir(kind), f"{stem}-{tag}.png")
        if not os.path.exists(path):
            im.save(path, "PNG")
    else:
        im = im.convert("RGB")
        im.thumbnail((4096, 4096))
        path = os.path.join(_asset_dir(kind), f"{stem}-{tag}.jpg")
        if not os.path.exists(path):
            im.save(path, "JPEG", quality=93)
    _remember_asset(kind, path)
    return path


def _kind_or_404(kind):
    if kind not in ASSET_KINDS:
        abort(404)


@app.get("/api/assets/<kind>")
def api_assets(kind):
    _kind_or_404(kind)
    return jsonify({"items": _assets(kind)})


@app.post("/api/assets/<kind>")
def api_asset_upload(kind):
    """A picture from the browser (Choose… button or drag and drop)."""
    _kind_or_404(kind)
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "No file received."}), 400
    if kind == "clip":  # videos (and photos) for the background: saved as they are
        name = packages.safe(os.path.basename(f.filename or "clip")) or "clip"
        if not name.lower().endswith(VIDEO_EXT + art.IMAGE_EXT + (".gif",)):
            return jsonify({"error": "That isn't a video or picture file."}), 400
        dst = os.path.join(_asset_dir(kind), name)
        base, ext = os.path.splitext(dst)
        n = 1
        while os.path.exists(dst):
            dst = f"{base} ({n}){ext}"
            n += 1
        f.save(dst)
        _remember_asset(kind, dst)
        return jsonify({"path": dst, "items": _assets(kind)})
    try:
        path = _store_asset(kind, f.read(), f.filename)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"path": path, "items": _assets(kind)})


@app.post("/api/assets/<kind>/add")
def api_asset_add(kind):
    """A picture already on this computer (path from the file browser): copied into the library."""
    _kind_or_404(kind)
    path = (request.json or {}).get("path", "")
    if not os.path.isfile(path):
        return jsonify({"error": "That file doesn't exist any more."}), 400
    if kind == "clip":  # clips can be big: remembered where they are, not copied
        if not path.lower().endswith(VIDEO_EXT + art.IMAGE_EXT + (".gif",)):
            return jsonify({"error": "That isn't a video or picture file."}), 400
        _remember_asset(kind, path)
        return jsonify({"path": path, "items": _assets(kind)})
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(_asset_dir(kind)):
        _remember_asset(kind, path)
        stored = path
    else:
        try:
            with open(path, "rb") as fh:
                stored = _store_asset(kind, fh.read(), path)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    return jsonify({"path": stored, "items": _assets(kind)})


@app.post("/api/assets/<kind>/remove")
def api_asset_remove(kind):
    _kind_or_404(kind)
    path = (request.json or {}).get("path", "")
    with _settings_lock:
        s = load_settings()
        s[ASSET_KINDS[kind]] = [p for p in s.get(ASSET_KINDS[kind], []) if p != path]
    save_settings(s)
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(_asset_dir(kind)) and os.path.isfile(path):
        os.remove(path)  # only our own copy, never your original
    return jsonify({"items": _assets(kind)})


@app.post("/api/settings")
def api_settings():
    s = load_settings()
    s.update(request.json or {})
    save_settings(s)
    ff.GPU["mode"] = s.get("gpu", "auto")
    return jsonify(s)


@app.get("/api/gpu")
def api_gpu():
    if request.args.get("recheck"):
        ff.GPU["broken"] = False
        ff.detect_gpu(force=True)
    return jsonify(ff.gpu_status())


@app.get("/api/project/<name>")
def api_project_get(name):
    p = os.path.join(PROJECTS, packages.safe(name) + ".json")
    if not os.path.exists(p):
        abort(404)
    with open(p, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.post("/api/project/<name>")
def api_project_save(name):
    p = os.path.join(PROJECTS, packages.safe(name) + ".json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(request.json, f, indent=2)
    return jsonify({"saved": p})


@app.get("/api/browse")
def api_browse():
    path = request.args.get("path") or os.path.expanduser("~")
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        path = os.path.dirname(path)
    try:
        entries = sorted(os.listdir(path), key=str.lower)
    except OSError as e:
        return jsonify({"error": str(e), "path": path}), 400
    dirs, files = [], []
    for e in entries:
        if e.startswith("."):
            continue
        fp = os.path.join(path, e)
        if os.path.isdir(fp):
            dirs.append(e)
        else:
            low = e.lower()
            kind = "audio" if low.endswith(AUDIO_EXT) else "image" if low.endswith(art.IMAGE_EXT) else \
                "video" if low.endswith(VIDEO_EXT) else "other"
            files.append({"name": e, "kind": kind})
    return jsonify({"path": path, "parent": os.path.dirname(path), "dirs": dirs, "files": files})


@app.get("/api/thumb")
def api_thumb():
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        abort(404)
    size = max(16, min(1600, int(request.args.get("size", 360))))
    if path.lower().endswith(VIDEO_EXT + (".gif",)):
        import hashlib
        st = os.stat(path)
        key = hashlib.sha1(f"{path}|{st.st_mtime}|{size}".encode()).hexdigest()[:16]
        os.makedirs(video.CACHE_DIR, exist_ok=True)
        png = os.path.join(video.CACHE_DIR, f"thumb_{key}.jpg")
        if not os.path.exists(png):
            try:
                at = "1" if ff.duration(path) > 2 else "0"
                ff.run(["-ss", at, "-i", path, "-frames:v", "1", "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                        "-update", "1", png])
            except Exception:
                abort(415)
        return send_file(png, mimetype="image/jpeg", max_age=60)
    try:
        with Image.open(path) as src:
            see_through = src.mode in ("RGBA", "LA", "PA") or (src.mode == "P" and "transparency" in src.info)
        if see_through:  # logos: keep the transparency so they show on a checkerboard
            im = Image.open(path).convert("RGBA")
            im.thumbnail((size, size))
        else:
            im = art.thumbnail(path, size)
    except Exception:
        abort(415)
    buf = io.BytesIO()
    if see_through:
        im.save(buf, "PNG")
    else:
        im.save(buf, "JPEG", quality=82)
    buf.seek(0)
    return send_file(buf, mimetype="image/png" if see_through else "image/jpeg", max_age=60)


@app.get("/api/file")
def api_file():
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        abort(404)
    return send_file(path, conditional=True)


@app.post("/api/audio_info")
def api_audio_info():
    try:
        return jsonify(ff.audio_info(request.json["path"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/scan_photos")
def api_scan_photos():
    folder = (request.json or {}).get("folder", "")
    if not os.path.isdir(folder):
        return jsonify({"error": "Folder not found"}), 400
    return jsonify({"photos": art.score_photos(folder)})


@app.post("/api/validate_cover")
def api_validate_cover():
    return jsonify(art.validate_cover(request.json["path"]))


@app.post("/api/meta")
def api_meta():
    b = request.json or {}
    r, kind = b.get("release", {}), b.get("kind", "official_audio")
    hours = b.get("hours", 10)
    rr = dict(r)
    if b.get("track_title"):
        rr["title"] = b["track_title"]
    return jsonify({
        "title": meta.youtube_title(rr, kind, hours=f"{float(hours):g}", album=r.get("title")),
        "description": meta.youtube_description(rr, load_settings().get("profile"), kind, b.get("chapters"), f"{float(hours):g}"),
        "tags": meta.tags(rr),
    })


@app.post("/api/timeline")
def api_timeline():
    r = request.json or {}
    if not r.get("release_date"):
        return jsonify({"error": "Set a release date first"}), 400
    items = meta.timeline(r["release_date"])
    return jsonify({"items": items, "ics": meta.ics(r, items)})


@app.post("/api/jobs")
def api_jobs_create():
    b = request.json or {}
    kinds = b.get("kinds") or [b.get("kind")]
    made, errors = [], []
    for k in kinds:
        try:
            params = b.get("params", {})
            label, fn = build_job(k, b.get("release", {}), params)
            job = new_job(k, label, fn)
            if params.get("track_index") not in (None, ""):
                job["track_index"] = params["track_index"]  # so "Schedule upload" can write the right title
            made.append(job["id"])
        except Exception as e:
            errors.append(f"{k}: {e}")
    return jsonify({"jobs": made, "errors": errors}), (200 if made or not errors else 400)


@app.get("/api/jobs")
def api_jobs():
    with LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: -j["created"])
    return jsonify([{k: v for k, v in j.items() if k not in ("cancel", "trace")} for j in jobs][:100])


@app.post("/api/jobs/clear")
def api_jobs_clear():
    """Forget finished jobs (the files they made stay where they are)."""
    with LOCK:
        for jid in [k for k, j in JOBS.items() if j["status"] not in ("queued", "running")]:
            del JOBS[jid]
    persist_jobs()
    return jsonify({"ok": True})


@app.post("/api/jobs/<jid>/cancel")
def api_job_cancel(jid):
    if jid in JOBS:
        JOBS[jid]["cancel"] = True
    return jsonify({"ok": True})


@app.post("/api/open")
def api_open():
    path = (request.json or {}).get("path", "")
    if not os.path.exists(path):
        return jsonify({"error": "not found"}), 404
    target = path if os.path.isdir(path) else os.path.dirname(path)
    sysname = platform.system()
    try:
        if sysname == "Windows":
            os.startfile(target)  # noqa
        elif sysname == "Darwin":
            subprocess.Popen(["open", target])
        else:
            subprocess.Popen(["xdg-open", target])
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"ok": True})


# ---------------------------------------------------------------- drag & drop, tags, previews

def _kind(name):
    low = name.lower()
    return "audio" if low.endswith(AUDIO_EXT) else "image" if low.endswith(art.IMAGE_EXT) else \
        "video" if low.endswith(VIDEO_EXT) else "other"


@app.post("/api/upload")
def api_upload():
    """Files dropped on the page are copied into ~/ReleaseKit/inbox/<date>/ (keeping sub-folders)."""
    batch = os.path.join(INBOX, time.strftime("%Y-%m-%d"))
    out = []
    for f in request.files.getlist("files"):
        rel = (f.filename or "file").replace("\\", "/")
        parts = [packages.safe(x) for x in rel.split("/") if x not in ("", ".", "..")]
        if not parts:
            continue
        dst = os.path.join(batch, *parts)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        base, ext = os.path.splitext(dst)
        n = 1
        while os.path.exists(dst):
            dst = f"{base} ({n}){ext}"
            n += 1
        f.save(dst)
        item = {"path": dst, "name": parts[-1], "kind": _kind(dst)}
        if item["kind"] == "audio":
            item["tags"] = ff.tags(dst)
        out.append(item)
    return jsonify({"files": out, "folder": batch})


@app.post("/api/tags")
def api_tags():
    return jsonify({p: ff.tags(p) for p in (request.json or {}).get("paths", []) if os.path.isfile(p)})


@app.post("/api/list_audio")
def api_list_audio():
    folder = (request.json or {}).get("folder", "")
    if not os.path.isdir(folder):
        return jsonify({"error": "Folder not found"}), 400
    files = []
    for root, _, names in os.walk(folder):
        for n in sorted(names):
            if n.lower().endswith(AUDIO_EXT):
                p = os.path.join(root, n)
                files.append({"path": p, "tags": ff.tags(p)})
    return jsonify({"files": files})


@app.post("/api/preview")
def api_preview():
    b = request.json or {}
    img = b.get("image")
    if not img or not os.path.isfile(img):
        return jsonify({"error": "Set a cover or choose an image first."}), 400
    if b.get("aspect") in video.ASPECTS:
        w, h = video.export_size(b["aspect"], 540)
    else:
        w, h = (540, 960) if b.get("vertical") else (960, 540)
    vertical = h > w
    tmp = os.path.join(DATA_DIR, "preview.png")
    style = b.get("vstyle") if isinstance(b.get("vstyle"), dict) else {}
    bump = style.get("bump") or "none"
    clip = b.get("video_clip") if b.get("visual") == "clip" else None
    from PIL import Image
    if clip and os.path.isfile(clip):
        ff.run(["-ss", "1", "-i", clip, "-frames:v", "1", "-vf",
                f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}", tmp])
    else:
        vis = style.get("visualizer") or "none"
        layout = "blur" if bump != "none" or vis == "scope" or vis in drawn.DRAWN else b.get("layout", "cover")
        video.make_frame(img, tmp, w, h, b.get("title", ""), b.get("artist", ""), layout,
                         b.get("text", True) and bump != "text",
                         vis_room=bool(b.get("aspect")) and vis in reactive.BOTTOM and layout == "cover")
    vis = style.get("visualizer") or "none"
    if vis in drawn.DRAWN and not clip:
        opts = dict(style, visualizer=vis, image=img, title=b.get("title", ""), artist=b.get("artist", ""))
        video.style_defaults(opts)
        layer = drawn.still(vis, w, h, reactive.pick_color(style.get("vis_color", "white"), img), opts, vertical)
        fr = Image.open(tmp).convert("RGBA")
        fr.alpha_composite(layer)
        fr.convert("RGB").save(tmp)
        bump = "none"
    if bump != "none":
        amount = float(style.get("bump_amount") or 0.12)
        cw, ch = reactive.element_canvas(bump, w, h, vertical, style.get("center_size"), style.get("logo"), amount)
        rgb = reactive.pick_color(style.get("vis_color", "white"), img)
        tile = reactive.bump_frames(bump, (cw, ch), amount, levels=2, image=img,
                                    logo=style.get("logo"), title=b.get("title", ""), artist=b.get("artist", ""),
                                    rgb=rgb)[1]
        fr = Image.open(tmp).convert("RGBA")
        t_im = Image.frombytes("RGBA", (cw, ch), tile)
        lift = 0.07 if (style.get("visualizer") or "none") in ("bars", "mirror", "waves", "line", "spectrum") else 0
        drawn.paste(fr, t_im, (w - cw) // 2, (h - ch) // 2 - int(h * (lift + (0.06 if vertical else 0))))
        fr.convert("RGB").save(tmp)
    return send_file(tmp, mimetype="image/png", max_age=0)


_preview_lock = threading.Lock()
_preview_gen = {"n": 0}
_loudest = {}


def _preview_audio(path):
    """(audio, start): the loudest part of the track, or the demo beat when there's no track."""
    if path and os.path.isfile(path):
        key = (path, os.path.getmtime(path))
        if key not in _loudest:
            _loudest[key] = audio.loudest_window(path, 6)
        return path, _loudest[key]
    demo = os.path.join(DATA_DIR, "demo_beat.wav")
    if not os.path.exists(demo):
        video.demo_beat(demo, 12)
    return demo, 2.0


@app.post("/api/look_preview")
def api_look_preview():
    """What the video really looks like with the current look: a still frame (seconds=0)
    or a short clip with sound, rendered small. Older requests still waiting are skipped."""
    b = request.json or {}
    img = b.get("image")
    if not img or not os.path.isfile(img):
        return jsonify({"error": "Set a cover (Artwork tab) or choose a picture first."}), 400
    style = b.get("vstyle") if isinstance(b.get("vstyle"), dict) else {}
    seconds = max(0.0, min(8.0, float(b.get("seconds") or 0)))
    aspect = b.get("aspect") if b.get("aspect") in video.ASPECTS else "16:9"
    w, h = video.export_size(aspect, 360 if seconds else 540)
    with LOCK:
        _preview_gen["n"] += 1
        gen = _preview_gen["n"]
    with _preview_lock:
        if gen != _preview_gen["n"]:
            return ("", 204)  # a newer preview was asked for meanwhile
        src, start = _preview_audio(b.get("track"))
        opts = {k: style.get(k) for k in video.STYLE_KEYS if style.get(k) not in (None, "")}
        if opts.get("bump") == "logo" and not (opts.get("logo") and os.path.isfile(opts["logo"])):
            opts["bump"] = "cover"
        d = os.path.join(DATA_DIR, "preview")
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
            except OSError:
                pass  # still being played by the browser
        clip = os.path.join(d, f"look_{gen}.mp4")
        length = seconds or 1.0
        video.release_video(src, img, clip, style.get("text_title") or b.get("title", ""),
                             style.get("text_artist") or b.get("artist", ""), visualizer=style.get("visualizer") or "none",
                             layout=b.get("layout", "cover"), text=b.get("text", True), fps=30, start=start,
                             length=length, fade=bool(seconds), size=(w, h), enc=video.encoder("mp4", "draft", 30),
                             opts=opts)
        if seconds:
            return send_file(clip, mimetype="video/mp4", max_age=0)
        png = os.path.join(d, f"look_{gen}.png")
        ff.run(["-sseof", "-0.1", "-i", clip, "-frames:v", "1", "-update", "1", png])
        return send_file(png, mimetype="image/png", max_age=0)


@app.post("/api/covers")
def api_covers():
    r = request.json or {}
    d = os.path.join(out_dir_for(r), "artwork")
    if not os.path.isdir(d):
        return jsonify({"covers": []})
    items = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith((".jpg", ".png")) and f.startswith("cover")]
    items.sort(key=os.path.getmtime, reverse=True)
    return jsonify({"covers": items})


@app.get("/api/export_options")
def api_export_options():
    return jsonify({"formats": {k: v["label"] for k, v in video.EXPORT_FORMATS.items()},
                    "aspects": list(video.ASPECTS), "sizes": list(video.SIZES), "fps": list(video.FPS_CHOICES),
                    "qualities": list(video.QUALITIES), "visualizers": reactive.VISUALIZERS})


@app.post("/api/estimate")
def api_estimate():
    b = request.json or {}
    need = estimate_long_bytes(b.get("hours", 10), b.get("resolution", "1080p"), b.get("visual", "static"))
    out = load_settings().get("output_dir") or DATA_DIR
    os.makedirs(out, exist_ok=True)
    return jsonify({"bytes": need, "free": shutil.disk_usage(out).free})


# ---------------------------------------------------------------- readiness

@app.post("/api/readiness")
def api_readiness():
    import datetime as dt
    r = request.json or {}
    checks = []

    def add(status, label, fix=None):
        checks.append({"status": status, "label": label, "fix": fix})

    tracks = r.get("tracks", [])
    add("ok" if r.get("title") and r.get("artist") else "bad", "Title and artist filled in", "release")
    if not tracks:
        add("bad", "At least one track", "release")
    missing = [t for t in tracks if not os.path.isfile(t.get("path", ""))]
    if missing:
        add("bad", f"{len(missing)} track file(s) can't be found (moved or deleted?)", "release")
    lossy = []
    for t in tracks:
        if os.path.isfile(t.get("path", "")):
            try:
                if ff.audio_info(t["path"])["codec"] in audio.LOSSY:
                    lossy.append(t.get("title"))
            except Exception:
                pass
    if tracks:
        add("warn" if lossy else "ok", "Lossless masters (WAV/FLAC)" + (f": {', '.join(lossy)} are lossy" if lossy else ""), "release")
    an = {a.get("title"): a for a in (r.get("analysis") or [])}
    if tracks and not an:
        add("warn", "Loudness not checked yet (press Analyze)", "release")
    elif an:
        bad = [k for k, a in an.items() if a.get("warnings")]
        add("warn" if bad else "ok", "Loudness & peaks" + (f": check {', '.join(bad)}" if bad else ""), "release")
    if r.get("art_path") and os.path.isfile(r["art_path"]):
        v = art.validate_cover(r["art_path"])
        add("bad" if v["issues"] else "ok", "Cover art 3000×3000 RGB" + (f": {v['issues'][0]}" if v["issues"] else ""), "art")
    else:
        add("bad", "Cover art", "art")
    no_writer = [t.get("title") for t in tracks if not t.get("composer")]
    add("warn" if no_writer else "ok", "Songwriter legal names (needed for publishing royalties)" +
        (f": missing on {len(no_writer)} track(s)" if no_writer else ""), "release")
    add("ok" if r.get("copyright_p") else "warn", "(P) line", "release")
    if r.get("release_date"):
        days = (dt.date.fromisoformat(r["release_date"]) - dt.date.today()).days
        if days < 0:
            add("ok", f"Release date passed ({-days} days ago)", "plan")
        elif days < 7:
            add("bad", f"Release is in {days} days: too late for Spotify editorial pitching (needs 7+ days)", "plan")
        elif days < 21:
            add("warn", f"Release is in {days} days: most distributors want 2–4 weeks", "plan")
        else:
            add("ok", f"Release date in {days} days", "plan")
    else:
        add("warn", "Release date", "release")
    s = load_settings()
    add("ok" if any((s.get("profile") or {}).values()) else "warn", "Your links for descriptions (Settings)", "set")
    done = {k for k, v in (s.get("signups") or {}).items() if v.get("done")}
    distros = [i["name"] for i in links.DIRECTORY[0]["items"]]
    add("ok" if done & set(distros) else "warn", "Signed up with a distributor", "links")
    return jsonify({"checks": checks, "score": sum(c["status"] == "ok" for c in checks), "total": len(checks)})


# ---------------------------------------------------------------- SL2

def _project_path(name):
    return os.path.join(PROJECTS, packages.safe(name) + ".json")


def import_sl2(song, project=None):
    s = load_settings()
    if project and os.path.exists(_project_path(project)):
        with open(_project_path(project), encoding="utf-8") as f:
            r = json.load(f)
        r.setdefault("tracks", []).append(sl2link.to_track(song))
        name = project
    else:
        r = sl2link.to_release(song, s.get("defaults"))
        name = f"{r.get('artist') or 'Artist'} - {r['title']}"
        if os.path.exists(_project_path(name)):
            name += f" ({song['id'][-4:]})"
    with open(_project_path(name), "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2)
    s["sl2_imported"] = sorted(set(s.get("sl2_imported", [])) | {song["id"]})
    save_settings(s)
    new_job("sl2_import", f"Imported from SL2: {song.get('title')}", None, status="done",
            result={"project": name})
    return name


_sl2_lock = threading.Lock()


def _watch_new(song):
    with _sl2_lock:
        if song["id"] not in load_settings().get("sl2_imported", []):
            import_sl2(song)


sl2link.Watcher(load_settings, _watch_new)


@app.post("/api/sl2/songs")
def api_sl2_songs():
    b = request.json or {}
    home = b.get("home") or load_settings().get("sl2_home") or sl2link.default_home()
    songs = sl2link.list_songs(home, not b.get("all_versions"))
    imported = set(load_settings().get("sl2_imported", []))
    for x in songs:
        x["imported"] = x["id"] in imported
    return jsonify({"home": home, "songs": songs})


@app.post("/api/sl2/import")
def api_sl2_import():
    b = request.json or {}
    home = b.get("home") or load_settings().get("sl2_home")
    by_id = {s["id"]: s for s in sl2link.list_songs(home, latest_only=False)}
    names = []
    with _sl2_lock:
        for sid in b.get("ids", []):
            if sid in by_id:
                names.append(import_sl2(by_id[sid], b.get("project")))
    return jsonify({"projects": names})


# ---------------------------------------------------------------- YouTube schedule

SCHED = schedule.Schedule(DATA_DIR, load_settings, update_settings)
SCHED.run_forever()
_channel_cache = {"t": 0, "v": None}


def _yt_status():
    st = {"available": youtube.available(), "has_client_secret": os.path.exists(os.path.join(DATA_DIR, "client_secret.json")),
          "signed_in": youtube.signed_in(DATA_DIR), "channel": None}
    if st["available"] and st["signed_in"]:
        if time.time() - _channel_cache["t"] > 600:
            try:
                _channel_cache["v"] = youtube.channel(DATA_DIR, interactive=False)
            except Exception as e:
                _channel_cache["v"] = {"title": None, "error": str(e)[:200]}
            _channel_cache["t"] = time.time()
        st["channel"] = _channel_cache["v"]
    return st


@app.get("/api/schedule")
def api_schedule():
    s = load_settings()
    return jsonify({"items": SCHED.items, "state": SCHED.state, "running": bool(s.get("schedule_running")),
                    "cap": int(s.get("schedule_daily_cap") or 6), "last24": SCHED.uploads_last_24h(),
                    "yt": _yt_status()})


def _sched_call(fn):
    try:
        res = fn(request.json or {})
        return jsonify(res if res is not None else {"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/schedule/add")
def api_schedule_add():
    def fn(b):
        tags = b.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        title, desc, thumb = b.get("title"), b.get("description", ""), b.get("thumbnail")
        base = os.path.splitext(b["video"])[0]
        if not title and os.path.exists(base + " - YouTube text.txt"):  # files made by the overnight batch
            title, desc, tags = schedule.parse_text_file(base + " - YouTube text.txt")
        if not thumb and os.path.exists(base + " - thumbnail.jpg"):
            thumb = base + " - thumbnail.jpg"
        return SCHED.add(b["video"], title, desc, tags, thumb,
                         b.get("playlist_id"), b.get("publish_at"), b.get("privacy", "public"))
    return _sched_call(fn)


@app.post("/api/schedule/import_folder")
def api_schedule_import():
    return _sched_call(lambda b: {"added": SCHED.import_folder(b["folder"], b.get("privacy", "public"))})


@app.post("/api/schedule/update")
def api_schedule_update():
    def fn(b):
        f = dict(b.get("fields") or {})
        if isinstance(f.get("tags"), str):
            f["tags"] = [t.strip() for t in f["tags"].split(",") if t.strip()]
        SCHED.update(b["id"], **f)
    return _sched_call(fn)


@app.post("/api/schedule/remove")
def api_schedule_remove():
    return _sched_call(lambda b: SCHED.remove(b["id"]))


@app.post("/api/schedule/move")
def api_schedule_move():
    return _sched_call(lambda b: SCHED.move(b["id"], int(b.get("delta", 0))))


@app.post("/api/schedule/retry")
def api_schedule_retry():
    return _sched_call(lambda b: SCHED.retry(b.get("id")))


@app.post("/api/schedule/times")
def api_schedule_times():
    return _sched_call(lambda b: SCHED.apply_times(b.get("times") or {}))


@app.post("/api/schedule/run")
def api_schedule_run():
    def fn(b):
        kw = {}
        if "running" in b:
            kw["schedule_running"] = bool(b["running"])
        if b.get("cap"):
            kw["schedule_daily_cap"] = max(1, min(100, int(b["cap"])))
        update_settings(**kw)
        if kw.get("schedule_running"):
            SCHED.retry()  # give paused/errored items another go
    return _sched_call(fn)


@app.post("/api/yt/connect")
def api_yt_connect():
    if not youtube.available():
        return jsonify({"error": "YouTube extras aren't installed (pip install -r requirements-youtube.txt)."}), 400
    if not os.path.exists(os.path.join(DATA_DIR, "client_secret.json")):
        return jsonify({"error": f"Put client_secret.json in {DATA_DIR} first (Sign-ups, section 7)."}), 400

    def fn(prog, cancel):
        ch = youtube.connect(DATA_DIR)
        _channel_cache.update(t=time.time(), v=ch)
        return {"channel": ch}
    j = new_job("yt_connect", "Connect YouTube (sign in in your browser)", fn)
    return jsonify({"job": j["id"]})


@app.post("/api/yt/signout")
def api_yt_signout():
    youtube.sign_out(DATA_DIR)
    _channel_cache.update(t=0, v=None)
    return jsonify({"ok": True})


@app.get("/api/yt/playlists")
def api_yt_playlists():
    try:
        return jsonify({"playlists": youtube.playlists(DATA_DIR)})
    except Exception as e:
        return jsonify({"error": str(e)[:300], "playlists": []}), 400


@app.post("/api/yt/playlist_create")
def api_yt_playlist_create():
    try:
        b = request.json or {}
        return jsonify(youtube.create_playlist(DATA_DIR, b["title"], b.get("privacy", "public")))
    except Exception as e:
        return jsonify({"error": str(e)[:300]}), 400


@app.post("/api/yt/audit_text")
def api_yt_audit_text():
    b = request.json or {}
    ch = (_yt_status().get("channel") or {})
    channel = b.get("channel") or (f"{ch.get('title')} - https://www.youtube.com/channel/{ch['id']}" if ch.get("id") else "my channel")
    return jsonify({"text": schedule.AUDIT_TEMPLATE.format(channel=channel, per_day=load_settings().get("schedule_daily_cap") or 6,
                                                           project=b.get("project") or "(from the Google Cloud console dashboard)")})


# ---------------------------------------------------------------- updates

@app.get("/api/update/status")
def api_update_status():
    s = load_settings()
    return jsonify(dict(update.STATE, current=__version__, skip=s.get("skip_version"),
                        enabled=s.get("check_updates", True), can_self_update=update.can_self_update()))


@app.post("/api/update/check")
def api_update_check():
    try:
        return jsonify(update.check())
    except Exception as e:
        return jsonify({"error": f"Couldn't reach GitHub: {e}"}), 400


@app.post("/api/update/apply")
def api_update_apply():
    busy = [j for j in JOBS.values() if j["status"] in ("running", "queued")]
    if busy and not (request.json or {}).get("force"):
        return jsonify({"error": f"{len(busy)} job(s) still running. Wait for them to finish (or stop them) first."}), 400
    try:
        return jsonify(update.apply(lambda: os._exit(0)))
    except Exception as e:
        update.STATE.update(applying=False, message="")
        return jsonify({"error": str(e)}), 400


@app.post("/api/update/skip")
def api_update_skip():
    update_settings(skip_version=(request.json or {}).get("version"))
    return jsonify({"ok": True})


@app.get("/api/version")
def api_version():
    return jsonify({"version": __version__})


def main():
    import webbrowser
    p = port()
    missing = ff.check_tools()
    if missing:
        print("WARNING: missing", ", ".join(missing), "- install ffmpeg (see README).")
    url = GUARD.url(p)
    update.check_in_background(load_settings().get("check_updates", True))
    ff.GPU["mode"] = load_settings().get("gpu", "auto")
    threading.Thread(target=ff.detect_gpu, daemon=True).start()  # find the graphics card while you get started
    print(f"Release Kit running. Open it with this private link (don't share it):\n  {url}\n(data folder: {DATA_DIR})")
    # after the update that adds the session key, the old tab can't get in: open the private link once
    if not os.environ.get("RELEASEKIT_NO_BROWSER") or (GUARD.new_key and HAD_SETTINGS):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    if not os.environ.get("RELEASEKIT_VERBOSE"):
        # keep the window readable: no line per request (errors still show). RELEASEKIT_VERBOSE=1 brings them back.
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
    print("Leave this window open while you work. Close it to quit.")
    # only ever listen on this computer, never on the network
    app.run(host="127.0.0.1", port=p, debug=False, threaded=True)


if __name__ == "__main__":
    main()

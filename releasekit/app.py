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

from . import art, audio, ff, links, meta, packages, sl2link, video, youtube

DATA_DIR = os.environ.get("RELEASEKIT_HOME", os.path.join(os.path.expanduser("~"), "ReleaseKit"))
PROJECTS = os.path.join(DATA_DIR, "projects")
INBOX = os.path.join(DATA_DIR, "inbox")
SETTINGS = os.path.join(DATA_DIR, "settings.json")
for d in (DATA_DIR, PROJECTS):
    os.makedirs(d, exist_ok=True)

AUDIO_EXT = (".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".ogg", ".opus")

app = Flask(__name__, static_folder=None)


# ---------------------------------------------------------------- settings / projects

def load_settings():
    if os.path.exists(SETTINGS):
        with open(SETTINGS, encoding="utf-8") as f:
            return json.load(f)
    return {"output_dir": os.path.join(DATA_DIR, "output"), "profile": {}, "signups": {}, "stock_author": ""}


def save_settings(s):
    with open(SETTINGS, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=2)


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
        job = JOBS[jid]
        if job["cancel"]:
            job["status"] = "cancelled"
            persist_jobs()
            continue
        job["status"] = "running"
        job["started"] = time.time()
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
        vis = p.get("visualizer", "none")
        name = packages.safe(f"{artist} - {t_title}{' (Visualizer)' if vis != 'none' else ''}") + ".mp4"
        return f"YouTube video: {t_title}", lambda prog, cancel: video.release_video(
            track["path"], img, os.path.join(out, "youtube", name), t_title, artist, p.get("resolution", "1080p"),
            vis, p.get("layout", "cover"), text, progress=prog, cancel=cancel)

    if kind == "album_video":
        _require(len(tracks) > 0, "Add tracks first.")
        img = _first_image(r, p)
        _require(img, "Set a cover first.")
        return f"Full album video: {title}", lambda prog, cancel: video.album_video(
            [t["path"] for t in tracks], img, os.path.join(out, "youtube", packages.safe(f"{artist} - {title} (Full Album)") + ".mp4"),
            [t.get("title") or os.path.basename(t["path"]) for t in tracks], title, artist,
            p.get("resolution", "1080p"), p.get("visualizer", "none"), float(p.get("gap", 1.0)), prog, cancel)

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
        if visual != "slideshow":
            _require(img, "Set a cover or choose an image.")
        check_space(out, estimate_long_bytes(hours, p.get("resolution", "1080p"), visual))
        name_title = chosen[0].get("title") if len(chosen) == 1 else title
        label_h = f"{hours:g}"
        name = packages.safe(f"{artist} - {name_title} ({label_h} Hours)") + ".mp4"
        return f"{label_h}h loop: {name_title}", lambda prog, cancel: video.long_video(
            [t["path"] for t in chosen], os.path.join(out, "youtube", name), hours, visual, img, images,
            name_title, artist, text, p.get("resolution", "1080p"), float(p.get("crossfade", 4)),
            p.get("ambient") or None, float(p.get("ambient_db", -18)), slide_seconds=float(p.get("slide_seconds", 8)),
            progress=prog, cancel=cancel)

    if kind == "batch_item":
        # one track of an overnight batch: (cover) + loop video + paste-ready text file
        hours = float(p.get("hours", 10))
        res = p.get("resolution", "1080p")
        check_space(out, estimate_long_bytes(hours, res, p.get("visual", "static")))
        t = track

        def fn(prog, cancel):
            img = p.get("image")
            if not img:
                cov = os.path.join(out, "artwork", "cover_batch.jpg")
                os.makedirs(os.path.dirname(cov), exist_ok=True)
                art.make_cover(None, "generate", cov, generate_style=p.get("style", "aurora"),
                               seed=zlib.crc32(t["path"].encode()))
                img = cov
            name = packages.safe(f"{artist} - {t['title']} ({hours:g} Hours)")
            vid = os.path.join(out, "youtube", name + ".mp4")
            res_v = video.long_video([t["path"]], vid, hours, p.get("visual", "static"), img, None, t["title"],
                                     artist, p.get("text", True), res, float(p.get("crossfade", 4)),
                                     p.get("ambient") or None, float(p.get("ambient_db", -18)),
                                     progress=prog, cancel=cancel)
            rr = dict(r, title=t["title"])
            kind_t = p.get("title_kind", "ten_hours")
            txt = os.path.join(out, "youtube", name + " - YouTube text.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write(meta.youtube_title(rr, kind_t, hours=f"{hours:g}") + "\n\n" +
                        meta.youtube_description(rr, load_settings().get("profile"), kind_t, None, f"{hours:g}") +
                        "\n\nTAGS: " + ", ".join(meta.tags(rr)))
            res_v["text_file"] = txt
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
            t_title, artist, secs, start, p.get("visualizer", "waves"), prog, cancel)

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
    path = os.path.normpath(os.path.join(base, name))
    if not path.startswith(base) or not os.path.isfile(path):
        abort(404)
    return send_file(path, max_age=86400)


@app.get("/api/init")
def api_init():
    return jsonify({
        "settings": load_settings(), "directory": links.DIRECTORY, "missing_tools": ff.check_tools(),
        "youtube_available": youtube.available(), "data_dir": DATA_DIR,
        "has_client_secret": os.path.exists(os.path.join(DATA_DIR, "client_secret.json")),
        "projects": sorted(f[:-5] for f in os.listdir(PROJECTS) if f.endswith(".json")),
        "home": os.path.expanduser("~"), "title_kinds": meta.TITLE_TEMPLATES,
        "sl2_default_home": sl2link.default_home(), "inbox": INBOX,
    })


@app.post("/api/settings")
def api_settings():
    s = load_settings()
    s.update(request.json or {})
    save_settings(s)
    return jsonify(s)


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
                "video" if low.endswith((".mp4", ".mov", ".mkv")) else "other"
            files.append({"name": e, "kind": kind})
    return jsonify({"path": path, "parent": os.path.dirname(path), "dirs": dirs, "files": files})


@app.get("/api/thumb")
def api_thumb():
    path = request.args.get("path", "")
    if not os.path.isfile(path):
        abort(404)
    size = int(request.args.get("size", 360))
    try:
        im = art.thumbnail(path, size)
    except Exception:
        abort(415)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=82)
    buf.seek(0)
    return send_file(buf, mimetype="image/jpeg", max_age=60)


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
            label, fn = build_job(k, b.get("release", {}), b.get("params", {}))
            made.append(new_job(k, label, fn)["id"])
        except Exception as e:
            errors.append(f"{k}: {e}")
    return jsonify({"jobs": made, "errors": errors}), (200 if made or not errors else 400)


@app.get("/api/jobs")
def api_jobs():
    with LOCK:
        jobs = sorted(JOBS.values(), key=lambda j: -j["created"])
    return jsonify([{k: v for k, v in j.items() if k != "cancel"} for j in jobs][:100])


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
        "video" if low.endswith((".mp4", ".mov", ".mkv")) else "other"


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
    vertical = bool(b.get("vertical"))
    w, h = (540, 960) if vertical else (960, 540)
    tmp = os.path.join(DATA_DIR, "preview.png")
    video.make_frame(img, tmp, w, h, b.get("title", ""), b.get("artist", ""), b.get("layout", "cover"),
                     b.get("text", True))
    return send_file(tmp, mimetype="image/png", max_age=0)


@app.post("/api/covers")
def api_covers():
    r = request.json or {}
    d = os.path.join(out_dir_for(r), "artwork")
    if not os.path.isdir(d):
        return jsonify({"covers": []})
    items = [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith((".jpg", ".png")) and f.startswith("cover")]
    items.sort(key=os.path.getmtime, reverse=True)
    return jsonify({"covers": items})


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


def main():
    import webbrowser
    port = int(os.environ.get("RELEASEKIT_PORT", 5577))
    missing = ff.check_tools()
    if missing:
        print("WARNING: missing", ", ".join(missing), "- install ffmpeg (see README).")
    url = f"http://127.0.0.1:{port}"
    print(f"Release Kit running at {url}  (data folder: {DATA_DIR})")
    if not os.environ.get("RELEASEKIT_NO_BROWSER"):
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()

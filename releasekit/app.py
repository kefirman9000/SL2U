"""Release Kit - local web app. Run:  python -m releasekit  (then open http://127.0.0.1:5577)"""
import io
import json
import os
import platform
import queue
import subprocess
import threading
import time
import traceback
import uuid

from flask import Flask, abort, jsonify, request, send_file

from . import art, audio, ff, links, meta, packages, video, youtube

DATA_DIR = os.environ.get("RELEASEKIT_HOME", os.path.join(os.path.expanduser("~"), "ReleaseKit"))
PROJECTS = os.path.join(DATA_DIR, "projects")
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


def new_job(kind, label, fn):
    jid = uuid.uuid4().hex[:8]
    job = {"id": jid, "kind": kind, "label": label, "status": "queued", "progress": 0.0,
           "created": time.time(), "result": None, "error": None, "cancel": False}
    with LOCK:
        JOBS[jid] = job
    Q.put((jid, fn))
    return job


def worker():
    while True:
        jid, fn = Q.get()
        job = JOBS[jid]
        if job["cancel"]:
            job["status"] = "cancelled"
            continue
        job["status"] = "running"
        job["started"] = time.time()

        def prog(f, job=job):
            job["progress"] = round(float(f), 4)

        try:
            job["result"] = fn(prog, lambda job=job: job["cancel"])
            job["status"] = "done"
            job["progress"] = 1.0
        except Exception as e:
            job["status"] = "cancelled" if job["cancel"] else "error"
            job["error"] = str(e)
            job["trace"] = traceback.format_exc()[-3000:]
        job["finished"] = time.time()


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
        name_title = chosen[0].get("title") if len(chosen) == 1 else title
        label_h = f"{hours:g}"
        name = packages.safe(f"{artist} - {name_title} ({label_h} Hours)") + ".mp4"
        return f"{label_h}h loop: {name_title}", lambda prog, cancel: video.long_video(
            [t["path"] for t in chosen], os.path.join(out, "youtube", name), hours, visual, img, images,
            name_title, artist, text, p.get("resolution", "1080p"), float(p.get("crossfade", 4)),
            p.get("ambient") or None, float(p.get("ambient_db", -18)), slide_seconds=float(p.get("slide_seconds", 8)),
            progress=prog, cancel=cancel)

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


@app.get("/api/init")
def api_init():
    return jsonify({
        "settings": load_settings(), "directory": links.DIRECTORY, "missing_tools": ff.check_tools(),
        "youtube_available": youtube.available(), "data_dir": DATA_DIR,
        "has_client_secret": os.path.exists(os.path.join(DATA_DIR, "client_secret.json")),
        "projects": sorted(f[:-5] for f in os.listdir(PROJECTS) if f.endswith(".json")),
        "home": os.path.expanduser("~"), "title_kinds": meta.TITLE_TEMPLATES,
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

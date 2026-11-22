"""YouTube upload schedule.

Each queued video is uploaded ahead of time as Private with a publish date ("publishAt"), so
YouTube itself makes it public on schedule - your computer doesn't need to be on at publish time.
The background uploader works through the queue while Release Kit is open, keeps under a daily
upload cap, and when YouTube says the daily limit is reached it waits and tries again later.
"""
import datetime as dt
import glob
import json
import os
import re
import threading
import time
import uuid

from . import youtube

LOCK = threading.Lock()


def _now():
    return time.time()


def _iso_to_ts(s):
    if not s:
        return None
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _ts_to_iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class Schedule:
    def __init__(self, data_dir, get_settings, update_settings=None):
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, "schedule.json")
        self.get_settings = get_settings
        self.update_settings = update_settings or (lambda **kw: None)
        self.items = []
        self.state = {"running": False, "current": None, "last_error": None, "next_check": None}
        self._load()
        self.uploader = youtube.upload  # swapped out in tests

    # ------------------------------------------------------------ storage
    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.items = json.load(f)
            except Exception:
                self.items = []
        for it in self.items:  # an upload cut off by closing the app starts again
            if it["status"] == "uploading":
                it["status"], it["progress"] = "waiting", 0

    def save(self):
        with LOCK:
            data = json.dumps(self.items, indent=1)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------ editing
    def add(self, video, title, description="", tags=None, thumbnail=None, playlist_id=None, publish_at=None,
            privacy="public"):
        if not os.path.isfile(video):
            raise ValueError(f"Video not found: {video}")
        it = {"id": uuid.uuid4().hex[:8], "video": video, "title": (title or os.path.basename(video))[:100],
              "description": (description or "")[:5000], "tags": tags or [], "thumbnail": thumbnail or "",
              "playlist_id": playlist_id or "", "publish_at": publish_at, "privacy": privacy,
              "status": "waiting", "message": "", "progress": 0, "video_id": None, "url": None,
              "added": _now(), "uploaded_at": None, "retry_after": 0}
        with LOCK:
            self.items.append(it)
        self.save()
        return it

    def update(self, iid, **fields):
        allowed = {"title", "description", "tags", "thumbnail", "playlist_id", "publish_at", "privacy"}
        with LOCK:
            for it in self.items:
                if it["id"] == iid:
                    if it["status"] in ("done", "uploading"):
                        raise ValueError("Already uploaded; change it in YouTube Studio instead.")
                    it.update({k: v for k, v in fields.items() if k in allowed})
                    if it["status"] in ("error", "needs_signin"):
                        it["status"], it["message"] = "waiting", ""
                    break
        self.save()

    def remove(self, iid):
        with LOCK:
            self.items = [i for i in self.items if not (i["id"] == iid and i["status"] != "uploading")]
        self.save()

    def move(self, iid, delta):
        with LOCK:
            idx = next((n for n, i in enumerate(self.items) if i["id"] == iid), None)
            if idx is None:
                return
            j = max(0, min(len(self.items) - 1, idx + delta))
            self.items.insert(j, self.items.pop(idx))
        self.save()

    def retry(self, iid=None):
        self.state["quota_until"] = 0
        with LOCK:
            for it in self.items:
                if (iid is None or it["id"] == iid) and it["status"] in ("error", "needs_signin", "paused_quota"):
                    it["status"], it["message"], it["retry_after"] = "waiting", "", 0
        self.save()

    def apply_times(self, times):
        """times: {id: ISO-UTC publish time or None}"""
        with LOCK:
            for it in self.items:
                if it["id"] in times and it["status"] not in ("done", "uploading"):
                    it["publish_at"] = times[it["id"]]
        self.save()

    # ------------------------------------------------------------ batch folder import
    def import_folder(self, folder, privacy="public"):
        """Adds every .mp4 in a folder; picks up '<name> - YouTube text.txt' and '<name> - thumbnail.jpg'."""
        added, known = [], {i["video"] for i in self.items}
        for vid in sorted(glob.glob(os.path.join(folder, "**", "*.mp4"), recursive=True)):
            if vid in known:
                continue
            base = vid[:-4]
            title, desc, tags = os.path.splitext(os.path.basename(vid))[0], "", []
            txt = base + " - YouTube text.txt"
            if os.path.exists(txt):
                title, desc, tags = parse_text_file(txt)
            thumb = next((p for p in (base + " - thumbnail.jpg", base + ".jpg") if os.path.exists(p)), "")
            added.append(self.add(vid, title, desc, tags, thumb, privacy=privacy))
        return added

    # ------------------------------------------------------------ uploader loop
    def uploads_last_24h(self):
        cut = _now() - 86400
        return sum(1 for i in self.items if i.get("uploaded_at") and i["uploaded_at"] > cut)

    def next_item(self):
        now = _now()
        for it in self.items:
            if it["status"] in ("waiting", "paused_quota") and it.get("retry_after", 0) <= now:
                return it
        return None

    def step(self):
        """Upload at most one video. Returns True if it did something."""
        s = self.get_settings()
        if not s.get("schedule_running"):
            return False
        if not youtube.available() or not os.path.exists(os.path.join(self.data_dir, "client_secret.json")):
            self.state["last_error"] = "YouTube upload isn't set up yet (see Sign-ups, section 7)."
            return False
        if self.state.get("quota_until", 0) > _now():
            return False  # the daily limit is account-wide: wait before trying any video
        cap = int(s.get("schedule_daily_cap") or 6)
        if self.uploads_last_24h() >= cap:
            self.state["last_error"] = f"Daily cap of {cap} uploads reached; continuing later."
            return False
        it = self.next_item()
        if not it:
            return False
        publish_at = it.get("publish_at")
        note = ""
        if publish_at and _iso_to_ts(publish_at) < _now() + 20 * 60:
            # YouTube rejects publish times in the past; publish straight away instead
            publish_at, note = None, "Publish time had passed, so it went live on upload."
        it["status"], it["progress"], it["message"] = "uploading", 0, ""
        self.state["current"] = it["id"]
        self.save()

        def prog(f):
            it["progress"] = round(f, 3)

        try:
            res = self.uploader(self.data_dir, it["video"], it["title"], it["description"], it["tags"],
                                privacy=it.get("privacy") or "public", publish_at=publish_at,
                                thumbnail=it.get("thumbnail") or None, playlist_id=it.get("playlist_id") or None,
                                progress=prog, interactive=False)
            it.update(status="done", progress=1, video_id=res["video_id"], url=res["url"], uploaded_at=_now(),
                      message=" ".join([note] + res.get("notes", [])).strip())
            self.state["last_error"] = None
        except Exception as e:
            kind = youtube.error_kind(e)
            if kind == "quota":
                it.update(status="paused_quota", progress=0, retry_after=_now() + 3600,
                          message="YouTube's daily upload limit was reached. Trying again every hour.")
                self.state["quota_until"] = _now() + 3600
            elif kind == "auth":
                it.update(status="needs_signin", progress=0, message=str(e))
                self.update_settings(schedule_running=False)  # pause instead of failing every item
            else:
                it.update(status="error", progress=0, message=str(e)[:500])
            self.state["last_error"] = it["message"]
        finally:
            self.state["current"] = None
            self.save()
        return True

    def run_forever(self):
        def loop():
            while True:
                try:
                    did = self.step()
                except Exception as e:  # keep the thread alive no matter what
                    self.state["last_error"] = str(e)
                    did = False
                self.state["next_check"] = _now() + (2 if did else 30)
                time.sleep(2 if did else 30)
        threading.Thread(target=loop, daemon=True).start()


def parse_text_file(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    lines = raw.splitlines()
    title = lines[0].strip() if lines else ""
    tags = []
    m = re.search(r"^TAGS:\s*(.*)$", raw, re.M)
    if m:
        tags = [t.strip() for t in m.group(1).split(",") if t.strip()]
        raw = raw[:m.start()]
    desc = "\n".join(raw.splitlines()[1:]).strip()
    return title, desc, tags


AUDIT_TEMPLATE = """YouTube API audit / quota request - answers to adapt

App name: Release Kit (personal desktop tool, https://github.com/kefirman9000/SL2U)
Who uses it: only me, to upload my own music videos to my own channel ({channel}).
What it does with the API:
- videos.insert: uploads videos I render on my own computer (music videos, visualizers,
  extended loop versions of my own tracks).
- thumbnails.set: sets the thumbnail I made for each upload.
- playlistItems.insert / playlists.list: adds uploads to my own playlists.
- channels.list: shows which channel is connected.
No other users' data is accessed, stored or shared. Videos and titles come from me.
Uploads are scheduled with publishAt so they go public at set times.
Expected volume: about {per_day} uploads per day at most.
Why I need the audit: API projects that haven't been audited can only upload Private videos,
so scheduled uploads never go public without the audit.
OAuth: Desktop-app client, the token stays on my own computer.
Google Cloud project number: {project}
"""

"""Read songs made by SL2 (Studio Lab 2) and turn them into Release Kit releases.

SL2 stores each version of a song as   <SL2 songs folder>/<id>/meta.json + mix.wav
Every edit in SL2 creates a new version whose meta.json names its 'parent', so only the newest
version of each song (one that no other version points to) is offered by default.
"""
import json
import os
import threading
import time

# genre id -> (release genre, mood) for metadata, stock keywords and YouTube titles
GENRE_HINTS = {
    "lofi": ("Lofi Hip Hop", "Chill"), "ambient": ("Ambient", "Calm"), "house": ("House", "Upbeat"),
    "deep_house": ("Deep House", "Smooth"), "tech_house": ("Tech House", "Groovy"), "techno": ("Techno", "Driving"),
    "melodic_techno": ("Melodic Techno", "Hypnotic"), "acid_house": ("Acid House", "Energetic"),
    "trance": ("Trance", "Uplifting"), "hard_electro": ("Electro", "Aggressive"), "garage": ("UK Garage", "Bouncy"),
    "liquid_dnb": ("Drum and Bass", "Rolling"), "dnb": ("Drum and Bass", "Dark"), "deep_dubstep": ("Dubstep", "Dark"),
    "trap": ("Trap", "Hard"), "phonk": ("Phonk", "Dark"), "sunlit": ("Electronic", "Sunny"),
    "synthwave": ("Synthwave", "Nostalgic"), "spooky_surf": ("Surf Rock", "Spooky"),
}


def default_home():
    for c in (os.environ.get("SL2_HOME"), os.path.expanduser("~/SL2/songs"), os.path.expanduser("~/sl2/songs"),
              os.path.expanduser("~/Documents/SL2/songs")):
        if c and os.path.isdir(c):
            return c
    return ""


def list_songs(home, latest_only=True):
    if not home or not os.path.isdir(home):
        return []
    songs = []
    for d in os.listdir(home):
        if d.startswith("."):
            continue
        mp, wav = os.path.join(home, d, "meta.json"), os.path.join(home, d, "mix.wav")
        if not (os.path.isfile(mp) and os.path.isfile(wav)):
            continue
        try:
            with open(mp, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        m["id"] = m.get("id") or d
        m["wav"] = wav
        songs.append(m)
    if latest_only:
        parents = {s.get("parent") for s in songs if s.get("parent")}
        songs = [s for s in songs if s["id"] not in parents]
    songs.sort(key=lambda s: -(s.get("created") or 0))
    return [{k: s.get(k) for k in ("id", "title", "genre", "genre_name", "key", "bpm", "duration", "starred",
                                    "created", "note", "parent", "wav")} for s in songs]


def to_track(s):
    return {"path": s["wav"], "title": s.get("title") or "Untitled", "bpm": s.get("bpm") or "",
            "key": s.get("key") or "", "sl2_id": s["id"]}


def to_release(s, defaults=None):
    genre, mood = GENRE_HINTS.get(s.get("genre"), (s.get("genre_name") or "Electronic", ""))
    r = dict(defaults or {})
    r.update({"title": s.get("title") or "Untitled", "genre": genre, "mood": mood, "language": "Instrumental",
              "vocals": False, "tracks": [to_track(s)], "source": "SL2", "sl2_id": s["id"]})
    r["tags"] = ", ".join(x for x in (s.get("genre_name"), f"{s.get('bpm')} bpm" if s.get("bpm") else "",
                                      s.get("key")) if x)
    return r


class Watcher:
    """Polls the SL2 songs folder and auto-imports songs (starred only, by default)."""

    def __init__(self, get_settings, on_new):
        self.get_settings, self.on_new = get_settings, on_new
        self.seen = None
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while True:
            try:
                s = self.get_settings()
                if s.get("sl2_watch") and s.get("sl2_home"):
                    songs = list_songs(s["sl2_home"])
                    if s.get("sl2_starred_only", True):
                        songs = [x for x in songs if x.get("starred")]
                    imported = set(s.get("sl2_imported", []))
                    for song in songs:
                        if song["id"] not in imported:
                            self.on_new(song)
            except Exception as e:  # never let the watcher die
                print("SL2 watcher:", e)
            time.sleep(20)

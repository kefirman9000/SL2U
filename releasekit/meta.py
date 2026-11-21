"""Titles, descriptions, tags, stock keywords, release timeline and calendar export."""
import datetime as dt
import re
import uuid

YT_TITLE_MAX = 100
YT_DESC_MAX = 5000
YT_TAGS_MAX_CHARS = 500

TITLE_TEMPLATES = {
    "official_audio": "{artist} - {title} (Official Audio)",
    "visualizer": "{artist} - {title} (Visualizer)",
    "lyric": "{artist} - {title} (Lyric Video)",
    "full_album": "{artist} - {album} (Full Album)",
    "ten_hours": "{title} - {hours} Hours | {mood} {genre} Loop",
    "ten_hours_plain": "{artist} - {title} [{hours} Hour Version]",
    "study": "{hours} Hours of {mood} {genre} for Study, Sleep & Focus | {artist}",
    "short": "{title} #shorts",
}

USE_CASES = {
    "lofi": ["study", "focus", "chill", "relax", "background", "cafe"],
    "ambient": ["sleep", "meditation", "relax", "spa", "yoga", "focus", "calm"],
    "cinematic": ["trailer", "film", "epic", "documentary", "dramatic", "score"],
    "corporate": ["business", "presentation", "advertising", "commercial", "uplifting", "technology"],
    "electronic": ["edm", "dance", "club", "energy", "workout", "gaming"],
    "hip hop": ["beat", "rap", "urban", "boom bap", "trap", "vlog"],
    "rock": ["guitar", "energetic", "sports", "action", "driving", "band"],
    "acoustic": ["folk", "guitar", "warm", "vlog", "travel", "wedding"],
    "piano": ["emotional", "sad", "romantic", "classical", "reflective", "solo piano"],
    "jazz": ["lounge", "restaurant", "smooth", "saxophone", "swing", "dinner"],
}


def _fmt(tpl, r, **extra):
    d = {"artist": r.get("artist", ""), "title": r.get("title", ""), "album": r.get("title", ""),
         "genre": r.get("genre", "") or "Music", "mood": r.get("mood", "") or "Relaxing", "hours": "10"}
    d.update({k: v for k, v in extra.items() if v is not None})
    s = tpl.format(**d)
    return re.sub(r"\s+", " ", s).strip(" -|")


def youtube_title(r, kind="official_audio", **extra):
    t = _fmt(TITLE_TEMPLATES.get(kind, kind), r, **extra)
    return t[:YT_TITLE_MAX]


def hashtags(r, n=3):
    raw = [r.get("artist", ""), r.get("genre", ""), r.get("mood", "")]
    tags = []
    for x in raw:
        x = re.sub(r"[^\w]", "", x)
        if x and x.lower() not in [t.lower() for t in tags]:
            tags.append(x)
    return " ".join("#" + t for t in tags[:n])


def tags(r, extra=()):
    base = [r.get("artist"), r.get("title"), f"{r.get('artist')} {r.get('title')}", r.get("genre"),
            r.get("subgenre"), r.get("mood"), "new music", f"{r.get('genre')} music"]
    base += [t.strip() for t in (r.get("tags") or "").split(",")]
    base += list(extra)
    g = (r.get("genre") or "").lower()
    for k, v in USE_CASES.items():
        if k in g:
            base += v
    out, total = [], 0
    for t in base:
        if not t:
            continue
        t = t.strip()
        if not t or t.lower() in (x.lower() for x in out):
            continue
        cost = len(t) + (2 if " " in t else 0) + 1
        if total + cost > YT_TAGS_MAX_CHARS:
            break
        out.append(t)
        total += cost
    return out


def links_block(profile):
    lines = []
    for label, key in (("Stream / save", "smart_link"), ("Spotify", "spotify"), ("Apple Music", "apple"),
                       ("Bandcamp", "bandcamp"), ("SoundCloud", "soundcloud"), ("Instagram", "instagram"),
                       ("TikTok", "tiktok"), ("Website", "website")):
        v = (profile or {}).get(key)
        if v:
            lines.append(f"{label}: {v}")
    return "\n".join(lines)


def youtube_description(r, profile=None, kind="official_audio", chapters=None, hours=None):
    p = []
    if kind in ("ten_hours", "ten_hours_plain", "study"):
        p.append(f"{hours or 10} hours of \"{r.get('title')}\" by {r.get('artist')}, looped seamlessly"
                 f"{' for studying, sleep, work and focus' if kind == 'study' else ''}.")
    elif kind == "full_album":
        p.append(f"The full album \"{r.get('title')}\" by {r.get('artist')}.")
    else:
        p.append(f"\"{r.get('title')}\" by {r.get('artist')}" + (f", out {r['release_date']}" if r.get("release_date") else "") + ".")
    if r.get("description"):
        p.append(r["description"].strip())
    lb = links_block(profile)
    if lb:
        p.append(lb)
    if chapters:
        p.append("Tracklist:\n" + chapters)
    credits = []
    for t in r.get("tracks", [])[:1] if kind != "full_album" else r.get("tracks", []):
        bits = [f"Written by {t['composer']}" if t.get("composer") else "",
                f"Produced by {t['producer']}" if t.get("producer") else ""]
        bits = [b for b in bits if b]
        if bits:
            credits.append(", ".join(bits))
    if credits:
        p.append("Credits: " + " | ".join(credits))
    if r.get("copyright_p"):
        p.append(f"\u2117 {r['copyright_p']}")
    if r.get("lyrics") and kind in ("official_audio", "lyric", "visualizer"):
        p.append("Lyrics:\n" + r["lyrics"].strip())
    p.append(hashtags(r))
    text = "\n\n".join(x for x in p if x)
    return text[:YT_DESC_MAX]


def stock_keywords(r, track=None, max_n=50):
    words = []
    for field in (r.get("genre"), r.get("subgenre"), r.get("mood"), r.get("instruments"), r.get("tags")):
        if field:
            words += [w.strip().lower() for w in re.split(r"[,/;]", field) if w.strip()]
    g = (r.get("genre") or "").lower()
    for k, v in USE_CASES.items():
        if k in g or k in (r.get("mood") or "").lower():
            words += v
    words += ["background music", "royalty free", "instrumental" if not r.get("vocals") else "vocals",
              "music for video", "youtube", "podcast", "content creator"]
    if track and track.get("bpm"):
        b = int(track["bpm"])
        words.append("slow" if b < 90 else "mid tempo" if b < 120 else "fast")
    out = []
    for w in words:
        if w and w not in out:
            out.append(w)
    return out[:max_n]


def stock_description(r, track):
    mood = r.get("mood") or "versatile"
    genre = r.get("genre") or "music"
    inst = r.get("instruments")
    s = f"{mood.capitalize()} {genre.lower()} track"
    if inst:
        s += f" featuring {inst}"
    if track.get("bpm"):
        s += f", {track['bpm']} BPM"
    s += ". Ideal for " + ", ".join(stock_keywords(r)[:5]) + "."
    return s


# ---------------------------------------------------------------- timeline + calendar

TIMELINE = [
    (-42, "Finish master, artwork and metadata; register songs with your PRO and The MLC (they don't need the release to be live)."),
    (-35, "Run Release Kit: analyze loudness, make cover + variants, build distributor package."),
    (-28, "Upload to your distributor (4+ weeks lets stores and editorial teams see it in time)."),
    (-21, "Pitch an unreleased song to Spotify editorial in Spotify for Artists (must be at least 7 days before release). Set up the pre-save / smart link."),
    (-14, "Post teaser Shorts/Reels/TikToks; schedule the YouTube premiere or upload as a scheduled video."),
    (-7, "Upload Spotify Canvas; final check of store listings; prep stock-library and Bandcamp uploads."),
    (0, "Release day: publish Bandcamp + SoundCloud, share links, pin posts, reply to comments."),
    (1, "Upload the 10-hour / extended version and visualizer to YouTube to catch search traffic."),
    (7, "Check stats in Spotify / Apple / Amazon for Artists; pitch playlists and blogs with the live link."),
    (14, "Submit to stock libraries (Pond5 etc.) if the track suits licensing; post a second wave of clips."),
    (30, "Review: which platform and clip worked best? Plan the next release (a steady cadence helps)."),
]


def timeline(release_date):
    d0 = dt.date.fromisoformat(release_date)
    return [{"date": (d0 + dt.timedelta(days=off)).isoformat(), "offset": off, "task": task}
            for off, task in TIMELINE]


def ics(release, items):
    def esc(s):
        return s.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ReleaseKit//EN"]
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    for it in items:
        d = it["date"].replace("-", "")
        nd = (dt.date.fromisoformat(it["date"]) + dt.timedelta(days=1)).isoformat().replace("-", "")
        lines += ["BEGIN:VEVENT", f"UID:{uuid.uuid4()}@releasekit", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{d}", f"DTEND;VALUE=DATE:{nd}",
                  f"SUMMARY:{esc(release.get('title', 'Release'))}: {esc(it['task'][:60])}",
                  f"DESCRIPTION:{esc(it['task'])}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"

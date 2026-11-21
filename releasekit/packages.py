"""Export folders ready to upload to each kind of platform."""
import csv
import json
import os
import re
import shutil

from PIL import Image

from . import art, audio, meta


def safe(s):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", s or "").strip()
    return s or "untitled"


def _track_name(i, t):
    return f"{i:02d} - {safe(t.get('title') or os.path.splitext(os.path.basename(t['path']))[0])}"


def _write_csv(path, rows):
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def _cover(r, out_dir, name="cover_3000.jpg", size=3000):
    if not r.get("art_path"):
        return None
    im = art.open_rgb(r["art_path"])
    im = art.center_square(im, size)
    p = os.path.join(out_dir, name)
    im.save(p, "JPEG", quality=95, subsampling=0)
    return p


def _stage(progress, i, n):
    return (lambda f: progress((i + f) / n)) if progress else None


# ---------------------------------------------------------------- distributor

DISTRO_CHECKLIST = """# Distributor upload checklist - {title}

Upload this folder's files to ONE distributor (DistroKid, TuneCore, CD Baby, Ditto, Amuse, RouteNote...).
It delivers to Spotify, Apple Music, Amazon Music, YouTube Music, TikTok/Instagram, Deezer, Tidal and more.

## Files
- Audio: WAV {bits}-bit / {sr} Hz in /audio
- Cover: cover_3000.jpg (3000x3000, RGB JPG)
- Metadata: metadata.csv / metadata.json (copy fields into the distributor form)
- Lyrics: /lyrics (paste into the distributor's lyrics field; many also send them to Apple/Musixmatch)

## Before you click submit
- [ ] Artist name spelled exactly as on your existing store profiles (avoids landing on someone else's page)
- [ ] Featured artists in the 'featuring' field, not in the track title
- [ ] Title has no extra words like "Official", "Free download", "New"
- [ ] Explicit flag set correctly for every track
- [ ] Composer/lyricist legal names entered (needed for publishing royalties)
- [ ] Release date at least 3-4 weeks out so you can pitch Spotify editorial (at least 7 days before release)
- [ ] Opt in or out of YouTube Content ID on purpose (opt OUT if you license this track on stock sites or use it in free-to-use loops)
- [ ] Keep the ISRCs your distributor assigns: paste them back into Release Kit (Release tab) so every other package and registration uses the same codes

## Analysis
{analysis}
"""


def distributor(r, out_root, bits=16, sr=44100, progress=None, cancel=None):
    out = os.path.join(out_root, safe(f"{r.get('artist')} - {r.get('title')} (Distribution)"))
    os.makedirs(os.path.join(out, "audio"), exist_ok=True)
    rows, analysis = [], []
    tracks = r.get("tracks", [])
    for i, t in enumerate(tracks, 1):
        dst = os.path.join(out, "audio", _track_name(i, t) + ".wav")
        audio.to_wav(t["path"], dst, sr=sr, bits=bits, progress=_stage(progress, i - 1, len(tracks) + 1), cancel=cancel)
        a = audio.analyze(t["path"])
        analysis.append(f"- {t.get('title')}: {a.get('lufs', 0):.1f} LUFS, true peak {a.get('true_peak', 0):.1f} dBTP"
                        + ("".join(f"\n  - WARNING: {w}" for w in a["warnings"])))
        rows.append({
            "track_number": i, "title": t.get("title", ""), "version": t.get("version", ""),
            "primary_artist": r.get("artist", ""), "featuring": t.get("featuring", r.get("featuring", "")),
            "isrc": t.get("isrc", ""), "explicit": "yes" if t.get("explicit") or r.get("explicit") else "no",
            "composer": t.get("composer", ""), "lyricist": t.get("lyricist", ""), "producer": t.get("producer", ""),
            "language": r.get("language", ""), "duration_sec": round(a["duration"], 2),
            "preview_start_sec": t.get("preview_start", ""), "file": os.path.basename(dst),
        })
        if t.get("lyrics"):
            os.makedirs(os.path.join(out, "lyrics"), exist_ok=True)
            with open(os.path.join(out, "lyrics", _track_name(i, t) + ".txt"), "w", encoding="utf-8") as f:
                f.write(t["lyrics"])
    _write_csv(os.path.join(out, "metadata.csv"), rows)
    release = {k: r.get(k, "") for k in ("title", "artist", "featuring", "label", "genre", "subgenre", "release_date",
                                         "upc", "language", "copyright_p", "copyright_c", "explicit")}
    release["tracks"] = rows
    with open(os.path.join(out, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(release, f, indent=2)
    cover = _cover(r, out)
    val = art.validate_cover(cover) if cover else {"issues": ["No cover art set."]}
    with open(os.path.join(out, "CHECKLIST.md"), "w", encoding="utf-8") as f:
        f.write(DISTRO_CHECKLIST.format(title=r.get("title"), bits=bits, sr=sr,
                                        analysis="\n".join(analysis) +
                                        ("\n- Cover: " + "; ".join(val["issues"]) if val["issues"] else "\n- Cover: OK")))
    if progress:
        progress(1.0)
    return {"path": out, "cover_issues": val["issues"]}


# ---------------------------------------------------------------- Bandcamp / SoundCloud

def direct(r, out_root, profile=None, progress=None, cancel=None):
    out = os.path.join(out_root, safe(f"{r.get('artist')} - {r.get('title')} (Bandcamp + SoundCloud)"))
    os.makedirs(out, exist_ok=True)
    tracks = r.get("tracks", [])
    for i, t in enumerate(tracks, 1):
        audio.to_flac(t["path"], os.path.join(out, _track_name(i, t) + ".flac"),
                      progress=_stage(progress, i - 1, len(tracks)), cancel=cancel)
    _cover(r, out, "cover_3000.jpg")
    _cover(r, out, "cover_1400.jpg", 1400)
    credits = []
    for t in tracks:
        c = [x for x in (t.get("composer") and f"written by {t['composer']}",
                         t.get("producer") and f"produced by {t['producer']}") if x]
        if c:
            credits.append(f"{t.get('title')}: " + ", ".join(c))
    text = [f"TITLE: {r.get('title')}", f"ARTIST: {r.get('artist')}", f"RELEASE DATE: {r.get('release_date', '')}",
            "", "ABOUT:", r.get("description", ""), "", "CREDITS:", *credits,
            f"℗ {r.get('copyright_p', '')}" if r.get("copyright_p") else "", "",
            "TAGS (Bandcamp/SoundCloud): " + ", ".join(meta.tags(r)[:10]), "",
            "LINKS:", meta.links_block(profile)]
    with open(os.path.join(out, "PASTE_THIS.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(text))
    if progress:
        progress(1.0)
    return {"path": out}


# ---------------------------------------------------------------- stock music libraries

def stock(r, out_root, author="", include_cutdowns=True, progress=None, cancel=None):
    out = os.path.join(out_root, safe(f"{r.get('artist')} - {r.get('title')} (Stock music)"))
    full = os.path.join(out, "wav_full")
    cuts = os.path.join(out, "wav_cutdowns")
    mp3 = os.path.join(out, "shutterstock_mp3")
    for d in (full, cuts, mp3):
        os.makedirs(d, exist_ok=True)
    author = safe(author or r.get("artist", "artist")).replace(" ", "").lower()
    tracks = r.get("tracks", [])
    rows, warn = [], []
    steps = len(tracks)
    for i, t in enumerate(tracks, 1):
        slug = re.sub(r"[^a-z0-9]+", "-", (t.get("title") or f"track{i}").lower()).strip("-")
        base = f"{author}_{slug}"
        wav = os.path.join(full, f"{base}_main-01_full.wav")
        audio.to_wav(t["path"], wav, progress=_stage(progress, i - 1, steps), cancel=cancel)
        d = audio.analyze(t["path"])
        if d["duration"] < 60:
            warn.append(f"{t.get('title')}: under 60 s (Storyblocks minimum).")
        if include_cutdowns:
            for sec in (60, 30, 15):
                if d["duration"] > sec + 5:
                    audio.cutdown(t["path"], os.path.join(cuts, f"{base}_short-{sec}sec.wav"), sec)
        m = os.path.join(mp3, f"{base}.mp3")
        audio.to_mp3(t["path"], m, 320)
        if os.path.getsize(m) > 15_000_000:
            audio.to_mp3(t["path"], m, 192)
        bpm = t.get("bpm") or d.get("bpm_estimate")
        tr = dict(t, bpm=bpm)
        rows.append({
            "filename": os.path.basename(wav), "title": t.get("title", ""),
            "description": meta.stock_description(r, tr),
            "keywords": ", ".join(meta.stock_keywords(r, tr)),
            "genre": r.get("genre", ""), "mood": r.get("mood", ""), "bpm": bpm or "",
            "key": t.get("key", ""), "instruments": r.get("instruments", ""),
            "vocals": "yes" if r.get("vocals") else "no",
            "duration_sec": round(d["duration"], 1),
        })
    _write_csv(os.path.join(out, "stock_metadata.csv"), rows)
    notes = ["# Stock library notes", "",
             "- Pond5: non-exclusive marketplace, upload WAVs from wav_full + cut-downs as versions; paste keywords from stock_metadata.csv.",
             "- Storyblocks: apply first; WAV/AIFF, 60 s minimum.",
             "- Shutterstock Music: send your best 5 tracks once as MP3 (shutterstock_mp3, under 15 MB each). Their contract is a full rights transfer - you can't use or sell those tracks anywhere else afterwards.",
             "- Opt these tracks OUT of YouTube Content ID at your distributor, or buyers' videos get claimed and libraries may pull your music.",
             "- File names follow the author_title_version pattern most libraries expect.", ""]
    notes += [f"- WARNING: {w}" for w in warn]
    with open(os.path.join(out, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(notes))
    if progress:
        progress(1.0)
    return {"path": out, "warnings": warn}


# ---------------------------------------------------------------- stock photo pack

def photo_stock(folder, out_root, keywords="", description_prefix="", min_mp=4.0, progress=None, cancel=None):
    """Resized, metadata-stripped JPGs + a CSV in Shutterstock's bulk-upload column layout
    (Filename, Description, Keywords, Categories, Editorial, Mature content, illustration)."""
    out = os.path.join(out_root, "Photo stock pack")
    os.makedirs(out, exist_ok=True)
    rows, skipped = [], []
    imgs = art.list_images(folder)
    kw = [k.strip() for k in keywords.split(",") if k.strip()]
    for i, p in enumerate(imgs):
        if cancel and cancel():
            break
        try:
            im = art.open_rgb(p)
        except Exception:
            skipped.append(os.path.basename(p))
            continue
        mp = im.width * im.height / 1e6
        if mp < min_mp:
            skipped.append(f"{os.path.basename(p)} ({mp:.1f} MP < {min_mp} MP)")
            continue
        name = safe(os.path.splitext(os.path.basename(p))[0]) + ".jpg"
        im.save(os.path.join(out, name), "JPEG", quality=95)  # re-save drops EXIF/GPS data
        words = re.sub(r"[_\-]+", " ", os.path.splitext(os.path.basename(p))[0]).lower().split()
        tags = kw + [w for w in words if len(w) > 2 and not w.isdigit()]
        rows.append({"Filename": name,
                     "Description": (description_prefix + " " + " ".join(words)).strip()[:200] or "Photo",
                     "Keywords": ",".join(dict.fromkeys(tags))[:1000],
                     "Categories": "", "Editorial": "no", "Mature content": "no", "illustration": "no"})
        if progress:
            progress((i + 1) / len(imgs))
    _write_csv(os.path.join(out, "shutterstock_upload.csv"), rows)
    with open(os.path.join(out, "README.txt"), "w") as f:
        f.write("Edit descriptions/keywords in shutterstock_upload.csv, then upload the JPGs and the CSV.\n"
                "Add 7-50 accurate keywords per photo; generic keywords hurt search ranking.\n"
                "Photos with recognisable people need a model release; private property may need a property release.\n"
                + ("\nSkipped:\n" + "\n".join(skipped) if skipped else ""))
    return {"path": out, "count": len(rows), "skipped": skipped}

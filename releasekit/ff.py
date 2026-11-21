"""Thin helpers around ffmpeg / ffprobe with progress reporting."""
import json
import os
import re
import shutil
import subprocess

import sys


def _find(name):
    """ffmpeg/ffprobe: env var, then a copy bundled next to the app (one-click builds), then PATH."""
    env = os.environ.get(name.upper())
    if env:
        return env
    exe = name + (".exe" if os.name == "nt" else "")
    roots = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable),
             os.path.dirname(os.path.dirname(os.path.abspath(__file__)))]
    for r in roots:
        if r:
            p = os.path.join(r, "bin", exe)
            if os.path.isfile(p):
                return p
    return shutil.which(name) or name


FFMPEG = _find("ffmpeg")
FFPROBE = _find("ffprobe")


class FFError(RuntimeError):
    pass


def check_tools():
    return [os.path.basename(t) for t in (FFMPEG, FFPROBE) if not (os.path.isfile(t) or shutil.which(t))]


def tags(path):
    """Embedded metadata (ID3 / Vorbis / iTunes atoms) normalised to Release Kit track fields."""
    try:
        info = probe(path)
    except FFError:
        return {}
    raw = {}
    for src in [info.get("format", {})] + info.get("streams", []):
        for k, v in (src.get("tags") or {}).items():
            raw.setdefault(k.lower(), v)
    out = {}
    mapping = {"title": ["title"], "artist": ["artist", "album_artist"], "album": ["album"], "genre": ["genre"],
               "bpm": ["tbpm", "bpm"], "key": ["tkey", "initialkey", "key"], "isrc": ["tsrc", "isrc"],
               "composer": ["composer", "tcom"], "lyrics": ["lyrics", "unsyncedlyrics", "uslt"],
               "date": ["date", "year"], "copyright": ["copyright"]}
    for field, keys in mapping.items():
        for k in keys:
            if raw.get(k):
                out[field] = str(raw[k]).strip()
                break
    if "bpm" in out:
        try:
            out["bpm"] = round(float(out["bpm"]))
        except ValueError:
            out.pop("bpm")
    return out


FRIENDLY = [
    ("No such file or directory", "A file it needs is missing or was moved. Check the file paths in the Release tab."),
    ("No space left on device", "Your disk is full. Free up space or change the output folder in Settings."),
    ("Permission denied", "Release Kit isn't allowed to write there. Choose another output folder in Settings."),
    ("Invalid data found when processing input", "One of the files isn't a readable audio/image file (it may be damaged or a different format than its name says)."),
    ("does not contain any stream", "That file has no audio in it."),
    ("Unknown encoder", "Your ffmpeg is missing an encoder. Install a full ffmpeg build (see README)."),
    ("Cancelled", "Stopped."),
]


def friendly(err):
    s = str(err)
    if isinstance(err, FileNotFoundError) and ("ffmpeg" in s or "ffprobe" in s):
        return "ffmpeg isn't installed or can't be found. See the README."
    for needle, msg in FRIENDLY:
        if needle in s:
            return msg
    return None


def probe(path):
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise FFError(f"ffprobe failed on {path}: {out.stderr.strip()}")
    return json.loads(out.stdout)


def duration(path):
    info = probe(path)
    d = info.get("format", {}).get("duration")
    if d is None:
        for s in info.get("streams", []):
            if s.get("duration"):
                d = s["duration"]
                break
    return float(d or 0)


def audio_info(path):
    info = probe(path)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if not a:
        raise FFError(f"No audio stream in {path}")
    return {
        "duration": float(info["format"].get("duration", 0)),
        "codec": a.get("codec_name"),
        "sample_rate": int(a.get("sample_rate", 0)),
        "channels": a.get("channels"),
        "bits": a.get("bits_per_raw_sample") or a.get("bits_per_sample"),
        "bitrate": int(info["format"].get("bit_rate", 0) or 0),
        "size": int(info["format"].get("size", 0) or 0),
    }


_time_re = re.compile(r"out_time_ms=(\d+)")


def run(args, total_seconds=None, progress=None, cancel=None):
    """Run ffmpeg with machine-readable progress.

    progress(fraction) is called with 0..1 when total_seconds is known.
    cancel() -> bool lets a job stop the process.
    """
    out_dir = os.path.dirname(str(args[-1])) if args else ""
    if out_dir and not str(args[-1]).startswith("-"):
        os.makedirs(out_dir, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-loglevel", "error"] + list(args)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    err_lines = []

    import threading

    def _drain():
        for line in proc.stderr:
            err_lines.append(line)
            if len(err_lines) > 200:
                del err_lines[:100]

    t = threading.Thread(target=_drain, daemon=True)
    t.start()
    for line in proc.stdout:
        if cancel and cancel():
            proc.kill()
            raise FFError("Cancelled")
        m = _time_re.search(line)
        if m and total_seconds and progress:
            secs = int(m.group(1)) / 1_000_000
            progress(max(0.0, min(1.0, secs / total_seconds)))
    proc.wait()
    t.join(timeout=2)
    if proc.returncode != 0:
        raise FFError("ffmpeg failed:\n" + "".join(err_lines[-25:]) + "\nCommand: " + " ".join(cmd))
    if progress:
        progress(1.0)


def capture(args):
    """Run ffmpeg and return stderr text (for analysis filters)."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin"] + list(args)
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise FFError(out.stderr[-2000:])
    return out.stderr


def esc_text(s):
    """Escape a string for use inside drawtext text='...'."""
    return (s.replace("\\", "\\\\").replace(":", "\\:").replace("'", "’")
            .replace("%", "\\%").replace(",", "\\,"))


def esc_path(p):
    """Escape a file path for use inside a filter argument."""
    p = os.path.abspath(p).replace("\\", "/")
    return p.replace(":", "\\:").replace("'", "\\'")

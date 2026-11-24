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


# ---------------------------------------------------------------- graphics-card (GPU) encoding
# Most PCs can encode H.264 on the graphics chip, several times faster than on the processor.
# We find out once which one works here (by actually encoding a tiny clip), use it for every
# MP4, and if it ever fails mid-job, that video is redone on the processor automatically.
GPU_CANDIDATES = [("h264_nvenc", "NVIDIA"), ("h264_qsv", "Intel Quick Sync"), ("h264_amf", "AMD"),
                  ("h264_videotoolbox", "Apple")]
GPU = {"mode": "auto", "encoder": None, "name": None, "checked": False, "broken": False, "note": ""}
_FALLBACK = {}  # tuple(gpu args) -> cpu args
_gpu_lock = __import__("threading").Lock()


def _gpu_args(enc, q):
    if enc == "h264_nvenc":
        return ["-c:v", enc, "-preset", ("p2", "p4", "p6")[q], "-tune", "hq", "-rc", "vbr",
                "-cq", ("28", "21", "17")[q], "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if enc == "h264_qsv":
        return ["-c:v", enc, "-preset", ("veryfast", "medium", "slower")[q],
                "-global_quality", ("28", "22", "18")[q], "-profile:v", "high", "-pix_fmt", "nv12"]
    if enc == "h264_amf":
        qp = ("28", "22", "18")[q]
        return ["-c:v", enc, "-quality", ("speed", "balanced", "quality")[q], "-rc", "cqp",
                "-qp_i", qp, "-qp_p", qp, "-qp_b", qp, "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if enc == "h264_videotoolbox":
        return ["-c:v", enc, "-q:v", ("45", "60", "72")[q], "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return []


def detect_gpu(force=False):
    """Which graphics-card H.264 encoder works on this computer (None if none, or switched off)."""
    with _gpu_lock:
        if GPU["checked"] and not force:
            return GPU["encoder"]
        GPU.update(checked=True, encoder=None, name=None, note="")
        fake = os.environ.get("RELEASEKIT_FAKE_GPU")  # tests: pretend this encoder exists
        try:
            listed = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                    timeout=20).stdout
        except Exception:
            listed = ""
        for enc, name in GPU_CANDIDATES:
            if fake:
                if enc == fake:
                    GPU.update(encoder=enc, name=name, note="test mode")
                    break
                continue
            if f" {enc} " not in listed:
                continue
            try:  # listed isn't enough: the card and its driver must actually work
                test = subprocess.run([FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                                       "testsrc2=s=640x360:d=1:r=30"] + _gpu_args(enc, 1) + ["-f", "null", "-"],
                                      capture_output=True, text=True, timeout=30)
                if test.returncode == 0:
                    GPU.update(encoder=enc, name=name)
                    break
            except Exception:
                pass
        if not GPU["encoder"]:
            GPU["note"] = "No graphics-card encoder found; videos are made on the processor."
        return GPU["encoder"]


def gpu_status():
    return {"mode": GPU["mode"], "available": GPU["encoder"], "name": GPU["name"], "checked": GPU["checked"],
            "active": bool(GPU["encoder"]) and GPU["mode"] != "off" and not GPU["broken"], "note": GPU["note"],
            "broken": GPU["broken"]}


def h264(fps, quality=1, still=False, cpu_preset=None, crf=None):
    """Encoder settings for an H.264 MP4: graphics card when available and switched on, else libx264.
    quality: 0 draft, 1 standard, 2 high."""
    q = max(0, min(2, int(quality)))
    cpu = ["-c:v", "libx264", "-preset", cpu_preset or ("ultrafast", "veryfast", "slow")[q],
           "-crf", str(crf if crf is not None else ("28", "20", "16")[q]), "-profile:v", "high", "-pix_fmt", "yuv420p"]
    if still:
        cpu += ["-tune", "stillimage"]
    tail = ["-r", str(fps), "-g", str(int(fps) * 2)]
    enc = None if GPU["mode"] == "off" or GPU["broken"] else detect_gpu()
    if not enc:
        return cpu + tail
    gpu = _gpu_args(enc, q) + tail
    _FALLBACK[tuple(gpu)] = cpu + tail
    return gpu


def _swap_gpu(args):
    """args with any graphics-card encoder settings replaced by the processor ones (or None if there were none)."""
    args = list(args)
    for g, c in _FALLBACK.items():
        n = len(g)
        for i in range(len(args) - n + 1):
            if tuple(args[i:i + n]) == g:
                return args[:i] + list(c) + args[i + n:]
    return None


def run(args, total_seconds=None, progress=None, cancel=None, feed=None):
    """Run ffmpeg with machine-readable progress.

    progress(fraction) is called with 0..1 when total_seconds is known.
    cancel() -> bool lets a job stop the process.
    If a graphics-card encode fails, it's redone on the processor (and the card isn't used again this session).
    """
    try:
        return _run(args, total_seconds, progress, cancel, feed)
    except FFError as e:
        if str(e) == "Cancelled" or (cancel and cancel()):
            raise
        cpu_args = _swap_gpu(args)
        if cpu_args is None:
            raise
        GPU["broken"] = True
        GPU["note"] = "The graphics card failed to encode a video, so the processor is used until Release Kit restarts. " \
                      "Updating the graphics driver usually fixes this. Details: " + str(e).splitlines()[1][:200] \
            if len(str(e).splitlines()) > 1 else "The graphics card failed to encode a video; using the processor."
        return _run(cpu_args, total_seconds, progress, cancel, feed)


def _run(args, total_seconds=None, progress=None, cancel=None, feed=None):
    out_dir = os.path.dirname(str(args[-1])) if args else ""
    if out_dir and not str(args[-1]).startswith("-"):
        os.makedirs(out_dir, exist_ok=True)
    cmd = [FFMPEG, "-hide_banner", "-y", "-nostdin", "-progress", "pipe:1", "-loglevel", "error"] + list(args)
    if feed:
        cmd.remove("-nostdin")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
                            stdin=subprocess.PIPE if feed else None)
    err_lines = []

    import threading

    if feed:
        def _feed():
            raw = proc.stdin.buffer if hasattr(proc.stdin, "buffer") else proc.stdin
            try:
                feed(raw.write)
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    raw.close()
                except OSError:
                    pass
        threading.Thread(target=_feed, daemon=True).start()

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

"""Audio analysis, conversion and edits."""
import json
import os
import re
import subprocess

import numpy as np

from . import ff

LOSSY = {"mp3", "aac", "vorbis", "opus", "wmav2"}


def _pcm(path, sr=11025, max_seconds=None):
    args = [ff.FFMPEG, "-v", "error", "-i", path]
    if max_seconds:
        args += ["-t", str(max_seconds)]
    args += ["-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    raw = subprocess.run(args, capture_output=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def loudness(path):
    """Integrated LUFS, true peak and loudness range using ffmpeg's loudnorm analyser."""
    txt = ff.capture(["-i", path, "-af", "loudnorm=I=-14:TP=-1:LRA=11:print_format=json", "-f", "null", "-"])
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", txt, re.S)
    if not m:
        return {}
    d = json.loads(m.group(0))
    return {"lufs": float(d["input_i"]), "true_peak": float(d["input_tp"]), "lra": float(d["input_lra"])}


def estimate_bpm(path):
    """Rough tempo estimate from onset-envelope autocorrelation (60-180 BPM)."""
    sr = 11025
    x = _pcm(path, sr, max_seconds=120)
    if len(x) < sr * 8:
        return None
    hop = 256
    n = len(x) // hop
    frames = x[: n * hop].reshape(n, hop)
    energy = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    onset = np.maximum(0, np.diff(np.log(energy)))
    onset -= onset.mean()
    fps = sr / hop
    ac = np.correlate(onset, onset, mode="full")[len(onset) - 1:]
    lo, hi = int(fps * 60 / 180), int(fps * 60 / 60)
    if hi >= len(ac):
        return None
    lag = lo + int(np.argmax(ac[lo:hi]))
    bpm = 60 * fps / lag
    while bpm < 70:
        bpm *= 2
    while bpm > 170:
        bpm /= 2
    return round(bpm)


def loudest_window(path, seconds):
    """Start time of the most energetic `seconds` window (good for Shorts / previews)."""
    sr = 4000
    x = _pcm(path, sr)
    if len(x) <= sr * seconds:
        return 0.0
    e = np.convolve(x ** 2, np.ones(sr) / sr, mode="valid")[::sr]  # per-second energy
    w = int(seconds)
    if len(e) <= w:
        return 0.0
    sums = np.convolve(e, np.ones(w), mode="valid")
    # avoid the very start/end, prefer something after the intro
    return float(int(np.argmax(sums)))


def analyze(path):
    info = ff.audio_info(path)
    info.update(loudness(path))
    info["bpm_estimate"] = estimate_bpm(path)
    warnings = []
    if info["codec"] in LOSSY:
        warnings.append(f"Source is lossy ({info['codec']}). Distributors want WAV/FLAC masters; export a lossless file from your DAW if you can.")
    if info["sample_rate"] and info["sample_rate"] < 44100:
        warnings.append(f"Sample rate {info['sample_rate']} Hz is below 44.1 kHz.")
    tp = info.get("true_peak")
    if tp is not None and tp > -1.0:
        warnings.append(f"True peak {tp:.1f} dBTP is above -1 dBTP; streaming encoders may clip. Consider a limiter ceiling of -1 dBTP.")
    lufs = info.get("lufs")
    if lufs is not None:
        if lufs > -8:
            warnings.append(f"Very loud master ({lufs:.1f} LUFS). Spotify/YouTube will turn it down to about -14 LUFS; you lose dynamics for no gain.")
        elif lufs < -20:
            warnings.append(f"Quiet master ({lufs:.1f} LUFS). It will play quieter than most tracks on platforms that don't turn quiet songs up.")
    if info["duration"] < 30:
        warnings.append("Under 30 seconds: Spotify does not count streams shorter than 30 s.")
    info["warnings"] = warnings
    return info


def to_wav(src, dst, sr=44100, bits=16, progress=None, cancel=None):
    codec = {16: "pcm_s16le", 24: "pcm_s24le"}[bits]
    ff.run(["-i", src, "-map", "0:a:0", "-ar", str(sr), "-c:a", codec, dst],
           ff.duration(src), progress, cancel)
    return dst


def to_flac(src, dst, progress=None, cancel=None):
    ff.run(["-i", src, "-map", "0:a:0", "-c:a", "flac", dst], ff.duration(src), progress, cancel)
    return dst


def to_mp3(src, dst, kbps=320, progress=None, cancel=None):
    ff.run(["-i", src, "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", f"{kbps}k", dst],
           ff.duration(src), progress, cancel)
    return dst


def cutdown(src, dst, length, start=0.0, fade=None, codec_args=None):
    """Make an edit of `length` seconds with a fade-out ending (for stock 60/30/15s versions)."""
    fade = fade or (1.0 if length <= 15 else 2.5)
    af = f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(0, length - fade)}:d={fade}"
    args = ["-ss", str(start), "-t", str(length), "-i", src, "-af", af]
    args += codec_args or ["-c:a", "pcm_s16le", "-ar", "44100"]
    ff.run(args + [dst])
    return dst


def build_cycle(tracks, dst, crossfade=0.0, gap=0.0, ambient=None, ambient_db=-18.0,
                progress=None, cancel=None):
    """Join tracks into one seamless loop cycle (WAV).

    With crossfade>0, the tail of the cycle is crossfaded into its own head, so
    concatenating the cycle with itself has no audible seam.
    """
    inputs, filters = [], []
    for i, t in enumerate(tracks):
        inputs += ["-i", t]
        filters.append(f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo[a{i}]")
    n = len(tracks)
    cur = "a0"
    for i in range(1, n):
        if crossfade > 0:
            filters.append(f"[{cur}][a{i}]acrossfade=d={crossfade}:c1=tri:c2=tri[j{i}]")
        else:
            if gap > 0:
                filters.append(f"[{cur}]apad=pad_dur={gap}[g{i}]")
                filters.append(f"[g{i}][a{i}]concat=n=2:v=0:a=1[j{i}]")
            else:
                filters.append(f"[{cur}][a{i}]concat=n=2:v=0:a=1[j{i}]")
        cur = f"j{i}"
    total = sum(ff.duration(t) for t in tracks)
    if crossfade > 0:
        total -= crossfade * (n - 1)
    elif gap > 0:
        filters.append(f"[{cur}]apad=pad_dur={gap}[padded]")
        cur = "padded"
        total += gap * n
    if ambient:
        inputs += ["-stream_loop", "-1", "-i", ambient]
        filters.append(f"[{n}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,volume={ambient_db}dB[amb]")
        filters.append(f"[{cur}][amb]amix=inputs=2:duration=first:normalize=0[mixed]")
        cur = "mixed"
    joined = dst if crossfade <= 0 else dst + ".joined.wav"
    half = (lambda f: progress(f * 0.7)) if progress and crossfade > 0 else progress
    ff.run(inputs + ["-filter_complex", ";".join(filters), "-map", f"[{cur}]",
                     "-c:a", "pcm_s16le", "-ar", "48000", joined], total, half, cancel)
    if crossfade > 0:
        # seamless loop: body = cycle[xf:], whose tail is crossfaded into the head cycle[0:xf].
        # The result ends exactly where the next repetition begins.
        xf = min(crossfade, total / 3)
        fc = (f"[0:a]atrim=start={xf},asetpts=PTS-STARTPTS[body];"
              f"[1:a]atrim=end={xf},asetpts=PTS-STARTPTS[head];"
              f"[body][head]acrossfade=d={xf}:c1=tri:c2=tri[loop]")
        ff.run(["-i", joined, "-i", joined, "-filter_complex", fc, "-map", "[loop]",
                "-c:a", "pcm_s16le", "-ar", "48000", dst], total - xf,
               (lambda f: progress(0.7 + f * 0.3)) if progress else None, cancel)
        os.remove(joined)
    return dst

"""Check GitHub for a newer Release Kit and install it (Windows build).

The Windows build updates itself: it downloads the release zip, then a small helper script
waits for Release Kit to close, copies the new files over the old ones and starts it again.
Your releases, settings and renders live in ~/ReleaseKit, not in the app folder, so they are untouched.
Running from source: you get a link to the release instead.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from . import __version__

REPO = "kefirman9000/SL2U"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "ReleaseKit-windows-x64.zip"
# updates are only ever downloaded from this repo's GitHub releases
DOWNLOAD_PREFIX = f"https://github.com/{REPO}/releases/download/"
REDIRECT_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")

# testing hooks (used by the Windows build check on GitHub)
TEST_ZIP = os.environ.get("RELEASEKIT_UPDATE_TEST_ZIP")
TEST_VERSION = os.environ.get("RELEASEKIT_UPDATE_TEST_VERSION")

STATE = {"checked": None, "result": None, "error": None, "applying": False, "progress": 0.0, "message": ""}


def parse_version(v):
    parts = []
    for x in str(v).lstrip("vV").split("."):
        num = "".join(ch for ch in x if ch.isdigit())
        parts.append(int(num) if num else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def is_frozen():
    return bool(getattr(sys, "frozen", False))


def can_self_update():
    return is_frozen() and os.name == "nt"


def check(timeout=8):
    if TEST_VERSION:
        res = {"latest": TEST_VERSION, "notes": "Test update", "page": "", "asset_url": TEST_ZIP, "size": 0,
               "sha256": _sha256(TEST_ZIP) if TEST_ZIP and os.path.isfile(TEST_ZIP) else None}
    else:
        req = urllib.request.Request(API, headers={"User-Agent": f"ReleaseKit/{__version__}",
                                                   "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.load(r)
        asset = next((a for a in d.get("assets", []) if a["name"] == ASSET), None)
        digest = (asset or {}).get("digest") or ""
        res = {"latest": d.get("tag_name", "").lstrip("v"), "notes": d.get("body") or "", "page": d.get("html_url"),
               "asset_url": asset and asset["browser_download_url"], "size": asset and asset["size"],
               "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None}
    res["current"] = __version__
    res["newer"] = parse_version(res["latest"]) > parse_version(__version__)
    res["can_self_update"] = can_self_update() and bool(res["asset_url"]) and bool(res["sha256"])
    STATE.update(checked=time.time(), result=res, error=None)
    return res


def check_in_background(enabled=True):
    def run():
        try:
            check()
        except Exception as e:  # offline, rate-limited...
            STATE["error"] = str(e)
    if enabled:
        threading.Thread(target=run, daemon=True).start()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follow GitHub's redirect to its download servers, but nowhere else and never to plain http."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlsplit(newurl)
        if u.scheme != "https" or u.hostname not in REDIRECT_HOSTS:
            raise urllib.error.URLError(f"Update download redirected somewhere unexpected ({u.hostname}); cancelled.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download(url, dst):
    if TEST_ZIP and url == TEST_ZIP and os.path.isfile(url):  # local file (tests only)
        shutil.copy(url, dst)
        STATE["progress"] = 0.6
        return
    if not str(url).startswith(DOWNLOAD_PREFIX):
        raise ValueError("The update isn't hosted on Release Kit's GitHub page; update cancelled.")
    opener = urllib.request.build_opener(_SafeRedirects)
    req = urllib.request.Request(url, headers={"User-Agent": f"ReleaseKit/{__version__}"})
    with opener.open(req, timeout=60) as r, open(dst, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                STATE["progress"] = 0.6 * got / total


PS1 = r"""
$ErrorActionPreference = "Continue"
$log = '__LOG__'
function L($m) { Add-Content -LiteralPath $log -Value ("{0:HH:mm:ss} {1}" -f (Get-Date), $m) }
L "Release Kit update to __VERSION__: waiting for the app (pid __PID__) to close"
try { Wait-Process -Id __PID__ -Timeout 120 -ErrorAction SilentlyContinue } catch { }
Start-Sleep -Seconds 1
L "copying new files"
$code = 99
for ($i = 1; $i -le 5; $i++) {
  & robocopy '__NEW__' '__APP__' /E /R:5 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
  $code = $LASTEXITCODE
  L "robocopy attempt $i exit code $code"
  if ($code -lt 8) { break }
  Start-Sleep -Seconds 2
}
if ($code -ge 8) {
  Set-Content -LiteralPath (Join-Path '__APP__' 'UPDATE-FAILED.txt') -Value "Release Kit could not copy all update files. Details: $log"
  L "FAILED"
} else {
  Remove-Item -LiteralPath (Join-Path '__APP__' 'UPDATE-FAILED.txt') -ErrorAction SilentlyContinue
}
$env:RELEASEKIT_NO_BROWSER = "1"
Start-Process -FilePath (Join-Path '__APP__' 'ReleaseKit.exe') -WorkingDirectory '__APP__'
L "restarted Release Kit"
"""


def apply(shutdown):
    """Download and stage the update, start the helper script, then call shutdown() to exit."""
    res = STATE["result"] or check()
    if not res.get("newer"):
        raise ValueError("You already have the latest version.")
    if not can_self_update():
        raise ValueError("Automatic update works in the Windows app. Download the new version from GitHub instead.")
    if not res.get("sha256"):
        raise ValueError("GitHub didn't publish a checksum for this update, so it can't be verified. "
                         "Download it from the releases page instead.")
    STATE.update(applying=True, progress=0.0, message="Downloading…")
    tmp = tempfile.mkdtemp(prefix="rk_update_")
    zpath = os.path.join(tmp, "update.zip")
    _download(res["asset_url"], zpath)
    STATE.update(progress=0.65, message="Checking the download…")
    if _sha256(zpath) != res["sha256"]:
        raise RuntimeError("The downloaded update doesn't match GitHub's checksum (damaged or tampered with); "
                           "update cancelled. Try again later or download it from the releases page.")
    STATE.update(progress=0.7, message="Unpacking…")
    new_root = os.path.join(tmp, "new")
    root_real = os.path.realpath(new_root)
    with zipfile.ZipFile(zpath) as z:
        for name in z.namelist():  # every file must land inside the staging folder
            dest = os.path.realpath(os.path.join(new_root, name))
            if os.path.commonpath([dest, root_real]) != root_real:
                raise RuntimeError(f"The update contains an unsafe path ({name}); update cancelled.")
        z.extractall(new_root)
    exe = next((os.path.join(r, "ReleaseKit.exe") for r, _, fs in os.walk(new_root) if "ReleaseKit.exe" in fs), None)
    if not exe:
        raise RuntimeError("The download doesn't contain ReleaseKit.exe; update cancelled.")
    new_dir = os.path.dirname(exe)
    app_dir = os.path.dirname(sys.executable)
    log = os.path.join(app_dir, "update-log.txt")
    q = lambda v: str(v).replace("'", "''")  # inside '...' PowerShell strings, ' is the only special character
    version = "".join(ch for ch in str(res["latest"]) if ch.isalnum() or ch in ".-")
    script = (PS1.replace("__LOG__", q(log)).replace("__PID__", str(os.getpid())).replace("__NEW__", q(new_dir))
              .replace("__APP__", q(app_dir)).replace("__VERSION__", version))
    ps1 = os.path.join(tmp, "update.ps1")
    with open(ps1, "w", encoding="utf-8-sig") as f:  # BOM so Windows PowerShell reads it as UTF-8
        f.write(script)
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", ps1]
    out = open(os.path.join(app_dir, "update-helper-output.txt"), "w")
    base = 0x08000000 | 0x00000200  # CREATE_NO_WINDOW (hidden console) | CREATE_NEW_PROCESS_GROUP
    try:  # also leave any job object, so closing Release Kit can't take the helper down with it
        subprocess.Popen(cmd, creationflags=base | 0x01000000, stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, cwd=tmp)
    except OSError:
        subprocess.Popen(cmd, creationflags=base, stdin=subprocess.DEVNULL, stdout=out,
                         stderr=subprocess.STDOUT, cwd=tmp)
    out.close()
    STATE.update(progress=0.9, message="Restarting…")
    threading.Timer(1.5, shutdown).start()
    return {"restarting": True, "version": res["latest"]}

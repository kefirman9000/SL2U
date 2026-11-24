"""Keeps the local web app private to you.

Release Kit is a web page served from your own computer (127.0.0.1). Without protection, any
website you visit could try to talk to it in the background: read files through it, start
renders, or upload to your YouTube channel. This module blocks that:

* Host check: only requests addressed to 127.0.0.1 / localhost on our port are answered.
  This stops "DNS rebinding", where a website points its own domain at 127.0.0.1.
* Origin / Sec-Fetch-Site check: requests made by other websites are refused.
* Session key: every API call must carry a secret key. The browser gets it as a cookie when
  Release Kit opens the page for you (the link printed in the black window has it too).
  Other programs and pages don't know it.
* Security headers: the page can't be framed by other sites and can only talk to itself.
"""
import hmac
import os
import secrets

from flask import Response, abort, redirect, request

HEADER = "X-ReleaseKit-Key"
# endpoints that are safe without the key (the updater's "is it back yet?" ping)
OPEN_PATHS = {"/api/version"}

LOCKED_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Release Kit</title>
<style>body{background:#000;color:#d8f5d0;font:16px/1.5 system-ui,sans-serif;display:flex;align-items:center;
justify-content:center;height:100vh;margin:0}div{max-width:520px;padding:24px;border:1px solid #1b2a1b;border-radius:8px}
b{color:#39ff14}code{background:#0d120d;padding:2px 6px;border-radius:4px}</style></head><body><div>
<p><b>Release Kit is locked to keep it private.</b></p>
<p>Open it with the link shown in the Release Kit window (it starts with
<code>http://127.0.0.1:%PORT%/?key=</code>), or close Release Kit and start it again so it opens this page for you.</p>
<p style="color:#7f9a7a;font-size:13px">This stops other websites from controlling Release Kit in the background.</p>
</div></body></html>"""

CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self'; connect-src 'self'; "
       "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def load_key(data_dir):
    """(key, new): RELEASEKIT_TOKEN if set, else a random key kept in the data folder.

    It's kept between runs so the browser stays signed in after a restart or an update.
    new is True the first time a key is made (first run, or the update that added it)."""
    env = os.environ.get("RELEASEKIT_TOKEN")
    if env:
        return env, False
    path = os.path.join(data_dir, "session_key")
    try:
        with open(path, encoding="utf-8") as f:
            key = f.read().strip()
        if len(key) >= 32:
            return key, False
    except OSError:
        pass
    key = secrets.token_urlsafe(32)
    fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key)
    os.replace(path + ".tmp", path)
    return key, True


class Guard:
    def __init__(self, app, data_dir, port_fn):
        self.key, self.new_key = load_key(data_dir)
        self.port_fn = port_fn
        app.before_request(self.check)
        app.after_request(self.headers)

    # ------------------------------------------------------------ helpers
    @property
    def port(self):
        return int(self.port_fn())

    @property
    def cookie_name(self):
        return f"rk_key_{self.port}"  # cookies are shared between ports, so name it per port

    def hosts(self):
        p = self.port
        return {f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}"}

    def origins(self):
        return {"http://" + h for h in self.hosts()}

    def _valid(self, value):
        return bool(value) and hmac.compare_digest(str(value).encode(), self.key.encode())

    def authed(self):
        return self._valid(request.headers.get(HEADER)) or self._valid(request.cookies.get(self.cookie_name))

    def url(self, port=None):
        return f"http://127.0.0.1:{port or self.port}/?key={self.key}"

    # ------------------------------------------------------------ hooks
    def check(self):
        # 1. DNS rebinding: a page on evil.example resolving to 127.0.0.1 still sends Host: evil.example
        if (request.host or "").lower() not in self.hosts():
            abort(Response("Unknown host", status=421, mimetype="text/plain"))

        # 2. Requests started by another website
        origin = request.headers.get("Origin")
        if origin and origin not in self.origins():  # includes "null" (sandboxed frames, file:// pages)
            abort(Response("Cross-site request blocked", status=403, mimetype="text/plain"))
        site = request.headers.get("Sec-Fetch-Site")
        path = request.path
        if path.startswith("/api/") and site and site not in ("same-origin", "none"):
            # "same-site" includes other apps on localhost, so it isn't good enough either
            abort(Response("Cross-site request blocked", status=403, mimetype="text/plain"))

        # 3. The session key
        if path == "/":
            if self._valid(request.args.get("key")):
                resp = redirect("/")  # drop the key from the address bar and history
                resp.set_cookie(self.cookie_name, self.key, max_age=400 * 86400, httponly=True,
                                samesite="Strict", path="/")
                return resp
            if not self.authed():
                return Response(LOCKED_PAGE.replace("%PORT%", str(self.port)), status=401, mimetype="text/html")
            return None
        if path.startswith("/static/"):
            return None  # fonts only
        if path in OPEN_PATHS:
            return None
        if not self.authed():
            abort(Response('{"error": "Release Kit is locked. Reopen it from the Release Kit window."}',
                           status=401, mimetype="application/json"))
        return None

    def headers(self, resp):
        h = resp.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if resp.mimetype == "text/html":
            h.setdefault("Content-Security-Policy", CSP)
        if request.path.startswith("/api/") and request.path not in ("/api/file", "/api/thumb"):
            h.setdefault("Cache-Control", "no-store")
        return resp

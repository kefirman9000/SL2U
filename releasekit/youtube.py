"""YouTube Data API v3 uploader (optional: needs google-api-python-client + google-auth-oauthlib).

Setup: create a Google Cloud project, enable YouTube Data API v3, create an OAuth client of
type 'Desktop app', download it as client_secret.json into the Release Kit data folder.
The first upload opens a browser window to sign in; the token is saved for next time.
"""
import os

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]
MUSIC_CATEGORY = "10"


def available():
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        return True
    except ImportError:
        return False


class NeedsSignIn(RuntimeError):
    pass


def signed_in(data_dir):
    return os.path.exists(os.path.join(data_dir, "youtube_token.json"))


def sign_out(data_dir):
    p = os.path.join(data_dir, "youtube_token.json")
    if os.path.exists(p):
        os.remove(p)


def _service(data_dir, interactive=True):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    secret = os.path.join(data_dir, "client_secret.json")
    token = os.path.join(data_dir, "youtube_token.json")
    if not os.path.exists(secret):
        raise RuntimeError(f"client_secret.json not found in {data_dir}. See the Sign-ups tab, section 7.")
    creds = Credentials.from_authorized_user_file(token, SCOPES) if os.path.exists(token) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                if not interactive:
                    raise NeedsSignIn("YouTube sign-in expired. Press 'Connect YouTube' in the Upload tab.")
                creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0)
        elif not interactive:
            # never pop a browser window from the background scheduler
            raise NeedsSignIn("Not signed in to YouTube. Press 'Connect YouTube' in the Upload tab.")
        else:
            creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0)
        # the token is a password for your channel: readable by you only
        fd = os.open(token + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(creds.to_json())
        os.replace(token + ".tmp", token)
    return build("youtube", "v3", credentials=creds)


def connect(data_dir):
    """Interactive sign-in; returns the channel name."""
    return channel(data_dir, interactive=True)


def channel(data_dir, interactive=False):
    yt = _service(data_dir, interactive)
    items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
    if not items:
        return {"title": "(no channel on this Google account)", "id": None}
    return {"title": items[0]["snippet"]["title"], "id": items[0]["id"]}


def playlists(data_dir):
    yt = _service(data_dir, interactive=False)
    out, token = [], None
    while True:
        r = yt.playlists().list(part="snippet", mine=True, maxResults=50, pageToken=token).execute()
        out += [{"id": i["id"], "title": i["snippet"]["title"]} for i in r.get("items", [])]
        token = r.get("nextPageToken")
        if not token:
            return out


def create_playlist(data_dir, title, privacy="public"):
    yt = _service(data_dir, interactive=False)
    r = yt.playlists().insert(part="snippet,status", body={"snippet": {"title": title[:150]},
                                                           "status": {"privacyStatus": privacy}}).execute()
    return {"id": r["id"], "title": title}


def error_kind(e):
    """'quota' (daily limit hit, try tomorrow), 'auth' (needs sign-in) or 'other'."""
    if isinstance(e, NeedsSignIn):
        return "auth"
    text = str(e)
    for reason in ("quotaExceeded", "uploadLimitExceeded", "rateLimitExceeded", "dailyLimitExceeded"):
        if reason in text:
            return "quota"
    if "invalid_grant" in text or "401" in text:
        return "auth"
    return "other"


def upload(data_dir, video_path, title, description, tags=None, privacy="private", publish_at=None,
           thumbnail=None, playlist_id=None, made_for_kids=False, progress=None, cancel=None, interactive=True):
    from googleapiclient.http import MediaFileUpload

    yt = _service(data_dir, interactive)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": bool(made_for_kids)}
    if publish_at:  # scheduled publish requires private status, ISO 8601 UTC
        status["privacyStatus"] = "private"
        status["publishAt"] = publish_at
    body = {"snippet": {"title": title[:100], "description": description[:5000], "tags": tags or [],
                        "categoryId": MUSIC_CATEGORY}, "status": status}
    media = MediaFileUpload(video_path, chunksize=16 * 1024 * 1024, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    resp = None
    while resp is None:
        if cancel and cancel():
            raise RuntimeError("Cancelled")
        st, resp = req.next_chunk(num_retries=5)
        if st and progress:
            progress(st.progress() * 0.95)
    vid = resp["id"]
    notes = []
    if thumbnail and os.path.exists(thumbnail):
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(thumbnail)).execute()
        except Exception as e:  # custom thumbnails need a verified channel
            notes.append(f"Thumbnail not set ({e}). Verify your channel at youtube.com/verify to allow custom thumbnails.")
    if playlist_id:
        try:
            yt.playlistItems().insert(part="snippet", body={"snippet": {
                "playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": vid}}}).execute()
        except Exception as e:
            notes.append(f"Could not add to playlist: {e}")
    if progress:
        progress(1.0)
    return {"video_id": vid, "url": f"https://youtu.be/{vid}", "notes": notes}

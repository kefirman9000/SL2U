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


def _service(data_dir):
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
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(secret, SCOPES).run_local_server(port=0)
        with open(token, "w") as f:
            f.write(creds.to_json())
    return build("youtube", "v3", credentials=creds)


def upload(data_dir, video_path, title, description, tags=None, privacy="private", publish_at=None,
           thumbnail=None, playlist_id=None, made_for_kids=False, progress=None, cancel=None):
    from googleapiclient.http import MediaFileUpload

    yt = _service(data_dir)
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

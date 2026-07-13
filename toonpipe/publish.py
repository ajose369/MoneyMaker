"""Stage 6b — YouTube upload via Data API v3.

One-time interactive OAuth (`toonpipe auth`), then fully hands-off:
the refresh token in yt_token.json is reused forever. One upload costs
1600 quota units against the 10,000/day default.
"""

from __future__ import annotations

from pathlib import Path

from .config import ROOT, Config
from .manifest import Manifest
from .meta import description_with_chapters

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

LANG_CODES = {"english": "en", "hindi": "hi", "malayalam": "ml", "tamil": "ta"}


def _token_path(cfg: Config) -> Path:
    return ROOT / str(cfg.get("publish", {}).get("token_file", "yt_token.json"))


def get_credentials(cfg: Config, interactive: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token_path = _token_path(cfg)
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    if not creds or not creds.valid:
        if not interactive:
            raise RuntimeError(
                "No valid YouTube credentials. Run `python -m toonpipe auth` once "
                "(requires client_secret.json from Google Cloud Console — see README)."
            )
        secrets = ROOT / str(cfg.get("publish", {}).get("client_secrets", "client_secret.json"))
        if not secrets.exists():
            raise RuntimeError(f"Missing {secrets} — download OAuth desktop credentials (see README)")
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
        creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")
        print(f"Saved YouTube token to {token_path}")
    return creds


def upload(cfg: Config, m: Manifest) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    assert m.final_video and m.meta
    if m.youtube_id:
        print(f"  [publish] already uploaded: https://youtu.be/{m.youtube_id}")
        return m.youtube_id

    pub = cfg.get("publish", {})
    creds = get_credentials(cfg)
    yt = build("youtube", "v3", credentials=creds)

    lang = LANG_CODES.get(str(cfg.get("language", "English")).lower(), "en")
    body = {
        "snippet": {
            "title": m.meta.title[:100],
            "description": description_with_chapters(m)[:4900],
            "tags": m.meta.tags[:30],
            "categoryId": str(pub.get("category_id", "1")),
            "defaultLanguage": lang,
            "defaultAudioLanguage": lang,
        },
        "status": {
            "privacyStatus": pub.get("privacy", "public"),
            "selfDeclaredMadeForKids": bool(pub.get("made_for_kids", True)),
        },
    }
    media = MediaFileUpload(str(m.path_for(m.final_video)), chunksize=8 * 1024 * 1024,
                            resumable=True, mimetype="video/mp4")
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    print("  [publish] uploading…")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"    {int(status.progress() * 100)}%")
    video_id = response["id"]

    if m.thumbnail:
        try:
            yt.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(m.path_for(m.thumbnail)), mimetype="image/jpeg"),
            ).execute()
        except Exception as e:
            # Thumbnail setting requires a verified channel; not fatal.
            print(f"  [publish] thumbnail upload skipped: {e}")

    m.youtube_id = video_id
    m.save()
    print(f"  [publish] done: https://youtu.be/{video_id} (privacy: {body['status']['privacyStatus']})")
    return video_id

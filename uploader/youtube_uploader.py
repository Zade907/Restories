"""
YouTube uploader: OAuth2 login + upload video as Shorts with metadata.
Uses Google's official youtube-upload library via google-api-python-client.
"""

import json
import os
from pathlib import Path
from typing import Optional

from config.utils import CONFIG, env, get_logger, retry

logger = get_logger("uploader")

# ── OAuth setup ───────────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

def get_youtube_service():
    """
    Build and return an authenticated YouTube service.
    Handles OAuth2 token refresh and initial login flow.
    """
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API libraries not installed.\n"
            "Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        )

    creds_file = env("YOUTUBE_CREDENTIALS_FILE", "config/youtube_credentials.json")
    secrets_file = env("YOUTUBE_CLIENT_SECRETS_FILE", "config/client_secrets.json")

    creds = None
    if os.path.exists(creds_file):
        creds = Credentials.from_authorized_user_file(creds_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(secrets_file):
                raise FileNotFoundError(
                    f"YouTube client secrets not found at '{secrets_file}'.\n"
                    "Download it from Google Cloud Console:\n"
                    "https://console.cloud.google.com/apis/credentials"
                )
            flow = InstalledAppFlow.from_client_secrets_file(secrets_file, SCOPES)
            creds = flow.run_local_server(port=0)

        # Save credentials for future runs
        with open(creds_file, "w") as f:
            f.write(creds.to_json())

    service = build("youtube", "v3", credentials=creds)
    return service


# ── Upload ────────────────────────────────────────────────────────────────────────

@retry(attempts=2, delay=15, logger_name="uploader")
def upload_video(
    video_path: str,
    title: str,
    description: str,
    hashtags: list[str],
    thumbnail_path: Optional[str] = None,
    privacy_status: Optional[str] = None,
) -> dict:
    """
    Upload a video to YouTube.
    Returns the YouTube API response (contains video ID and URL).
    """
    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        raise ImportError("Install: pip install google-api-python-client")

    yt_cfg = CONFIG["youtube"]
    privacy = privacy_status or yt_cfg.get("privacy_status", "private")

    # Build hashtag string for description
    tag_str = " ".join(f"#{t}" for t in hashtags[:20])
    full_description = f"{description}\n\n{tag_str}\n\n#Shorts"

    # YouTube tags (flat list, no #)
    tags = hashtags[:50] + ["Shorts"]

    body = {
        "snippet": {
            "title": title[:100],
            "description": full_description[:5000],
            "tags": tags,
            "categoryId": yt_cfg.get("category_id", "22"),
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": yt_cfg.get("made_for_kids", False),
        },
    }

    logger.info(f"Uploading '{title}' to YouTube ({privacy})...")

    service = get_youtube_service()
    media = MediaFileUpload(
        video_path,
        chunksize=5 * 1024 * 1024,  # 5MB chunks
        resumable=True,
        mimetype="video/mp4",
    )

    request = service.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            logger.info(f"Upload progress: {pct}%")

    video_id = response.get("id", "")
    video_url = f"https://www.youtube.com/shorts/{video_id}"
    logger.info(f"Upload complete! Video ID: {video_id} — {video_url}")

    # Upload thumbnail if provided
    if thumbnail_path and os.path.exists(thumbnail_path):
        _upload_thumbnail(service, video_id, thumbnail_path)

    return {
        "video_id": video_id,
        "url": video_url,
        "title": title,
        "status": privacy,
    }


def _upload_thumbnail(service, video_id: str, thumbnail_path: str):
    """Set a custom thumbnail for an uploaded video."""
    try:
        from googleapiclient.http import MediaFileUpload
        media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
        service.thumbnails().set(videoId=video_id, media_body=media).execute()
        logger.info(f"Thumbnail uploaded for video {video_id}")
    except Exception as e:
        logger.warning(f"Thumbnail upload failed: {e}")


# ── Dry-run / simulation ──────────────────────────────────────────────────────────

def simulate_upload(video_path: str, title: str, description: str,
                    hashtags: list, **kwargs) -> dict:
    """Simulate an upload (for testing without YouTube credentials)."""
    logger.info(f"[DRY RUN] Would upload: {title}")
    return {
        "video_id": "SIMULATED_ID",
        "url": "https://www.youtube.com/shorts/SIMULATED_ID",
        "title": title,
        "status": "dry_run",
    }

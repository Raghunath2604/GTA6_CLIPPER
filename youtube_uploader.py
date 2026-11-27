"""
YouTube Shorts 1-Click Uploader (OAuth 2.0 via Google API Python Client).
Uploads generated clips as Private Drafts with #Shorts, Gaming category,
YouTube tag budget enforcement, and resumable chunk retries.
"""

import os
import re
import glob
import time
import shutil
from typing import Dict, Any, List, Optional
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

from logger import get_logger

log = get_logger("youtube_uploader")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def sanitize_youtube_tags(
    tags: Optional[List[str]] = None,
    default_tags: Optional[List[str]] = None,
    max_total_chars: int = 460
) -> List[str]:
    """Strip invalid characters, deduplicate, and enforce YouTube's 500-char tag budget."""
    defaults = default_tags or ["Shorts", "Gaming", "GamingClips", "LiveStream", "FunnyMoments", "Viral"]
    combined = list(tags or []) + list(defaults)

    seen = set()
    cleaned: List[str] = []
    used_chars = 0

    for raw in combined:
        tag = re.sub(r"[<>\"#]", "", str(raw or "")).strip()[:30]
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        extra_len = len(tag) + (len(tag.split()) > 1) * 2 + (1 if cleaned else 0)
        if used_chars + extra_len > max_total_chars:
            continue
        seen.add(key)
        cleaned.append(tag)
        used_chars += extra_len

    return cleaned


def find_client_secret_file() -> Optional[str]:
    """
    Finds client_secret.json or any downloaded Google Cloud OAuth JSON file
    (e.g., client_secret_*.json) and ensures it is ready for use.
    """
    if os.path.exists(CLIENT_SECRET_FILE) and os.path.getsize(CLIENT_SECRET_FILE) > 10:
        return CLIENT_SECRET_FILE

    patterns = [
        os.path.join(BASE_DIR, "*client_secret*.json"),
        os.path.join(BASE_DIR, "client_secrets*.json"),
        os.path.join(BASE_DIR, "*.apps.googleusercontent.com.json")
    ]
    candidates = []
    for pat in patterns:
        for match in glob.glob(pat):
            if match != CLIENT_SECRET_FILE and os.path.exists(match) and os.path.getsize(match) > 10:
                candidates.append(match)

    if candidates:
        chosen = candidates[0]
        try:
            shutil.copyfile(chosen, CLIENT_SECRET_FILE)
            log.info(f"Auto-detected '{os.path.basename(chosen)}' -> linked as 'client_secret.json'")
            return CLIENT_SECRET_FILE
        except Exception as e:
            log.warning(f"Could not link client secret: {e}")
            return chosen

    return None


def is_client_secret_present() -> bool:
    """Checks if client_secret.json or any valid secret file exists."""
    return find_client_secret_file() is not None


def is_token_present() -> bool:
    """Checks if token.json exists and has valid credentials."""
    return os.path.exists(TOKEN_FILE) and os.path.getsize(TOKEN_FILE) > 10


def get_oauth_status() -> Dict[str, Any]:
    """Returns the current YouTube OAuth configuration status."""
    has_secret = is_client_secret_present()
    has_token = is_token_present()
    status_msg = "Not Configured (Missing client_secret.json)"

    if has_secret and not has_token:
        secret_path = find_client_secret_file()
        fname = os.path.basename(secret_path) if secret_path else "client_secret.json"
        status_msg = f"Ready for Login ({fname} detected ✅)"
    elif has_secret and has_token:
        status_msg = "Authenticated & Connected ✅"

    return {
        "has_secret": has_secret,
        "has_token": has_token,
        "status_text": status_msg
    }


def _parse_youtube_http_error(http_err: HttpError) -> Optional[str]:
    """Return an actionable message for well-known YouTube Data API HTTP errors."""
    status = getattr(getattr(http_err, "resp", None), "status", None)
    raw_content = getattr(http_err, "content", b"")
    if isinstance(raw_content, bytes):
        body = raw_content.decode("utf-8", errors="ignore")
    else:
        body = str(raw_content or "")
    combined = f"{http_err} {body}"
    if status == 403 and ("quotaExceeded" in combined or "uploadLimitExceeded" in combined):
        return (
            "YouTube Data API daily upload quota exceeded (HTTP 403 quotaExceeded). "
            "Wait for the daily quota reset or request higher quota in Google Cloud Console."
        )
    return None


def get_authenticated_service(interactive: bool = True):
    """Authenticates using client_secret.json and token.json."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            log.warning(f"Error reading {TOKEN_FILE}: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                log.warning(f"OAuth token refresh error: {e}")
                creds = None
                if not interactive:
                    raise RuntimeError(
                        "YouTube OAuth token expired or revoked. "
                        "Connect your YouTube channel in the sidebar or run Login_CLI.bat --service youtube."
                    ) from e

        if not creds:
            secret_path = find_client_secret_file()
            if not secret_path:
                raise FileNotFoundError(
                    "Missing 'client_secret.json'. Please download your OAuth 2.0 Client credentials JSON "
                    "from Google Cloud Console and place it into this project folder."
                )
            if not interactive:
                raise RuntimeError(
                    "YouTube OAuth required — connect your YouTube channel in the sidebar "
                    "or run Login_CLI.bat --service youtube before uploading."
                )
            flow = InstalledAppFlow.from_client_secrets_file(secret_path, SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        with open(TOKEN_FILE, "w", encoding="utf-8") as token_f:
            token_f.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)


def upload_short_to_youtube(
    video_path: str,
    title: str,
    description: str,
    tags: Optional[List[str]] = None,
    privacy_status: str = "private",
    thumbnail_path: Optional[str] = None,
    interactive: bool = True,
) -> Dict[str, Any]:
    """
    Uploads 9:16 video to YouTube Shorts:
    - Appends #Shorts to title (enforcing 100-char limit)
    - Category ID 20 (Gaming), tags within YouTube's 500-char limit
    - Resumable upload with transient 5xx/network chunk retry
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    clean_title = (title or "Gaming Highlight").strip()
    if "#shorts" not in clean_title.lower():
        clean_title = f"{clean_title[:92].rstrip()} #Shorts"
    clean_title = clean_title[:100]

    clean_desc = (description or clean_title).strip()[:5000]
    merged_tags = sanitize_youtube_tags(tags)

    youtube = get_authenticated_service(interactive=interactive)

    body = {
        "snippet": {
            "title": clean_title,
            "description": clean_desc,
            "tags": merged_tags,
            "categoryId": "20"
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 4
    )

    log.info(f"Uploading '{clean_title}' to YouTube Shorts as {privacy_status}...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retry_count = 0
    while response is None:
        try:
            status, response = request.next_chunk()
            retry_count = 0
            if status:
                log.info(f"YouTube upload progress: {int(status.progress() * 100)}%")
        except HttpError as http_err:
            if http_err.resp.status in (500, 502, 503, 504) and retry_count < 3:
                retry_count += 1
                time.sleep(2 ** retry_count)
                continue
            friendly = _parse_youtube_http_error(http_err)
            if friendly:
                raise RuntimeError(friendly) from http_err
            raise
        except (OSError, TimeoutError) as net_err:
            if retry_count < 3:
                retry_count += 1
                log.warning(f"Transient network error during chunk upload ({net_err}); retrying ({retry_count}/3)...")
                time.sleep(2 ** retry_count)
                continue
            raise

    video_id = response.get("id")
    shorts_url = f"https://www.youtube.com/shorts/{video_id}"
    log.info(f"YouTube upload complete! Video ID: {video_id} -> {shorts_url}")

    thumb_set = False
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            thumb_media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            thumb_set = True
        except Exception as thumb_err:
            log.warning(f"Custom thumbnail warning: {thumb_err}")

    return {
        "status": "success",
        "video_id": video_id,
        "url": shorts_url,
        "title": clean_title,
        "privacy": privacy_status,
        "thumbnail_set": thumb_set
    }

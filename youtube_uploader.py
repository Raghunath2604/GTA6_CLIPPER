"""
YouTube Shorts 1-Click Uploader (OAuth 2.0 via Google API Python Client).
Uploads generated clips as Private Drafts with #Shorts and Gaming category.
Automatically detects client_secret.json or any Google Cloud downloaded secret JSON file.
"""

import os
import glob
import json
import shutil
from typing import Dict, Any, List, Optional
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly"
]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")

def find_client_secret_file() -> Optional[str]:
    """
    Finds client_secret.json or any downloaded Google Cloud OAuth JSON file
    (e.g., client_secret_*.json) and ensures it is ready for use.
    """
    if os.path.exists(CLIENT_SECRET_FILE) and os.path.getsize(CLIENT_SECRET_FILE) > 10:
        return CLIENT_SECRET_FILE

    # Search for any downloaded secret JSON files
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
            # Copy to standard client_secret.json
            shutil.copyfile(chosen, CLIENT_SECRET_FILE)
            print(f"[YouTube Uploader] Auto-detected '{os.path.basename(chosen)}' -> linked as 'client_secret.json'")
            return CLIENT_SECRET_FILE
        except Exception as e:
            print(f"[YouTube Uploader] Warning linking client secret: {e}")
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

def get_authenticated_service():
    """Authenticates using client_secret.json and token.json."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[YouTube Uploader] Error reading {TOKEN_FILE}: {e}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"[YouTube Uploader] Refresh error: {e}")
                creds = None

        if not creds:
            secret_path = find_client_secret_file()
            if not secret_path:
                raise FileNotFoundError(
                    "Missing 'client_secret.json'. Please download your OAuth 2.0 Client credentials JSON "
                    "from Google Cloud Console and place it into this project folder."
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
    thumbnail_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Uploads 9:16 video to YouTube Shorts:
    - Appends #Shorts to title
    - Category ID 20 (Gaming)
    - Privacy: private (Draft)
    - MadeForKids: False
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Ensure #Shorts is in title
    clean_title = title.strip()
    if "#Shorts" not in clean_title and "#shorts" not in clean_title:
        clean_title = f"{clean_title} #Shorts"
    clean_title = clean_title[:100]

    default_tags = ["Shorts", "Gaming", "GamingClips", "LiveStream", "FunnyMoments", "Viral"]
    merged_tags = list(dict.fromkeys((tags or []) + default_tags))

    youtube = get_authenticated_service()

    body = {
        "snippet": {
            "title": clean_title,
            "description": description,
            "tags": merged_tags,
            "categoryId": "20"  # Gaming category
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

    print(f"[YouTube Uploader] Uploading '{clean_title}' to YouTube Shorts as {privacy_status} draft...")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"[YouTube Uploader] Upload progress: {progress}%")

    video_id = response.get("id")
    shorts_url = f"https://www.youtube.com/shorts/{video_id}"
    print(f"[YouTube Uploader] Upload complete! Video ID: {video_id} -> {shorts_url}")

    thumb_set = False
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            thumb_media = MediaFileUpload(thumbnail_path, mimetype="image/jpeg")
            youtube.thumbnails().set(videoId=video_id, media_body=thumb_media).execute()
            thumb_set = True
        except Exception as thumb_err:
            print(f"[YouTube Uploader] Custom thumbnail warning: {thumb_err}")

    return {
        "status": "success",
        "video_id": video_id,
        "url": shorts_url,
        "title": clean_title,
        "privacy": privacy_status,
        "thumbnail_set": thumb_set
    }

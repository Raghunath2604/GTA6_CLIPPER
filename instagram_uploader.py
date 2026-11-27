"""
Instagram Reels Uploader & Connection Manager (GTA6 Viral Clipper Pro).
Supports two seamless integration pathways:
1. Direct Account Connection (instagrapi):
   - Logs in with Instagram username/password (or session file ig_session.json) with 2FA support.
   - Direct local file .mp4 reel upload with caption and optional thumbnail.
2. Official Meta Graph API (Content Publishing API):
   - Uses INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID.
   - Streams local video binaries via Meta Resumable Upload protocol.
"""

import os
import time
import requests
from typing import Dict, Any, Optional

from ffmpeg_utils import get_media_duration
from logger import get_logger

log = get_logger("instagram_uploader")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
IG_SESSION_FILE = os.path.join(BASE_DIR, "ig_session.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")


def get_instagram_config() -> Dict[str, str]:
    """Retrieves Instagram credentials from environment."""
    return {
        "username": os.getenv("INSTAGRAM_USERNAME", "").strip(),
        "password": os.getenv("INSTAGRAM_PASSWORD", "").strip(),
        "access_token": os.getenv("INSTAGRAM_ACCESS_TOKEN", "").strip(),
        "account_id": os.getenv("INSTAGRAM_ACCOUNT_ID", "").strip()
    }


def is_instagram_connected() -> bool:
    """Checks whether either Direct Login or Meta Graph API is ready."""
    cfg = get_instagram_config()
    has_session = os.path.exists(IG_SESSION_FILE) and os.path.getsize(IG_SESSION_FILE) > 20
    has_direct_creds = bool(cfg["username"] and cfg["password"])
    has_meta_creds = bool(cfg["access_token"] and cfg["account_id"])
    return has_session or has_direct_creds or has_meta_creds


def get_instagram_status() -> Dict[str, Any]:
    """Returns the connection status and active method."""
    cfg = get_instagram_config()
    has_session = os.path.exists(IG_SESSION_FILE) and os.path.getsize(IG_SESSION_FILE) > 20
    has_direct_creds = bool(cfg["username"] and cfg["password"])
    has_meta_creds = bool(cfg["access_token"] and cfg["account_id"])

    if has_session:
        username = cfg["username"] or "Saved Session"
        return {
            "connected": True,
            "method": "Direct Account Session",
            "account": username,
            "status_text": f"Connected (@{username}) ✅"
        }
    elif has_direct_creds:
        return {
            "connected": True,
            "method": "Direct Credentials",
            "account": cfg["username"],
            "status_text": f"Ready to Connect (@{cfg['username']}) ✅"
        }
    elif has_meta_creds:
        return {
            "connected": True,
            "method": "Meta Graph API",
            "account": cfg["account_id"],
            "status_text": "Connected (Meta Graph API) ✅"
        }
    else:
        return {
            "connected": False,
            "method": "None",
            "account": "",
            "status_text": "Not Connected (Add credentials in .env or sidebar)"
        }


def save_instagram_credentials(
    username: Optional[str] = None,
    password: Optional[str] = None,
    access_token: Optional[str] = None,
    account_id: Optional[str] = None
) -> None:
    """Updates or appends Instagram credentials to the project .env file."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updates = {}
    if username is not None:
        updates["INSTAGRAM_USERNAME"] = username
    if password is not None:
        updates["INSTAGRAM_PASSWORD"] = password
    if access_token is not None:
        updates["INSTAGRAM_ACCESS_TOKEN"] = access_token
    if account_id is not None:
        updates["INSTAGRAM_ACCOUNT_ID"] = account_id

    new_lines = []
    found_keys = set()
    for line in lines:
        matched = False
        for k, v in updates.items():
            if line.strip().startswith(f"{k}=") or line.strip().startswith(f"# {k}="):
                new_lines.append(f"{k}={v}\n")
                found_keys.add(k)
                os.environ[k] = v
                matched = True
                break
        if not matched:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in found_keys:
            new_lines.append(f"{k}={v}\n")
            os.environ[k] = v

    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)


def _validate_reel_file(video_path: str) -> Optional[str]:
    if not os.path.exists(video_path):
        return f"Video file not found: {video_path}"
    if os.path.getsize(video_path) < 1024:
        return f"Video file is too small or corrupt: {video_path}"
    dur = get_media_duration(video_path)
    if dur > 0 and dur < 3.0:
        return f"Instagram Reels must be at least 3 seconds long (got {dur:.1f}s)."
    if dur > 90.0:
        log.warning(f"Clip duration {dur:.1f}s exceeds standard 90s Reels length; Meta may trim or reject.")
    return None


def login_instagram_direct(
    username: str,
    password: str,
    verification_code: Optional[str] = None
) -> Dict[str, Any]:
    """Authenticates directly with Instagram and saves session settings."""
    try:
        from instagrapi import Client
        cl = Client()
        cl.delay_range = [1, 3]

        if os.path.exists(IG_SESSION_FILE):
            try:
                cl.load_settings(IG_SESSION_FILE)
            except Exception:
                pass

        if verification_code:
            cl.login(username, password, verification_code=verification_code.strip())
        else:
            cl.login(username, password)

        cl.dump_settings(IG_SESSION_FILE)
        save_instagram_credentials(username=username, password=password)
        log.info(f"Authenticated Instagram direct account @{username}")

        return {
            "status": "success",
            "message": f"Successfully authenticated as @{username}!",
            "username": username
        }
    except Exception as e:
        err_msg = str(e)
        if "two_factor_required" in err_msg.lower():
            return {
                "status": "two_factor_required",
                "message": "Two-factor authentication required. Please enter your 2FA security code.",
                "error": err_msg
            }
        log.warning(f"Instagram login failed: {err_msg}")
        return {
            "status": "error",
            "message": f"Instagram login failed: {err_msg}",
            "error": err_msg
        }


def upload_reel_direct(
    video_path: str,
    caption: str,
    thumbnail_path: Optional[str] = None
) -> Dict[str, Any]:
    """Uploads a vertical clip directly to Instagram Reels using instagrapi."""
    err = _validate_reel_file(video_path)
    if err:
        return {"status": "error", "method": "direct", "error": err}

    cfg = get_instagram_config()
    username = cfg["username"]
    password = cfg["password"]

    try:
        from instagrapi import Client
        cl = Client()
        cl.delay_range = [1, 3]

        logged_in = False
        if os.path.exists(IG_SESSION_FILE):
            try:
                cl.load_settings(IG_SESSION_FILE)
                cl.get_timeline_feed()
                logged_in = True
            except Exception:
                logged_in = False

        if not logged_in and username and password:
            cl.login(username, password)
            cl.dump_settings(IG_SESSION_FILE)
            logged_in = True

        if not logged_in:
            return {
                "status": "error",
                "method": "direct",
                "error": "Instagram session expired or missing credentials. Please log in first."
            }

        log.info(f"Uploading reel '{os.path.basename(video_path)}' via direct account...")
        media = cl.clip_upload(
            path=video_path,
            caption=(caption or "")[:2200],
            thumbnail=thumbnail_path if (thumbnail_path and os.path.exists(thumbnail_path)) else None
        )

        code = media.code
        reel_url = f"https://www.instagram.com/reel/{code}/"
        log.info(f"Instagram direct upload complete! Reel URL: {reel_url}")

        return {
            "status": "success",
            "method": "direct",
            "media_id": str(media.id),
            "code": code,
            "url": reel_url,
            "caption": caption
        }
    except Exception as e:
        log.warning(f"Instagram direct upload failed: {e}")
        return {"status": "error", "method": "direct", "error": str(e)}


def upload_reel_meta_graph(
    video_url_or_path: str,
    caption: str,
    access_token: Optional[str] = None,
    account_id: Optional[str] = None
) -> Dict[str, Any]:
    """Publishes a 9:16 vertical video to Instagram Reels via the Meta Content Publishing API."""
    cfg = get_instagram_config()
    token = access_token or cfg["access_token"]
    act_id = account_id or cfg["account_id"]

    if not token or not act_id:
        return {
            "status": "error",
            "error": "Instagram Meta Graph API credentials missing (set INSTAGRAM_ACCESS_TOKEN and INSTAGRAM_ACCOUNT_ID)."
        }

    api_version = "v19.0"
    base_url = f"https://graph.facebook.com/{api_version}"
    init_url = f"{base_url}/{act_id}/media"
    is_local_file = os.path.exists(video_url_or_path)

    if is_local_file:
        val_err = _validate_reel_file(video_url_or_path)
        if val_err:
            return {"status": "error", "error": val_err}

        file_size = os.path.getsize(video_url_or_path)
        init_payload = {
            "access_token": token,
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": (caption or "")[:2200],
            "share_to_feed": "true"
        }
        try:
            init_res = requests.post(init_url, data=init_payload, timeout=30).json()
            if "id" not in init_res or "uri" not in init_res:
                err_msg = init_res.get("error", {}).get("message", "Resumable container creation failed")
                return {"status": "error", "error": err_msg}

            container_id = init_res["id"]
            upload_uri = init_res["uri"]

            upload_headers = {
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(file_size),
                "Content-Type": "application/octet-stream"
            }
            with open(video_url_or_path, "rb") as vf:
                rupload_res = requests.post(upload_uri, headers=upload_headers, data=vf, timeout=180)
            if rupload_res.status_code not in (200, 201):
                return {"status": "error", "error": f"Binary upload to rupload failed ({rupload_res.status_code}): {rupload_res.text}"}

        except Exception as e:
            return {"status": "error", "error": f"Meta Resumable Upload error: {e}"}
    else:
        init_payload = {
            "access_token": token,
            "media_type": "REELS",
            "caption": (caption or "")[:2200],
            "share_to_feed": "true",
            "video_url": video_url_or_path
        }
        try:
            res_data = requests.post(init_url, data=init_payload, timeout=30).json()
            if "id" not in res_data:
                return {"status": "error", "error": res_data.get("error", {}).get("message", "Container creation failed")}
            container_id = res_data["id"]
        except Exception as e:
            return {"status": "error", "error": str(e)}

    status_url = f"{base_url}/{container_id}"
    finished = False
    for _ in range(40):
        time.sleep(5)
        try:
            s_res = requests.get(
                status_url,
                params={"access_token": token, "fields": "status_code,status"},
                timeout=15
            ).json()
            code = s_res.get("status_code")
            if code == "FINISHED":
                finished = True
                break
            elif code == "ERROR":
                detail = s_res.get("status") or "Meta video transcoding failed on Instagram servers."
                return {"status": "error", "error": detail}
        except Exception:
            pass

    if not finished:
        return {"status": "error", "error": "Timed out waiting for Instagram container processing."}

    try:
        pub_url = f"{base_url}/{act_id}/media_publish"
        pub_res = requests.post(pub_url, data={"access_token": token, "creation_id": container_id}, timeout=30).json()
        if "id" in pub_res:
            media_id = pub_res["id"]
            return {
                "status": "success",
                "method": "meta_graph",
                "media_id": media_id,
                "url": f"https://www.instagram.com/p/{media_id}/"
            }
        return {"status": "error", "error": pub_res.get("error", {}).get("message", "Publish failed")}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def upload_reel_to_instagram(
    video_url_or_path: str,
    caption: str,
    thumbnail_path: Optional[str] = None,
    access_token: Optional[str] = None,
    account_id: Optional[str] = None
) -> Dict[str, Any]:
    """Automatically selects the best active method (Direct Account or Meta Graph) and publishes the Reel."""
    cfg = get_instagram_config()

    if os.path.exists(IG_SESSION_FILE) or (cfg["username"] and cfg["password"]):
        res = upload_reel_direct(video_url_or_path, caption, thumbnail_path=thumbnail_path)
        if res.get("status") == "success":
            return res
        if not ((access_token or cfg["access_token"]) and (account_id or cfg["account_id"])):
            return res

    if (access_token or cfg["access_token"]) and (account_id or cfg["account_id"]):
        return upload_reel_meta_graph(video_url_or_path, caption, access_token=access_token, account_id=account_id)

    return {
        "status": "not_configured",
        "error": "Instagram is not connected. Add INSTAGRAM_USERNAME & PASSWORD or INSTAGRAM_ACCESS_TOKEN & ACCOUNT_ID."
    }

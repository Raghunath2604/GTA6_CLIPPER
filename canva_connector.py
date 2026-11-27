"""
Canva Connect API Integration for GTA6 Viral Clipper Pro.
Handles token validation, refresh, credential persistence in .env,
and uploading clean 9:16 gameplay frames to Canva assets for Brand Templates.
"""

import os
import json
import time
import base64
import requests
from typing import Dict, Any, Optional

from logger import get_logger

log = get_logger("canva_connector")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CANVA_TOKEN_FILE = os.path.join(BASE_DIR, "canva_token.json")
ENV_FILE = os.path.join(BASE_DIR, ".env")

try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    pass


def get_canva_config() -> Dict[str, str]:
    """Read Canva Connect API settings and tokens from env/token file."""
    token_data: Dict[str, Any] = {}
    if os.path.exists(CANVA_TOKEN_FILE):
        try:
            with open(CANVA_TOKEN_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, dict):
                    token_data = loaded
        except Exception as e:
            log.warning(f"Could not read {CANVA_TOKEN_FILE}: {e}")

    return {
        "client_id": os.getenv("CANVA_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("CANVA_CLIENT_SECRET", "").strip(),
        "brand_template_id": os.getenv("CANVA_BRAND_TEMPLATE_ID", "").strip(),
        "access_token": os.getenv("CANVA_ACCESS_TOKEN", "").strip() or str(token_data.get("access_token", "")).strip(),
        "refresh_token": os.getenv("CANVA_REFRESH_TOKEN", "").strip() or str(token_data.get("refresh_token", "")).strip(),
    }


def is_canva_connected() -> bool:
    """Return True if a Canva Connect access or refresh token is available."""
    cfg = get_canva_config()
    return bool(cfg["access_token"] or (cfg["refresh_token"] and cfg["client_id"] and cfg["client_secret"]))


def get_canva_status() -> Dict[str, Any]:
    """Inspect Canva connection state without making blocking network calls."""
    cfg = get_canva_config()
    has_creds = bool(cfg["client_id"] and cfg["client_secret"])
    connected = bool(cfg["access_token"])
    if connected:
        status_text = "Connected (Canva Connect API) ✅"
    elif has_creds:
        status_text = "Client Credentials Configured (Run Login_CLI.bat --service canva)"
    else:
        status_text = "Optional — Configure Client ID/Secret to sync Canva Assets"

    return {
        "connected": connected,
        "has_client_creds": has_creds,
        "brand_template_id": cfg["brand_template_id"],
        "status_text": status_text,
    }


def save_canva_credentials(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    brand_template_id: Optional[str] = None,
    access_token: Optional[str] = None,
    refresh_token: Optional[str] = None,
) -> None:
    """Persist Canva configuration keys cleanly into .env and process environment."""
    lines = []
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

    updates: Dict[str, str] = {}
    for key, val in (
        ("CANVA_CLIENT_ID", client_id),
        ("CANVA_CLIENT_SECRET", client_secret),
        ("CANVA_BRAND_TEMPLATE_ID", brand_template_id),
        ("CANVA_ACCESS_TOKEN", access_token),
        ("CANVA_REFRESH_TOKEN", refresh_token),
    ):
        if val is not None:
            updates[key] = val.strip()

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


def disconnect_canva() -> None:
    """Remove local Canva token cache cleanly."""
    if os.path.exists(CANVA_TOKEN_FILE):
        try:
            os.remove(CANVA_TOKEN_FILE)
        except Exception:
            pass
    save_canva_credentials(access_token="", refresh_token="")


def _refresh_canva_token() -> Optional[str]:
    """Attempt OAuth 2.0 refresh token exchange when access_token expires."""
    cfg = get_canva_config()
    if not (cfg["refresh_token"] and cfg["client_id"] and cfg["client_secret"]):
        return None
    auth_header = base64.b64encode(f"{cfg['client_id']}:{cfg['client_secret']}".encode("utf-8")).decode("utf-8")
    try:
        resp = requests.post(
            "https://api.canva.com/rest/v1/oauth/token",
            headers={
                "Authorization": f"Basic {auth_header}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "refresh_token", "refresh_token": cfg["refresh_token"]},
            timeout=20,
        )
        data = resp.json()
        if resp.status_code == 200 and "access_token" in data:
            with open(CANVA_TOKEN_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            save_canva_credentials(
                access_token=data["access_token"],
                refresh_token=data.get("refresh_token", cfg["refresh_token"]),
            )
            return data["access_token"]
    except Exception as e:
        log.warning(f"Canva token refresh failed: {e}")
    return None


def upload_thumbnail_asset_to_canva(image_path: str, asset_name: Optional[str] = None) -> Dict[str, Any]:
    """Upload a clean 9:16 gameplay thumbnail to Canva Connect Assets API."""
    if not os.path.exists(image_path):
        return {"status": "error", "error": f"Image file not found: {image_path}"}

    cfg = get_canva_config()
    token = cfg["access_token"]
    if not token:
        token = _refresh_canva_token() or ""
    if not token:
        return {"status": "not_configured", "error": "Canva Connect OAuth token missing. Run Login_CLI.bat --service canva."}

    name_clean = (asset_name or os.path.basename(image_path))[:50]
    metadata_b64 = base64.b64encode(json.dumps({"name_base64": base64.b64encode(name_clean.encode("utf-8")).decode("utf-8")}).encode("utf-8")).decode("utf-8")

    def _do_upload(bearer: str) -> requests.Response:
        with open(image_path, "rb") as f:
            return requests.post(
                "https://api.canva.com/rest/v1/asset-uploads",
                headers={
                    "Authorization": f"Bearer {bearer}",
                    "Content-Type": "application/octet-stream",
                    "Asset-Upload-Metadata": metadata_b64,
                },
                data=f,
                timeout=60,
            )

    try:
        resp = _do_upload(token)
        if resp.status_code == 401:
            refreshed = _refresh_canva_token()
            if refreshed:
                token = refreshed
                resp = _do_upload(token)

        if resp.status_code not in (200, 201):
            return {"status": "error", "error": f"Canva asset upload failed ({resp.status_code}): {resp.text[:200]}"}

        job = resp.json().get("job", {})
        job_id = job.get("id")
        for _ in range(15):
            if job.get("status") == "success":
                asset = job.get("asset", {})
                return {
                    "status": "success",
                    "asset_id": asset.get("id"),
                    "name": asset.get("name", name_clean),
                }
            if job.get("status") == "failed":
                return {"status": "error", "error": str(job.get("error", "Canva upload job failed"))}
            if not job_id:
                break
            time.sleep(2)
            poll = requests.get(
                f"https://api.canva.com/rest/v1/asset-uploads/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15,
            )
            if poll.status_code == 200:
                job = poll.json().get("job", {})

        return {"status": "error", "error": "Timed out waiting for Canva asset upload job."}
    except Exception as e:
        return {"status": "error", "error": str(e)}

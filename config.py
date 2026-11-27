"""
Configuration manager for GTA6 Viral Clipper Pro.
Handles loading and persisting settings with priority:
config.json > .env > defaults
"""

import os
import json
import threading
from typing import Dict, Any

from logger import get_logger

log = get_logger("config")
_CONFIG_LOCK = threading.Lock()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass


def atomic_write_text(file_path: str, content: str, encoding: str = "utf-8") -> None:
    """Write text atomically via a temp file + os.replace to prevent truncated writes."""
    abs_path = os.path.abspath(file_path)
    parent = os.path.dirname(abs_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp_path = f"{abs_path}.tmp.{os.getpid()}.{threading.get_ident()}"
    try:
        with open(tmp_path, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, abs_path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def atomic_write_json(file_path: str, payload: Any, indent: int = 2) -> None:
    """Serialize JSON and persist atomically."""
    atomic_write_text(file_path, json.dumps(payload, indent=indent) + "\n")


DEFAULT_CONFIG: Dict[str, Any] = {
    "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    "streamer_urls": "\n".join([
        "https://www.youtube.com/@IShowSpeed/streams",
        "https://www.youtube.com/@TGGOfficial/videos",
        "https://www.youtube.com/@DarkViperAU/streams",
        "https://www.youtube.com/@NoughtPointFourLIVE/streams",
        "https://www.youtube.com/@Mythpat/streams"
    ]),
    "parallel_workers": int(os.getenv("PARALLEL_WORKERS", 3)),
    "live_mode": os.getenv("LIVE_MODE", "record_buffer"),
    "clips_per_stream": int(os.getenv("CLIPS_PER_STREAM", 2)),
    "clip_duration_seconds": int(os.getenv("CLIP_DURATION_SECONDS", 35)),
    "max_capture_minutes": int(os.getenv("MAX_CAPTURE_MINUTES", 30)),
    "auto_subtitles": os.getenv("AUTO_SUBTITLES", "true").lower() == "true",
    "burn_hook_banner": os.getenv("BURN_HOOK_BANNER", "true").lower() == "true",
    "auto_cleanup": os.getenv("AUTO_CLEANUP", "true").lower() == "true",
    "output_dir": os.getenv("OUTPUT_DIR", "Output_Clips")
}


def load_config() -> Dict[str, Any]:
    with _CONFIG_LOCK:
        cfg = DEFAULT_CONFIG.copy()
        if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 5:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    for k, v in saved.items():
                        if k == "gemini_api_key" and not v and cfg.get("gemini_api_key"):
                            continue
                        cfg[k] = v
            except Exception as e:
                log.warning(f"Error reading {CONFIG_FILE}: {e}")
        return cfg


def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    with _CONFIG_LOCK:
        cfg = DEFAULT_CONFIG.copy()
        cfg["gemini_api_key"] = ""
        if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 5:
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    if isinstance(saved, dict):
                        cfg.update(saved)
            except Exception as e:
                log.warning(f"Error loading existing {CONFIG_FILE} during save: {e}")
        cfg.update(updates)
        try:
            atomic_write_json(CONFIG_FILE, cfg, indent=4)
        except Exception as e:
            log.warning(f"Error writing {CONFIG_FILE}: {e}")
    return load_config()


def update_env_file(env_path: str, updates: Dict[str, str], also_set_environ: bool = True) -> None:
    """Safely update or append KEY=VALUE entries in a .env file atomically."""
    with _CONFIG_LOCK:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

        clean_updates = {str(k): str(v) for k, v in updates.items()}
        new_lines = []
        found_keys = set()
        for line in lines:
            matched = False
            stripped = line.strip()
            for k, v in clean_updates.items():
                if stripped.startswith(f"{k}=") or stripped.startswith(f"# {k}="):
                    new_lines.append(f"{k}={v}\n")
                    found_keys.add(k)
                    if also_set_environ:
                        os.environ[k] = v
                    matched = True
                    break
            if not matched:
                new_lines.append(line if line.endswith("\n") else f"{line}\n")

        for k, v in clean_updates.items():
            if k not in found_keys:
                new_lines.append(f"{k}={v}\n")
                if also_set_environ:
                    os.environ[k] = v

        atomic_write_text(env_path, "".join(new_lines))

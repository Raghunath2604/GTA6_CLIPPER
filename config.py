"""
Configuration manager for GTA6 Viral Clipper Pro.
Handles loading and persisting settings with priority:
config.json > .env > defaults
"""

import os
import json
from typing import Dict, Any

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

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
            print(f"[Config] Error reading {CONFIG_FILE}: {e}")
    return cfg

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = DEFAULT_CONFIG.copy()
    cfg["gemini_api_key"] = ""
    if os.path.exists(CONFIG_FILE) and os.path.getsize(CONFIG_FILE) > 5:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    cfg.update(saved)
        except Exception:
            pass
    cfg.update(updates)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[Config] Error writing {CONFIG_FILE}: {e}")
    return load_config()

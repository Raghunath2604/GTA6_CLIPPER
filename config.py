"""
Configuration manager for GTAG Viral Clipper Pro.
Handles loading and persisting settings with priority:
config.json > .env > defaults
"""

import os
import json
from typing import Dict, Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG: Dict[str, Any] = {
    "gemini_api_key": os.getenv("GEMINI_API_KEY", ""),
    "streamer_urls": "\n".join([
        "https://www.youtube.com/@JmanCurly/streams",
        "https://www.youtube.com/@VMT/streams",
        "https://www.youtube.com/@ElliotVR/streams",
        "https://www.youtube.com/@K9_VR/streams",
        "https://www.youtube.com/@TTTPIG/streams",
        "https://www.youtube.com/@electronicwall/streams"
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
                cfg.update(saved)
        except Exception as e:
            print(f"[Config] Error reading {CONFIG_FILE}: {e}")
    return cfg

def save_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    cfg = load_config()
    cfg.update(updates)
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4)
    except Exception as e:
        print(f"[Config] Error writing {CONFIG_FILE}: {e}")
    return cfg

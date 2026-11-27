"""
Thread-safe processed stream deduplication registry.
Maintains unique processed video IDs in processed_videos.txt.
"""

import os
import threading
from typing import Set

from logger import get_logger

log = get_logger("processed_tracker")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_FILE = os.path.join(BASE_DIR, "processed_videos.txt")
_LOCK = threading.Lock()


def _load_ids() -> Set[str]:
    if not os.path.exists(TRACKER_FILE):
        return set()
    ids = set()
    try:
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    vid_id = line.split()[0]
                    ids.add(vid_id)
    except Exception as e:
        log.warning(f"Error loading {TRACKER_FILE}: {e}")
    return ids


def is_processed(video_id: str) -> bool:
    """Return True if video_id has already been recorded as processed."""
    vid = str(video_id or "").strip()
    if not vid:
        return False
    with _LOCK:
        return vid in _load_ids()


def mark_processed(video_id: str, title: str = "") -> None:
    """Record video_id idempotently (avoids duplicate entries across reruns)."""
    vid = str(video_id or "").strip()
    if not vid:
        return
    with _LOCK:
        try:
            if vid in _load_ids():
                return
            with open(TRACKER_FILE, "a", encoding="utf-8") as f:
                safe_title = title.replace("\n", " ").replace("\r", " ").strip()
                f.write(f"{vid}\t{safe_title}\n")
        except Exception as e:
            log.warning(f"Error writing {vid} to {TRACKER_FILE}: {e}")


def get_all_processed() -> Set[str]:
    """Return a snapshot of all processed video IDs."""
    with _LOCK:
        return _load_ids()

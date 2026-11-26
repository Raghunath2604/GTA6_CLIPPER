"""
Thread-safe processed stream deduplication registry.
Maintains processed video IDs in processed_videos.txt.
"""

import os
import threading
from typing import Set

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
        print(f"[Tracker] Error loading {TRACKER_FILE}: {e}")
    return ids

def is_processed(video_id: str) -> bool:
    if not video_id:
        return False
    with _LOCK:
        return video_id in _load_ids()

def mark_processed(video_id: str, title: str = "") -> None:
    if not video_id:
        return
    with _LOCK:
        try:
            with open(TRACKER_FILE, "a", encoding="utf-8") as f:
                safe_title = title.replace("\n", " ").strip()
                f.write(f"{video_id}\t{safe_title}\n")
        except Exception as e:
            print(f"[Tracker] Error writing {video_id} to {TRACKER_FILE}: {e}")

def get_all_processed() -> Set[str]:
    with _LOCK:
        return _load_ids()

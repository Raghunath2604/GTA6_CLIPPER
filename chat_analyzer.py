"""
Live Chat Explosion Velocity Calculator for GTA6 Viral Clipper Pro.
Parses yt-dlp .live_chat.json files into 10-second intervals and detects
the top timestamp hotspots with the highest message burst velocity.
"""

import os
import json
from typing import List, Dict, Tuple
from collections import defaultdict

class ChatHotspot:
    def __init__(self, timestamp_seconds: float, message_count: int, spike_score: float):
        self.timestamp_seconds = timestamp_seconds
        self.message_count = message_count
        self.spike_score = spike_score

    @property
    def formatted_time(self) -> str:
        mins = int(self.timestamp_seconds // 60)
        secs = int(self.timestamp_seconds % 60)
        return f"{mins:02d}:{secs:02d}"

    def to_dict(self) -> Dict:
        return {
            "timestamp_seconds": self.timestamp_seconds,
            "formatted_time": self.formatted_time,
            "message_count": self.message_count,
            "spike_score": round(self.spike_score, 2)
        }

def parse_live_chat_timestamps(chat_file_path: str) -> List[float]:
    if not os.path.exists(chat_file_path) or os.path.getsize(chat_file_path) == 0:
        return []

    timestamps = []
    try:
        with open(chat_file_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    offset_usec = None
                    if "replayChatItemAction" in data:
                        actions = data["replayChatItemAction"].get("actions", [])
                        for a in actions:
                            item = a.get("addChatItemAction", {}).get("item", {})
                            renderer = (
                                item.get("liveChatTextMessageRenderer") or
                                item.get("liveChatPaidMessageRenderer")
                            )
                            if renderer and "timestampUsec" in renderer:
                                pass
                        offset_str = data["replayChatItemAction"].get("videoOffsetTimeMsec")
                        if offset_str is not None:
                            timestamps.append(float(offset_str) / 1000.0)
                    elif "videoOffsetTimeMsec" in data:
                        timestamps.append(float(data["videoOffsetTimeMsec"]) / 1000.0)
                except Exception:
                    continue
    except Exception as e:
        print(f"[Chat Analyzer] Error parsing chat file {chat_file_path}: {e}")

    return sorted(timestamps)

def calculate_top_hotspots(
    timestamps: List[float],
    video_duration_sec: float,
    interval_sec: float = 10.0,
    top_n: int = 6
) -> List[ChatHotspot]:
    if not timestamps:
        return []

    buckets = defaultdict(int)
    for ts in timestamps:
        if 0 <= ts <= video_duration_sec:
            bucket_idx = int(ts // interval_sec)
            buckets[bucket_idx] += 1

    if not buckets:
        return []

    counts = list(buckets.values())
    avg_count = sum(counts) / max(1, len(counts))

    candidate_hotspots = []
    for bucket_idx, count in buckets.items():
        center_sec = (bucket_idx * interval_sec) + (interval_sec / 2.0)
        spike_score = (count / max(1.0, avg_count)) * 10.0
        candidate_hotspots.append(ChatHotspot(center_sec, count, spike_score))

    candidate_hotspots.sort(key=lambda x: x.spike_score, reverse=True)

    selected: List[ChatHotspot] = []
    min_separation_sec = 45.0

    for candidate in candidate_hotspots:
        is_separated = True
        for sel in selected:
            if abs(candidate.timestamp_seconds - sel.timestamp_seconds) < min_separation_sec:
                is_separated = False
                break
        if is_separated:
            selected.append(candidate)
            if len(selected) >= top_n:
                break

    selected.sort(key=lambda x: x.timestamp_seconds)
    return selected

def analyze_chat_explosions(
    chat_file_path: str,
    video_duration_sec: float,
    top_n: int = 6
) -> List[ChatHotspot]:
    timestamps = parse_live_chat_timestamps(chat_file_path)
    if timestamps and len(timestamps) >= 10:
        hotspots = calculate_top_hotspots(timestamps, video_duration_sec, top_n=top_n)
        if hotspots:
            return hotspots

    safe_dur = max(1.0, float(video_duration_sec or 0.0))
    step = safe_dur / (top_n + 1)
    fallback_hotspots = []
    for i in range(1, top_n + 1):
        ts = step * i
        fallback_hotspots.append(ChatHotspot(ts, 15, 5.0))
    return fallback_hotspots

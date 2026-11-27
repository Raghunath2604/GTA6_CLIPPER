"""
Parallel Pipeline Orchestrator for GTA6 Viral Clipper Pro.
Uses ThreadPoolExecutor and thread locks to process streamer channels
or direct YouTube videos concurrently without file collisions or CPU clashes.
"""

import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any, Callable, Optional

from processed_tracker import is_processed, mark_processed
from stream_manager import get_channel_latest_stream_info, download_stream_or_buffer
from chat_analyzer import analyze_chat_explosions
from ai_clipper import analyze_stream_for_viral_clips
from subtitle_generator import generate_subtitles_for_clip
from video_editor import render_viral_clip
from media_packager import package_clip, cleanup_temporary_stream_files

FFMPEG_LOCK = threading.Lock()
STATE_LOCK = threading.Lock()

class ChannelTaskState:
    def __init__(self, channel_url: str):
        self.channel_url = channel_url
        self.channel_name = channel_url.split("/")[-1]
        self.status = "Queued"
        self.progress = 0.0
        self.stream_id: Optional[str] = None
        self.stream_title: Optional[str] = None
        self.clips_generated: int = 0
        self.error: Optional[str] = None
        self.packaged_folders: List[str] = []

    def update(self, status: str, progress: float = 0.0, error: Optional[str] = None):
        self.status = status
        if progress > 0:
            self.progress = progress
        if error:
            self.error = error

def process_single_streamer(
    channel_url: str,
    config: Dict[str, Any],
    state: ChannelTaskState,
    log_callback: Optional[Callable[[str], None]] = None
) -> Dict[str, Any]:
    def log(msg: str):
        print(f"[{state.channel_name}] {msg}")
        if log_callback:
            log_callback(f"[{state.channel_name}] {msg}")

    work_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(work_dir, config.get("output_dir", "Output_Clips"))
    os.makedirs(output_dir, exist_ok=True)

    clip_duration = int(config.get("clip_duration_seconds", 35))

    try:
        state.update("Discovering stream / video...", 10)
        log(f"Extracting info for '{channel_url}'...")
        stream_info = get_channel_latest_stream_info(channel_url)
        
        if not stream_info:
            state.update("No stream found", 100, "Could not find stream or VOD")
            log("No active stream or video found.")
            return {"status": "no_stream", "url": channel_url}

        vid_id = stream_info["video_id"]
        title = stream_info["title"]
        state.stream_id = vid_id
        state.stream_title = title
        state.channel_name = stream_info.get("channel_name", state.channel_name)

        if is_processed(vid_id) and not stream_info.get("is_direct_video", False):
            state.update(f"Already Processed ({vid_id})", 100)
            log(f"Stream '{title}' ({vid_id}) has already been clipped. Skipping.")
            return {"status": "already_processed", "video_id": vid_id}

        if stream_info.get("live_status") == "is_live" and config.get("live_mode") == "skip_active":
            state.update("Active Live Stream (Skipped)", 100)
            log("Stream is currently live. Config is set to skip active broadcasts until finished.")
            return {"status": "skipped_live", "video_id": vid_id}

        state.update("Downloading video (720p)...", 25)
        log(f"Downloading video/stream {vid_id}...")
        video_path, chat_path, duration = download_stream_or_buffer(
            stream_info=stream_info,
            work_dir=work_dir,
            max_duration_minutes=int(config.get("max_capture_minutes", 30)),
            live_mode=config.get("live_mode", "record_buffer")
        )

        if not video_path or not os.path.exists(video_path):
            state.update("Download Failed", 100, "Could not download video")
            log("Video download failed.")
            return {"status": "download_failed", "video_id": vid_id}

        log(f"Downloaded video: {video_path} (Duration: {duration:.1f}s)")

        state.update("Analyzing chat explosion spikes...", 45)
        log("Calculating 10-second chat velocity spikes...")
        hotspots = analyze_chat_explosions(
            chat_file_path=chat_path or "",
            video_duration_sec=duration,
            top_n=6
        )
        log(f"Identified {len(hotspots)} hotspots: {', '.join([h.formatted_time for h in hotspots])}")

        state.update(f"Running Multimodal AI Clipper (>{clip_duration}s)...", 60)
        log(f"Uploading to Gemini 2.5 Flash for {clip_duration}s viral moments extraction...")
        ai_result = analyze_stream_for_viral_clips(
            video_path=video_path,
            hotspots=hotspots,
            video_duration=duration,
            clips_per_stream=int(config.get("clips_per_stream", 2)),
            clip_duration_seconds=clip_duration,
            api_key=config.get("gemini_api_key")
        )

        selected_clips = ai_result.clips
        log(f"AI extracted {len(selected_clips)} viral moments!")

        state.update("Rendering 9:16 vertical clips with subtitles...", 75)
        rendered_folders = []

        for idx, clip in enumerate(selected_clips, 1):
            log(f"Rendering Clip #{idx}: [{clip.hook_banner}] ({clip.start_seconds:.1f}s -> {clip.end_seconds:.1f}s, {clip_duration}s)...")
            
            temp_clip_mp4 = os.path.join(work_dir, f"temp_render_{vid_id}_{idx}.mp4")
            temp_clean_thumb = os.path.join(work_dir, f"temp_clean_thumb_{vid_id}_{idx}.jpg")
            temp_post_thumb = os.path.join(work_dir, f"temp_post_thumb_{vid_id}_{idx}.jpg")
            temp_srt = os.path.join(work_dir, f"temp_subs_{vid_id}_{idx}.srt")

            if config.get("auto_subtitles", True):
                generate_subtitles_for_clip(
                    clip_video_path=video_path,
                    output_srt_path=temp_srt,
                    hook_banner=clip.hook_banner,
                    api_key=config.get("gemini_api_key"),
                    start_sec=clip.start_seconds,
                    duration_sec=float(clip_duration)
                )

            with FFMPEG_LOCK:
                success = render_viral_clip(
                    source_video_path=video_path,
                    start_sec=clip.start_seconds,
                    duration_sec=float(clip_duration),
                    output_clip_path=temp_clip_mp4,
                    hook_banner=clip.hook_banner,
                    clean_thumbnail_path=temp_clean_thumb,
                    post_thumbnail_path=temp_post_thumb,
                    srt_path=temp_srt if os.path.exists(temp_srt) else None,
                    burn_banner=config.get("burn_hook_banner", True),
                    burn_subtitles=config.get("auto_subtitles", True)
                )

            if success:
                packaged_dir = package_clip(
                    output_base_dir=output_dir,
                    vid_id=vid_id,
                    clip_index=idx,
                    clip_info=clip.model_dump(),
                    rendered_mp4_path=temp_clip_mp4,
                    clean_thumb_path=temp_clean_thumb,
                    post_thumb_path=temp_post_thumb,
                    srt_path=temp_srt,
                    stream_title=title,
                    channel_name=state.channel_name
                )
                rendered_folders.append(packaged_dir)
                state.clips_generated += 1

            cleanup_temporary_stream_files([temp_clip_mp4, temp_clean_thumb, temp_post_thumb, temp_srt])

        if config.get("auto_cleanup", True):
            log("Auto-cleaning raw download files...")
            cleanup_temporary_stream_files([video_path, chat_path])

        mark_processed(vid_id, title)
        state.packaged_folders = rendered_folders
        state.update("Completed ✅", 100)
        log(f"All {len(rendered_folders)} clips packaged successfully!")

        return {
            "status": "success",
            "video_id": vid_id,
            "clips_count": len(rendered_folders),
            "folders": rendered_folders
        }

    except Exception as e:
        err_msg = str(e)
        state.update("Error", 100, err_msg)
        log(f"Error: {err_msg}")
        return {"status": "error", "error": err_msg}

def run_parallel_clipper(
    channel_urls: List[str],
    config: Dict[str, Any],
    log_callback: Optional[Callable[[str], None]] = None,
    state_callback: Optional[Callable[[Dict[str, ChannelTaskState]], None]] = None
) -> Dict[str, Any]:
    max_workers = int(config.get("parallel_workers", 3))
    states: Dict[str, ChannelTaskState] = {url: ChannelTaskState(url) for url in channel_urls if url.strip()}

    if not states:
        return {"status": "empty", "message": "No URLs provided."}

    if state_callback:
        state_callback(states)

    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_url = {
            executor.submit(process_single_streamer, url, config, states[url], log_callback): url
            for url in states
        }

        for future in as_completed(future_to_url):
            url = future_to_url[future]
            try:
                res = future.result()
                results[url] = res
            except Exception as exc:
                results[url] = {"status": "exception", "error": str(exc)}
            
            if state_callback:
                state_callback(states)

    return {"status": "completed", "results": results}

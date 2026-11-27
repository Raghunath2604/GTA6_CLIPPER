"""
Parallel Pipeline Orchestrator for GTA6 Viral Clipper Pro.
Uses ThreadPoolExecutor and isolated per-run temp directories to process
streamer channels or direct YouTube videos concurrently without file collisions.
"""

import os
import time
import shutil
import tempfile
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
from logger import get_logger

log = get_logger("pipeline")
FFMPEG_LOCK = threading.Lock()


def cleanup_stale_temp_dirs(max_age_sec: int = 7200) -> int:
    """Remove orphaned per-run temp directories older than max_age_sec."""
    root_tmp = os.path.join(tempfile.gettempdir(), "gta6_clipper_tmp")
    if not os.path.exists(root_tmp):
        return 0
    removed = 0
    now = time.time()
    for name in os.listdir(root_tmp):
        full = os.path.join(root_tmp, name)
        if not os.path.isdir(full):
            continue
        try:
            if (now - os.path.getmtime(full)) >= max_age_sec:
                shutil.rmtree(full, ignore_errors=True)
                removed += 1
        except Exception:
            continue
    return removed


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
    def emit(msg: str):
        log.info(f"[{state.channel_name}] {msg}")
        if log_callback:
            log_callback(f"[{state.channel_name}] {msg}")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    raw_output_dir = config.get("output_dir", "Output_Clips")
    output_dir = raw_output_dir if os.path.isabs(raw_output_dir) else os.path.join(base_dir, raw_output_dir)
    os.makedirs(output_dir, exist_ok=True)

    run_tmp_dir = os.path.join(tempfile.gettempdir(), "gta6_clipper_tmp", f"{int(time.time())}_{threading.get_ident()}")
    os.makedirs(run_tmp_dir, exist_ok=True)

    clip_duration = max(30, int(config.get("clip_duration_seconds", 35)))
    auto_cleanup = bool(config.get("auto_cleanup", True))

    try:
        state.update("Discovering stream / video...", 10)
        emit(f"Extracting info for '{channel_url}'...")
        stream_info = get_channel_latest_stream_info(channel_url)

        if not stream_info:
            state.update("No stream found", 100, "Could not find stream or VOD")
            emit("No active stream or video found.")
            return {"status": "no_stream", "url": channel_url}

        vid_id = stream_info["video_id"]
        title = stream_info["title"]
        state.stream_id = vid_id
        state.stream_title = title
        state.channel_name = stream_info.get("channel_name", state.channel_name)

        if is_processed(vid_id) and not stream_info.get("is_direct_video", False):
            state.update(f"Already Processed ({vid_id})", 100)
            emit(f"Stream '{title}' ({vid_id}) already clipped. Skipping.")
            return {"status": "already_processed", "video_id": vid_id}

        if stream_info.get("live_status") == "is_live" and config.get("live_mode") == "skip_active":
            state.update("Active Live Stream (Skipped)", 100)
            emit("Stream is actively live and live_mode is skip_active.")
            return {"status": "skipped_live", "video_id": vid_id}

        state.update("Downloading video (720p)...", 25)
        emit(f"Downloading video/stream {vid_id}...")
        video_path, chat_path, duration = download_stream_or_buffer(
            stream_info=stream_info,
            work_dir=run_tmp_dir,
            max_duration_minutes=int(config.get("max_capture_minutes", 30)),
            live_mode=config.get("live_mode", "record_buffer")
        )

        if not video_path or not os.path.exists(video_path):
            state.update("Download Failed", 100, "Could not download video")
            emit("Video download failed.")
            return {"status": "download_failed", "video_id": vid_id}

        emit(f"Downloaded video: {os.path.basename(video_path)} (Duration: {duration:.1f}s)")

        state.update("Analyzing chat explosion spikes...", 45)
        emit("Calculating 10-second chat velocity spikes...")
        hotspots = analyze_chat_explosions(
            chat_file_path=chat_path or "",
            video_duration_sec=duration,
            top_n=6
        )
        emit(f"Identified {len(hotspots)} hotspots: {', '.join([h.formatted_time for h in hotspots])}")

        state.update(f"Running Multimodal AI Clipper (>{clip_duration}s)...", 60)
        emit(f"Uploading to Gemini AI for {clip_duration}s viral moments extraction...")
        ai_result = analyze_stream_for_viral_clips(
            video_path=video_path,
            hotspots=hotspots,
            video_duration=duration,
            clips_per_stream=int(config.get("clips_per_stream", 2)),
            clip_duration_seconds=clip_duration,
            api_key=config.get("gemini_api_key")
        )

        selected_clips = ai_result.clips
        emit(f"AI extracted {len(selected_clips)} viral moments!")

        state.update("Rendering 9:16 vertical clips with subtitles...", 75)
        rendered_folders = []

        for idx, clip in enumerate(selected_clips, 1):
            render_dur = max(1.0, round(clip.end_seconds - clip.start_seconds, 1))
            emit(f"Rendering Clip #{idx}: [{clip.hook_banner}] ({clip.start_seconds:.1f}s -> {clip.end_seconds:.1f}s, {render_dur:.1f}s)...")

            temp_clip_mp4 = os.path.join(run_tmp_dir, f"temp_render_{vid_id}_{idx}.mp4")
            temp_clean_thumb = os.path.join(run_tmp_dir, f"temp_clean_thumb_{vid_id}_{idx}.jpg")
            temp_post_thumb = os.path.join(run_tmp_dir, f"temp_post_thumb_{vid_id}_{idx}.jpg")
            temp_srt = os.path.join(run_tmp_dir, f"temp_subs_{vid_id}_{idx}.srt")

            if config.get("auto_subtitles", True):
                generate_subtitles_for_clip(
                    clip_video_path=video_path,
                    output_srt_path=temp_srt,
                    hook_banner=clip.hook_banner,
                    api_key=config.get("gemini_api_key"),
                    start_sec=clip.start_seconds,
                    duration_sec=render_dur
                )

            with FFMPEG_LOCK:
                success = render_viral_clip(
                    source_video_path=video_path,
                    start_sec=clip.start_seconds,
                    duration_sec=render_dur,
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

        mark_processed(vid_id, title)
        state.packaged_folders = rendered_folders
        state.update("Completed ✅", 100)
        emit(f"All {len(rendered_folders)} clips packaged successfully!")

        return {
            "status": "success",
            "video_id": vid_id,
            "clips_count": len(rendered_folders),
            "folders": rendered_folders
        }

    except Exception as e:
        err_msg = str(e)
        log.exception(f"Pipeline failure for {channel_url}: {err_msg}")
        state.update("Error", 100, err_msg)
        emit(f"Error: {err_msg}")
        return {"status": "error", "error": err_msg}

    finally:
        if auto_cleanup and os.path.exists(run_tmp_dir):
            shutil.rmtree(run_tmp_dir, ignore_errors=True)


def run_parallel_clipper(
    channel_urls: List[str],
    config: Dict[str, Any],
    log_callback: Optional[Callable[[str], None]] = None,
    state_callback: Optional[Callable[[Dict[str, ChannelTaskState]], None]] = None
) -> Dict[str, Any]:
    max_workers = max(1, int(config.get("parallel_workers", 3)))
    unique_urls = list(dict.fromkeys(u.strip() for u in channel_urls if u and u.strip()))
    states: Dict[str, ChannelTaskState] = {url: ChannelTaskState(url) for url in unique_urls}

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
                results[url] = future.result()
            except Exception as exc:
                log.exception(f"Worker unhandled exception for {url}")
                results[url] = {"status": "exception", "error": str(exc)}

            if state_callback:
                state_callback(states)

    return {"status": "completed", "results": results}

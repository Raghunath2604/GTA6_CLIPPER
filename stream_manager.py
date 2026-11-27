"""
Parallel Stream Ingestion and yt-dlp Manager for GTA6 Viral Clipper Pro.
Handles:
1. Querying both streamer channel links (/streams, /live) and direct YouTube video/stream links.
2. Checking deduplication via processed_tracker.
3. Downloading isolated 720p MP4 (temp_stream_{vid_id}.mp4) + .live_chat.json.
4. Timed live-stream buffer recording using FFmpeg.
"""

import os
import time
import shutil
from typing import Dict, Any, Optional, Tuple
import yt_dlp
from yt_dlp.utils import download_range_func

from ffmpeg_utils import run_ffmpeg, get_ffmpeg_path, get_media_duration
from logger import get_logger

log = get_logger("stream_manager")

# Ensure FFmpeg bin directory is in PATH for yt-dlp and sub-processes
_ffmpeg_bin_dir = os.path.dirname(get_ffmpeg_path())
if _ffmpeg_bin_dir and os.path.exists(_ffmpeg_bin_dir):
    if _ffmpeg_bin_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_bin_dir + os.pathsep + os.environ.get("PATH", "")


def _detect_js_runtimes() -> Dict[str, Dict[str, str]]:
    """Detect available JS runtime (deno/node) so yt-dlp solves YouTube EJS challenges."""
    runtimes: Dict[str, Dict[str, str]] = {}
    deno = shutil.which("deno")
    if deno:
        runtimes["deno"] = {"path": deno}
    node = shutil.which("node")
    if node:
        runtimes["node"] = {"path": node}
    return runtimes


def _base_ydl_opts(**extra: Any) -> Dict[str, Any]:
    opts: Dict[str, Any] = {
        "ffmpeg_location": _ffmpeg_bin_dir,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 20,
    }
    js_rt = _detect_js_runtimes()
    if js_rt:
        opts["js_runtimes"] = js_rt
    opts.update(extra)
    return opts

def is_direct_video_url(url: str) -> bool:
    """Checks if a URL points directly to a single video/stream rather than a channel."""
    u = url.strip().lower()
    return any(p in u for p in ["watch?v=", "youtu.be/", "/live/", "/shorts/"])

def get_channel_latest_stream_info(input_url: str) -> Optional[Dict[str, Any]]:
    clean_url = input_url.strip()
    if not clean_url:
        return None

    # Case A: Direct YouTube Video / Live Stream Link
    if is_direct_video_url(clean_url):
        ydl_opts = _base_ydl_opts(ignoreerrors=True)
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(clean_url, download=False)
                if not info:
                    return None
                vid_id = info.get("id")
                title = info.get("title", "YouTube Video")
                live_status = info.get("live_status", "not_live")
                channel_name = info.get("channel") or info.get("uploader", "Creator")
                return {
                    "video_id": vid_id,
                    "title": title,
                    "url": info.get("webpage_url", clean_url),
                    "live_status": live_status,
                    "channel_name": channel_name,
                    "channel_url": clean_url,
                    "is_direct_video": True
                }
        except Exception as e:
            log.warning(f"Error extracting direct video {clean_url}: {e}")
            return None

    # Case B: Channel Streams or Videos Tabs
    candidate_urls = [clean_url]
    if "youtube.com" in clean_url and not any(clean_url.endswith(x) for x in ["/streams", "/live", "/videos"]):
        if "/@" in clean_url:
            candidate_urls = [
                clean_url.rstrip("/") + "/streams",
                clean_url.rstrip("/") + "/videos",
                clean_url.rstrip("/")
            ]

    ydl_opts = _base_ydl_opts(extract_flat=True, playlistend=3, ignoreerrors=True)

    for channel_url in candidate_urls:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(channel_url, download=False)
                if not info:
                    continue

                entries = info.get("entries")
                channel_name = info.get("channel") or info.get("uploader") or info.get("title", "Streamer")

                if entries and len(entries) > 0:
                    for entry in entries:
                        if not entry:
                            continue
                        vid_id = entry.get("id")
                        title = entry.get("title", "Untitled Stream")
                        live_status = entry.get("live_status", "not_live")
                        watch_url = f"https://www.youtube.com/watch?v={vid_id}" if vid_id else entry.get("url")

                        return {
                            "video_id": vid_id,
                            "title": title,
                            "url": watch_url,
                            "live_status": live_status,
                            "channel_name": channel_name,
                            "channel_url": channel_url,
                            "is_direct_video": False
                        }
                elif "id" in info:
                    return {
                        "video_id": info.get("id"),
                        "title": info.get("title", "Stream Video"),
                        "url": info.get("webpage_url", channel_url),
                        "live_status": info.get("live_status", "not_live"),
                        "channel_name": info.get("channel") or info.get("uploader", "Streamer"),
                        "channel_url": channel_url,
                        "is_direct_video": True
                    }
        except Exception:
            continue

    return None

def _hls_http_input_args(url: str) -> list:
    """Return FFmpeg input args with HTTP/HLS reconnect flags when url is HTTP(S)."""
    if str(url).lower().startswith(("http://", "https://")):
        return ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", url]
    return ["-i", url]


def build_live_hls_ffmpeg_args(
    video_m3u8: Optional[str],
    audio_m3u8: Optional[str],
    buffer_sec: int,
    video_output_path: str,
) -> Optional[list]:
    """Build resilient FFmpeg capture args for a live HLS stream (video-only or muxed audio)."""
    if not video_m3u8:
        return None
    args = ["-y", *_hls_http_input_args(video_m3u8)]
    if audio_m3u8:
        args.extend([
            *_hls_http_input_args(audio_m3u8),
            "-t", str(buffer_sec),
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            "-movflags", "+faststart",
            video_output_path,
        ])
    else:
        args.extend([
            "-t", str(buffer_sec),
            "-c", "copy",
            "-movflags", "+faststart",
            video_output_path,
        ])
    return args


def download_stream_or_buffer(
    stream_info: Dict[str, Any],
    work_dir: str,
    max_duration_minutes: int = 30,
    live_mode: str = "record_buffer"
) -> Tuple[Optional[str], Optional[str], float]:
    vid_id = stream_info["video_id"]
    watch_url = stream_info["url"]
    live_status = stream_info.get("live_status", "")
    is_live = (live_status == "is_live")

    video_output_path = os.path.join(work_dir, f"temp_stream_{vid_id}.mp4")
    chat_output_path = os.path.join(work_dir, f"temp_stream_{vid_id}.live_chat.json")

    if os.path.exists(video_output_path) and os.path.getsize(video_output_path) > 1000000:
        from ffmpeg_utils import get_media_duration
        dur = get_media_duration(video_output_path)
        return video_output_path, (chat_output_path if os.path.exists(chat_output_path) else None), dur

    if is_live:
        if live_mode == "skip_active":
            log.info(f"Stream {vid_id} is actively live. Config set to 'skip_active'. Skipping.")
            return None, None, 0.0

        buffer_sec = max(60, max_duration_minutes * 60)
        log.info(f"Stream {vid_id} is live; recording {max_duration_minutes}m ({buffer_sec}s) buffer...")
        
        ydl_opts = _base_ydl_opts(format="bestvideo[height<=720]+bestaudio/best[height<=720]/best")
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(watch_url, download=False) or {}

            req_formats = info.get("requested_formats") or []
            video_m3u8 = None
            audio_m3u8 = None

            if len(req_formats) >= 2:
                video_m3u8 = req_formats[0].get("url")
                audio_m3u8 = req_formats[1].get("url")
            elif info.get("url") and info.get("acodec", "none") != "none" and info.get("vcodec", "none") != "none":
                video_m3u8 = info["url"]
            else:
                formats = info.get("formats", [])
                muxed = [
                    f for f in formats
                    if f.get("url") and f.get("vcodec", "none") != "none" and f.get("acodec", "none") != "none"
                    and (f.get("height") or 0) <= 720
                ]
                if muxed:
                    video_m3u8 = muxed[-1]["url"]
                else:
                    vid_only = [
                        f for f in formats
                        if f.get("url") and f.get("vcodec", "none") != "none" and (f.get("height") or 0) <= 720
                    ] or [f for f in formats if f.get("url") and f.get("vcodec", "none") != "none"]
                    aud_only = [
                        f for f in formats
                        if f.get("url") and f.get("acodec", "none") != "none" and f.get("vcodec", "none") == "none"
                    ] or [f for f in formats if f.get("url") and f.get("acodec", "none") != "none"]
                    if vid_only:
                        video_m3u8 = vid_only[-1]["url"]
                    if aud_only:
                        audio_m3u8 = aud_only[-1]["url"]

            ffmpeg_args = build_live_hls_ffmpeg_args(
                video_m3u8=video_m3u8,
                audio_m3u8=audio_m3u8,
                buffer_sec=buffer_sec,
                video_output_path=video_output_path,
            )

            if ffmpeg_args:
                log.info(f"Capturing live stream buffer via FFmpeg ({buffer_sec}s, muxed_audio={bool(audio_m3u8)})...")
                res = run_ffmpeg(ffmpeg_args, timeout=buffer_sec + 60)
                if res.returncode == 0 and os.path.exists(video_output_path):
                    dur = get_media_duration(video_output_path)
                    log.info(f"Captured live buffer ({dur:.1f}s).")
                    return video_output_path, None, dur
        except Exception as e:
            log.warning(f"Error during live buffer recording: {e}")

    max_sec = max(60, max_duration_minutes * 60)
    log.info(f"Downloading 720p VOD window (up to {max_duration_minutes}m) for stream {vid_id}...")

    ydl_opts = _base_ydl_opts(
        format="bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best",
        outtmpl=os.path.join(work_dir, f"temp_stream_{vid_id}.%(ext)s"),
        merge_output_format="mp4",
        download_ranges=download_range_func(None, [(0, max_sec)]),
        force_keyframes_at_cuts=True,
        writesubtitles=True,
        writeautomaticsub=False,
        subtitleslangs=["live_chat"],
        quiet=False,
    )

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([watch_url])
    except Exception as e:
        log.warning(f"Section download notice ({e}); checking if partial or fallback download needed...")
        if not any(os.path.exists(os.path.join(work_dir, f"temp_stream_{vid_id}.{ext}")) for ext in ["mp4", "mkv", "webm"]):
            fallback_opts = dict(ydl_opts)
            fallback_opts.pop("download_ranges", None)
            fallback_opts.pop("force_keyframes_at_cuts", None)
            try:
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    ydl.download([watch_url])
            except Exception as fb_err:
                log.warning(f"Fallback download notice: {fb_err}")

    actual_video_path = None
    for ext in ["mp4", "mkv", "webm"]:
        candidate = os.path.join(work_dir, f"temp_stream_{vid_id}.{ext}")
        if os.path.exists(candidate) and os.path.getsize(candidate) > 100000:
            if ext != "mp4":
                remux_args = ["-y", "-i", candidate, "-c", "copy", video_output_path]
                run_ffmpeg(remux_args, timeout=120)
                if os.path.exists(video_output_path):
                    actual_video_path = video_output_path
            else:
                actual_video_path = candidate
            break

    from ffmpeg_utils import get_media_duration
    if actual_video_path and os.path.exists(actual_video_path):
        dur = get_media_duration(actual_video_path)
        # If downloaded raw file exceeds max_duration_minutes, trim head with ultrafast stream copy
        if dur > max_sec + 10:
            trimmed_path = os.path.join(work_dir, f"temp_stream_{vid_id}_trimmed.mp4")
            trim_cmd = ["-y", "-i", actual_video_path, "-t", str(max_sec), "-c", "copy", trimmed_path]
            res_trim = run_ffmpeg(trim_cmd, timeout=60)
            if res_trim.returncode == 0 and os.path.exists(trimmed_path):
                os.replace(trimmed_path, actual_video_path)
                dur = float(max_sec)

    actual_chat_path = None
    for candidate_name in [
        f"temp_stream_{vid_id}.live_chat.json",
        f"temp_stream_{vid_id}.en.live_chat.json",
    ]:
        p = os.path.join(work_dir, candidate_name)
        if os.path.exists(p) and os.path.getsize(p) > 0:
            actual_chat_path = p
            break

    dur = get_media_duration(actual_video_path) if actual_video_path else 0.0
    return actual_video_path, actual_chat_path, dur

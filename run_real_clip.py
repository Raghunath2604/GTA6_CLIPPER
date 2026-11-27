"""
CLI runner for GTA6 Viral Clipper Pro.
Executes the full pipeline on a YouTube URL or Streamer channel, with optional
clip count, duration, and 1-click YouTube Shorts upload flags.
"""

import argparse
import glob
import json
import os
from config import load_config
from pipeline import process_single_streamer, ChannelTaskState


def main():
    parser = argparse.ArgumentParser(description="GTA6 Viral Clipper Pro - CLI Pipeline Runner")
    parser.add_argument(
        "url",
        nargs="?",
        default="https://youtu.be/wEGdTkRL-uI?si=ZdQkAe9QdGajgsQM",
        help="YouTube video, live stream, or channel URL",
    )
    parser.add_argument("--clips", type=int, help="Number of viral clips to generate (1-5)")
    parser.add_argument("--duration", type=int, help="Target clip duration in seconds (>=30)")
    parser.add_argument("--max-minutes", type=int, help="Max source capture window in minutes")
    parser.add_argument("--no-subs", action="store_true", help="Disable auto-subtitles")
    parser.add_argument("--no-banner", action="store_true", help="Disable top hook banner")
    parser.add_argument("--upload-yt", action="store_true", help="Upload generated clips to YouTube Shorts")
    parser.add_argument(
        "--privacy",
        choices=["private", "unlisted", "public"],
        default="private",
        help="Privacy status for --upload-yt (default: private)",
    )
    args = parser.parse_args()

    config = load_config()
    if args.clips is not None:
        config["clips_per_stream"] = max(1, min(5, args.clips))
    if args.duration is not None:
        config["clip_duration_seconds"] = max(30, min(60, args.duration))
    if args.max_minutes is not None:
        config["max_capture_minutes"] = max(1, args.max_minutes)
    if args.no_subs:
        config["auto_subtitles"] = False
    if args.no_banner:
        config["burn_hook_banner"] = False

    print(f"Loaded config. Gemini API Key present: {bool(config.get('gemini_api_key'))}")
    print(f"Starting real clipping for: {args.url}...")

    state = ChannelTaskState(args.url)
    def log_cb(msg):
        print(f">> {msg}")

    result = process_single_streamer(args.url, config, state, log_cb)
    print(f"Process result: {result}")

    if args.upload_yt and result.get("status") == "success":
        from youtube_uploader import upload_short_to_youtube
        for folder in result.get("folders", []):
            meta_path = os.path.join(folder, "metadata.json")
            mp4s = glob.glob(os.path.join(folder, "*.mp4"))
            thumbs = [p for p in glob.glob(os.path.join(folder, "*.jpg")) if not p.endswith("_clean.jpg")]
            if not mp4s:
                continue
            meta = {}
            if os.path.exists(meta_path):
                with open(meta_path, encoding="utf-8") as mf:
                    meta = json.load(mf)
            up_res = upload_short_to_youtube(
                video_path=mp4s[0],
                title=meta.get("title", "GTA6 Highlight #Shorts"),
                description=meta.get("description", ""),
                tags=meta.get("tags"),
                privacy_status=args.privacy,
                thumbnail_path=thumbs[0] if thumbs else None,
            )
            meta["youtube_upload"] = up_res
            with open(meta_path, "w", encoding="utf-8") as mf:
                json.dump(meta, mf, indent=2)
            print(f"Uploaded Short: {up_res['url']}")


if __name__ == "__main__":
    main()

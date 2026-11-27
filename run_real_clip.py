"""
CLI runner for GTA6 Viral Clipper Pro.
Executes the full pipeline on a YouTube URL or Streamer channel.
"""

import sys
from config import load_config
from pipeline import process_single_streamer, ChannelTaskState

def main():
    target_url = sys.argv[1] if len(sys.argv) > 1 else "https://youtu.be/wEGdTkRL-uI?si=ZdQkAe9QdGajgsQM"
    config = load_config()
    print(f"Loaded config. Gemini API Key present: {bool(config.get('gemini_api_key'))}")
    print(f"Starting real clipping for: {target_url}...")

    state = ChannelTaskState(target_url)
    def log_cb(msg):
        print(f">> {msg}")

    result = process_single_streamer(target_url, config, state, log_cb)
    print(f"Process result: {result}")

if __name__ == "__main__":
    main()

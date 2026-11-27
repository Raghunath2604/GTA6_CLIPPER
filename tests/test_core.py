import os
import tempfile
import unittest

import config
import processed_tracker
from ai_clipper import ViralClip, sanitize_clips
from chat_analyzer import analyze_chat_explosions, calculate_top_hotspots
from ffmpeg_utils import check_ffmpeg_health
from media_packager import sanitize_filename, package_clip
from stream_manager import is_direct_video_url
from subtitle_generator import (
    _format_srt_timestamp,
    split_segment_proportionally,
    split_words_into_short_cues,
)
from video_editor import _srt_ts_to_ass, _convert_srt_to_vertical_ass
from youtube_uploader import sanitize_youtube_tags


class CorePipelineTests(unittest.TestCase):
    def test_srt_timestamp_formatting_and_rollover(self):
        self.assertEqual(_format_srt_timestamp(0.0), "00:00:00,000")
        self.assertEqual(_format_srt_timestamp(65.25), "00:01:05,250")
        self.assertEqual(_format_srt_timestamp(1.9999), "00:00:02,000")

    def test_ass_conversion_uses_1080x1920_playres(self):
        self.assertEqual(_srt_ts_to_ass("00:01:05,250"), "0:01:05.25")
        with tempfile.TemporaryDirectory() as td:
            srt = os.path.join(td, "in.srt")
            ass = os.path.join(td, "out.ass")
            with open(srt, "w", encoding="utf-8") as f:
                f.write("1\n00:00:01,000 --> 00:00:03,500\nhello chat\n\n")
            _convert_srt_to_vertical_ass(srt, ass)
            with open(ass, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("PlayResX: 1080", content)
            self.assertIn("PlayResY: 1920", content)
            self.assertIn("Dialogue: 0,0:00:01.00,0:00:03.50,ViralYellow,,0,0,0,,HELLO CHAT", content)

    def test_direct_video_url_detection(self):
        self.assertTrue(is_direct_video_url("https://www.youtube.com/watch?v=qSyo5d-dVzA"))
        self.assertTrue(is_direct_video_url("https://youtu.be/wEGdTkRL-uI"))
        self.assertTrue(is_direct_video_url("https://www.youtube.com/live/abc123"))
        self.assertFalse(is_direct_video_url("https://www.youtube.com/@IShowSpeed/streams"))

    def test_chat_hotspots_spacing_and_fallback(self):
        fallback = analyze_chat_explosions("nonexistent_chat.json", 120.0, top_n=3)
        self.assertEqual(len(fallback), 3)
        timestamps = [15.0] * 15 + [16.0] * 10 + [85.0] * 20
        hotspots = calculate_top_hotspots(timestamps, 120.0, top_n=3)
        self.assertEqual(len(hotspots), 2)
        self.assertGreaterEqual(abs(hotspots[1].timestamp_seconds - hotspots[0].timestamp_seconds), 45.0)

    def test_media_packager_outputs_all_sidecars(self):
        self.assertEqual(sanitize_filename("GTA 6: Crazy Clip!! #Shorts"), "GTA_6_Crazy_Clip_Shorts")
        with tempfile.TemporaryDirectory() as td:
            mp4 = os.path.join(td, "clip.mp4")
            t_clean = os.path.join(td, "clean.jpg")
            t_post = os.path.join(td, "post.jpg")
            srt = os.path.join(td, "subs.srt")
            for p in (mp4, t_clean, t_post, srt):
                with open(p, "wb") as f:
                    f.write(b"test")
            out_dir = package_clip(
                td, "vid123", 1,
                {"title": "Test Title #Shorts", "hook_banner": "INSANE MOMENT", "caption": "Cap", "description": "Desc"},
                mp4, t_clean, t_post, srt
            )
            self.assertTrue(os.path.exists(os.path.join(out_dir, "clip_1.mp4")))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "thumbnail_1_clean.jpg")))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "thumbnail_1.jpg")))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "metadata.json")))
            self.assertTrue(os.path.exists(os.path.join(out_dir, "SEO_metadata.txt")))

    def test_default_streamers_configured(self):
        self.assertIn("IShowSpeed", config.DEFAULT_CONFIG["streamer_urls"])

    def test_empty_gemini_key_in_config_json_does_not_clobber_env(self):
        with tempfile.TemporaryDirectory() as td:
            fake_cfg = os.path.join(td, "config.json")
            with open(fake_cfg, "w", encoding="utf-8") as f:
                f.write('{"gemini_api_key": ""}')
            orig_file = config.CONFIG_FILE
            orig_default = dict(config.DEFAULT_CONFIG)
            try:
                config.CONFIG_FILE = fake_cfg
                config.DEFAULT_CONFIG["gemini_api_key"] = "TEST_ENV_KEY"
                loaded = config.load_config()
                self.assertEqual(loaded["gemini_api_key"], "TEST_ENV_KEY")
            finally:
                config.CONFIG_FILE = orig_file
                config.DEFAULT_CONFIG.update(orig_default)

    def test_sanitize_clips_clamps_bounds_and_drops_overlaps(self):
        raw = [
            ViralClip(start_seconds=-5.0, end_seconds=20.0, viral_score=9, trigger_type="CHAT", hook_banner="one two three four five six seven", title="T1", caption="C1", description="D1"),
            ViralClip(start_seconds=3.0, end_seconds=38.0, viral_score=8, trigger_type="CHAT", hook_banner="overlap clip", title="T2", caption="C2", description="D2"),
            ViralClip(start_seconds=90.0, end_seconds=125.0, viral_score=7, trigger_type="CHAT", hook_banner="valid second clip", title="T3", caption="C3", description="D3"),
        ]
        cleaned = sanitize_clips(raw, video_duration=100.0, target_duration=35.0, max_clips=3)
        self.assertEqual(len(cleaned), 2)
        self.assertEqual(cleaned[0].start_seconds, 0.0)
        self.assertEqual(cleaned[0].end_seconds, 35.0)
        self.assertEqual(len(cleaned[0].hook_banner.split()), 6)
        self.assertEqual(cleaned[1].start_seconds, 65.0)
        self.assertEqual(cleaned[1].end_seconds, 100.0)

    def test_subtitle_word_and_segment_chunking(self):
        words = [(i * 0.4, (i + 1) * 0.4, w) for i, w in enumerate(["this", "is", "insane", "chat", "look", "at", "that", "car"])]
        cues = split_words_into_short_cues(words, max_words=5, max_cue_sec=2.4)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0][3], "THIS IS INSANE CHAT LOOK")
        self.assertEqual(cues[1][3], "AT THAT CAR")

        segs = split_segment_proportionally(0.0, 6.0, "one two three four five six seven", max_words=4)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0][2], "ONE TWO THREE FOUR")

    def test_sanitize_youtube_tags_budget_and_dedupe(self):
        tags = ["#GTA6", "gta6", "<bad>", "x" * 40] + [f"customtag_{i}" for i in range(40)]
        cleaned = sanitize_youtube_tags(tags, max_total_chars=120)
        self.assertIn("GTA6", cleaned)
        self.assertEqual(len([t for t in cleaned if t.lower() == "gta6"]), 1)
        self.assertTrue(all(len(t) <= 30 and "<" not in t and "#" not in t for t in cleaned))

    def test_processed_tracker_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            orig_tracker = processed_tracker.TRACKER_FILE
            try:
                processed_tracker.TRACKER_FILE = os.path.join(td, "processed.txt")
                processed_tracker.mark_processed("vidA", "Stream One")
                processed_tracker.mark_processed("vidA", "Stream One Duplicate")
                with open(processed_tracker.TRACKER_FILE, encoding="utf-8") as f:
                    lines = [ln for ln in f.read().splitlines() if ln.strip()]
                self.assertEqual(len(lines), 1)
                self.assertTrue(processed_tracker.is_processed("vidA"))
            finally:
                processed_tracker.TRACKER_FILE = orig_tracker

    def test_ffmpeg_health_shape(self):
        h = check_ffmpeg_health()
        self.assertIn("ready", h)
        self.assertIn("ffmpeg_path", h)


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest

import config
from chat_analyzer import analyze_chat_explosions, calculate_top_hotspots
from media_packager import sanitize_filename, package_clip
from stream_manager import is_direct_video_url
from subtitle_generator import _format_srt_timestamp
from video_editor import _srt_ts_to_ass, _convert_srt_to_vertical_ass


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


if __name__ == "__main__":
    unittest.main()

import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from googleapiclient.errors import HttpError

import config
import canva_connector
import pipeline
import processed_tracker
import youtube_uploader
from ai_clipper import ViralClip, sanitize_clips, verify_gemini_api_key
from chat_analyzer import analyze_chat_explosions, calculate_top_hotspots
from ffmpeg_utils import check_ffmpeg_health
from media_packager import (
    sanitize_filename,
    package_clip,
    extract_tags_from_clip_info,
    format_seo_metadata_text,
)
from stream_manager import is_direct_video_url, build_live_hls_ffmpeg_args
from subtitle_generator import (
    _format_srt_timestamp,
    split_segment_proportionally,
    split_words_into_short_cues,
)
from video_editor import _srt_ts_to_ass, _convert_srt_to_vertical_ass
from youtube_uploader import sanitize_youtube_tags, _parse_youtube_http_error


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

    def test_package_clip_extracts_hashtags_when_tags_missing(self):
        with tempfile.TemporaryDirectory() as td:
            mp4 = os.path.join(td, "src.mp4")
            clean = os.path.join(td, "clean.jpg")
            post = os.path.join(td, "post.jpg")
            srt = os.path.join(td, "sub.srt")
            for p in (mp4, clean, post, srt):
                with open(p, "wb") as f:
                    f.write(b"x")
            folder = package_clip(
                output_base_dir=td,
                vid_id="vid123",
                clip_index=1,
                clip_info={
                    "title": "Vice City Chaos",
                    "hook_banner": "INSANE POLICE CHASE",
                    "caption": "Watch this #GTA6 #ViceCity #gta6 moment!",
                    "description": "Best highlight #RockstarGames",
                    "trigger_type": "CLUTCH",
                },
                rendered_mp4_path=mp4,
                clean_thumb_path=clean,
                post_thumb_path=post,
                srt_path=srt,
            )
            with open(os.path.join(folder, "metadata.json"), encoding="utf-8") as mf:
                meta = json.load(mf)
            self.assertEqual(meta["tags"][:3], ["GTA6", "ViceCity", "RockstarGames"])
            self.assertIn("CLUTCH", meta["tags"])
            self.assertEqual(len([t for t in meta["tags"] if t.lower() == "gta6"]), 1)
            with open(os.path.join(folder, "SEO_metadata.txt"), encoding="utf-8") as sf:
                self.assertIn("GTA6, ViceCity, RockstarGames", sf.read())

    def test_live_hls_ffmpeg_args_include_reconnect_flags(self):
        dual = build_live_hls_ffmpeg_args(
            "https://example.com/video.m3u8",
            "https://example.com/audio.m3u8",
            120,
            "out.mp4",
        )
        self.assertIsNotNone(dual)
        self.assertEqual(dual.count("-reconnect"), 2)
        v_idx = dual.index("https://example.com/video.m3u8")
        self.assertEqual(dual[v_idx - 7:v_idx + 1], ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", "https://example.com/video.m3u8"])
        a_idx = dual.index("https://example.com/audio.m3u8")
        self.assertEqual(dual[a_idx - 7:a_idx + 1], ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5", "-i", "https://example.com/audio.m3u8"])

        single = build_live_hls_ffmpeg_args("https://example.com/muxed.m3u8", None, 90, "out.mp4")
        self.assertEqual(single.count("-reconnect"), 1)
        self.assertIn("copy", single)

        local = build_live_hls_ffmpeg_args("local.ts", None, 90, "out.mp4")
        self.assertNotIn("-reconnect", local)
        self.assertIsNone(build_live_hls_ffmpeg_args(None, None, 90, "out.mp4"))

    def test_youtube_non_interactive_auth_raises_fast(self):
        with tempfile.TemporaryDirectory() as td:
            secret = os.path.join(td, "client_secret.json")
            with open(secret, "w", encoding="utf-8") as f:
                f.write("{}")
            orig_token = youtube_uploader.TOKEN_FILE
            orig_finder = youtube_uploader.find_client_secret_file
            try:
                youtube_uploader.TOKEN_FILE = os.path.join(td, "missing_token.json")
                youtube_uploader.find_client_secret_file = lambda: secret
                with self.assertRaises(RuntimeError) as ctx:
                    youtube_uploader.get_authenticated_service(interactive=False)
                self.assertIn("Login_CLI.bat", str(ctx.exception))
            finally:
                youtube_uploader.TOKEN_FILE = orig_token
                youtube_uploader.find_client_secret_file = orig_finder

    def test_youtube_quota_exceeded_error_message(self):
        err_403 = HttpError(
            SimpleNamespace(status=403, reason="Forbidden"),
            b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}',
        )
        msg = _parse_youtube_http_error(err_403)
        self.assertIsNotNone(msg)
        self.assertIn("quotaExceeded", msg)

        err_404 = HttpError(SimpleNamespace(status=404, reason="Not Found"), b"{}")
        self.assertIsNone(_parse_youtube_http_error(err_404))

    def test_sanitize_clips_clamps_short_video_to_actual_duration(self):
        raw = [
            ViralClip(
                start_seconds=5.0,
                end_seconds=40.0,
                viral_score=8,
                trigger_type="CHAT",
                hook_banner="short clip test",
                title="Short VOD",
                caption="Short caption",
                description="Short desc",
            )
        ]
        cleaned = sanitize_clips(raw, video_duration=20.0, target_duration=35.0, max_clips=1)
        self.assertEqual(len(cleaned), 1)
        self.assertEqual(cleaned[0].start_seconds, 0.0)
        self.assertEqual(cleaned[0].end_seconds, 20.0)

    def test_direct_video_url_handles_channel_live_tabs(self):
        self.assertFalse(is_direct_video_url("https://www.youtube.com/@IShowSpeed/live/"))
        self.assertFalse(is_direct_video_url("https://www.youtube.com/@DarkViperAU/streams"))
        self.assertTrue(is_direct_video_url("https://www.youtube.com/live/dQw4w9WgXcQ"))
        self.assertTrue(is_direct_video_url("https://youtu.be/wEGdTkRL-uI"))

    def test_youtube_oauth_status_detects_missing_and_invalid_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            secret_file = os.path.join(td, "client_secret.json")
            token_file = os.path.join(td, "token.json")
            orig_base = youtube_uploader.BASE_DIR
            orig_sec = youtube_uploader.CLIENT_SECRET_FILE
            orig_tok = youtube_uploader.TOKEN_FILE
            orig_id = os.environ.get("YOUTUBE_CLIENT_ID")
            orig_csec = os.environ.get("YOUTUBE_CLIENT_SECRET")
            try:
                youtube_uploader.BASE_DIR = td
                youtube_uploader.CLIENT_SECRET_FILE = secret_file
                youtube_uploader.TOKEN_FILE = token_file
                os.environ["YOUTUBE_CLIENT_ID"] = "dummy-id.apps.googleusercontent.com"
                os.environ["YOUTUBE_CLIENT_SECRET"] = "dummy-secret"

                resolved = youtube_uploader.find_client_secret_file()
                self.assertEqual(resolved, secret_file)
                self.assertTrue(os.path.exists(secret_file))

                with open(token_file, "w", encoding="utf-8") as tf:
                    tf.write('{"invalid": "corrupt_token"}')
                status = youtube_uploader.get_oauth_status(validate_token=False)
                self.assertFalse(status["token_valid"])
                self.assertTrue(status["needs_reauth"])
            finally:
                youtube_uploader.BASE_DIR = orig_base
                youtube_uploader.CLIENT_SECRET_FILE = orig_sec
                youtube_uploader.TOKEN_FILE = orig_tok
                if orig_id is None:
                    os.environ.pop("YOUTUBE_CLIENT_ID", None)
                else:
                    os.environ["YOUTUBE_CLIENT_ID"] = orig_id
                if orig_csec is None:
                    os.environ.pop("YOUTUBE_CLIENT_SECRET", None)
                else:
                    os.environ["YOUTUBE_CLIENT_SECRET"] = orig_csec

    def test_save_config_does_not_copy_env_key_into_config_json(self):
        with tempfile.TemporaryDirectory() as td:
            fake_cfg = os.path.join(td, "config.json")
            orig_file = config.CONFIG_FILE
            orig_default = dict(config.DEFAULT_CONFIG)
            try:
                config.CONFIG_FILE = fake_cfg
                config.DEFAULT_CONFIG["gemini_api_key"] = "SECRET_FROM_ENV"
                loaded = config.save_config({"parallel_workers": 4})
                self.assertEqual(loaded["gemini_api_key"], "SECRET_FROM_ENV")
                self.assertEqual(loaded["parallel_workers"], 4)
                with open(fake_cfg, encoding="utf-8") as f:
                    raw = json.load(f)
                self.assertEqual(raw["gemini_api_key"], "")
            finally:
                config.CONFIG_FILE = orig_file
                config.DEFAULT_CONFIG.update(orig_default)

    def test_canva_status_and_credentials_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            env_path = os.path.join(td, ".env")
            tok_path = os.path.join(td, "canva_token.json")
            orig_env = canva_connector.ENV_FILE
            orig_tok = canva_connector.CANVA_TOKEN_FILE
            saved_vars = {k: os.environ.get(k) for k in ("CANVA_CLIENT_ID", "CANVA_CLIENT_SECRET", "CANVA_BRAND_TEMPLATE_ID", "CANVA_ACCESS_TOKEN", "CANVA_REFRESH_TOKEN")}
            try:
                canva_connector.ENV_FILE = env_path
                canva_connector.CANVA_TOKEN_FILE = tok_path
                for k in saved_vars:
                    os.environ.pop(k, None)

                self.assertFalse(canva_connector.is_canva_connected())
                canva_connector.save_canva_credentials(
                    client_id="cid",
                    client_secret="csec",
                    brand_template_id="tmpl1",
                    access_token="tok123",
                )
                status = canva_connector.get_canva_status()
                self.assertTrue(status["connected"])
                self.assertTrue(status["has_client_creds"])
                self.assertEqual(status["brand_template_id"], "tmpl1")

                canva_connector.disconnect_canva()
                self.assertFalse(canva_connector.is_canva_connected())
            finally:
                canva_connector.ENV_FILE = orig_env
                canva_connector.CANVA_TOKEN_FILE = orig_tok
                for k, v in saved_vars.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_unified_seo_metadata_formatting(self):
        tags = extract_tags_from_clip_info(
            caption="Wild #GTA6 #Shorts clip",
            description="More #Gaming highlights",
            trigger_type="CLUTCH",
        )
        txt = format_seo_metadata_text(
            title="GTA6 Clip #Shorts",
            hook_banner="CRAZY ESCAPE",
            caption="Wild #GTA6 #Shorts clip",
            description="More #Gaming highlights",
            tags=tags,
            viral_score=9,
            trigger_type="CLUTCH",
            source_id="vid999",
        )
        self.assertIn("[TAGS]", txt)
        self.assertIn("GTA6, Shorts, Gaming", txt)
        self.assertIn("20 (Gaming)", txt)

    def test_youtube_expired_token_without_refresh_token_raises_in_headless(self):
        with tempfile.TemporaryDirectory() as td:
            secret_file = os.path.join(td, "client_secret.json")
            token_file = os.path.join(td, "token.json")
            with open(secret_file, "w", encoding="utf-8") as sf:
                sf.write("{}")
            with open(token_file, "w", encoding="utf-8") as tf:
                tf.write('{"token": "stale"}')
            orig_tok = youtube_uploader.TOKEN_FILE
            orig_finder = youtube_uploader.find_client_secret_file
            orig_from_file = youtube_uploader.Credentials.from_authorized_user_file
            try:
                youtube_uploader.TOKEN_FILE = token_file
                youtube_uploader.find_client_secret_file = lambda: secret_file
                youtube_uploader.Credentials.from_authorized_user_file = classmethod(
                    lambda cls, p, scopes: SimpleNamespace(valid=False, expired=True, refresh_token=None)
                )
                with self.assertRaises(RuntimeError):
                    youtube_uploader.get_authenticated_service(interactive=False)
            finally:
                youtube_uploader.TOKEN_FILE = orig_tok
                youtube_uploader.find_client_secret_file = orig_finder
                youtube_uploader.Credentials.from_authorized_user_file = orig_from_file

    def test_cleanup_stale_temp_dirs_removes_only_expired(self):
        root_tmp = os.path.join(tempfile.gettempdir(), "gta6_clipper_tmp")
        os.makedirs(root_tmp, exist_ok=True)
        suffix = f"{os.getpid()}_{int(time.time())}"
        old_dir = os.path.join(root_tmp, f"_ut_old_{suffix}")
        fresh_dir = os.path.join(root_tmp, f"_ut_fresh_{suffix}")
        os.makedirs(old_dir, exist_ok=True)
        os.makedirs(fresh_dir, exist_ok=True)
        stale_ts = time.time() - 5000
        os.utime(old_dir, (stale_ts, stale_ts))
        try:
            pipeline.cleanup_stale_temp_dirs(max_age_sec=3600)
            self.assertFalse(os.path.exists(old_dir))
            self.assertTrue(os.path.exists(fresh_dir))
        finally:
            for d in (old_dir, fresh_dir):
                if os.path.exists(d):
                    os.rmdir(d)

    def test_verify_gemini_api_key_handles_empty_key(self):
        res = verify_gemini_api_key("")
        self.assertFalse(res["ok"])
        self.assertIn("empty", res["error"].lower())


if __name__ == "__main__":
    unittest.main()

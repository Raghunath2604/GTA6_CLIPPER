"""Senior QA pytest suite covering edge cases, retries, error paths, and UI smoke checks."""

from types import SimpleNamespace
import pytest
from googleapiclient.errors import HttpError
from PIL import Image

import chat_analyzer
import config
import instagram_uploader
import media_packager
import processed_tracker
import video_editor
import youtube_uploader


def test_update_env_file_handles_missing_trailing_newline_and_comments(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=1\n# TARGET_KEY=old", encoding="utf-8")
    monkeypatch.delenv("TARGET_KEY", raising=False)
    monkeypatch.delenv("NEW_KEY", raising=False)

    config.update_env_file(str(env_file), {"TARGET_KEY": "updated", "NEW_KEY": "added"})

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines == ["EXISTING=1", "TARGET_KEY=updated", "NEW_KEY=added"]


@pytest.mark.parametrize("raw_name,expected", [
    ("!!!???", "Clip_Highlight"),
    ("GTA 6 / Leak #1!", "GTA_6_Leak_1"),
    ("...__", "Clip_Highlight"),
])
def test_sanitize_filename_never_returns_empty(raw_name, expected):
    assert media_packager.sanitize_filename(raw_name) == expected


def test_processed_tracker_strips_whitespace(tmp_path, monkeypatch):
    tracker = tmp_path / "processed.txt"
    monkeypatch.setattr(processed_tracker, "TRACKER_FILE", str(tracker))

    processed_tracker.mark_processed("  vid_space  ", "My\nStream")
    assert processed_tracker.is_processed("vid_space")
    assert processed_tracker.is_processed("  vid_space ")
    processed_tracker.mark_processed("vid_space", "Duplicate")
    lines = [ln for ln in tracker.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1


def test_chat_analyzer_zero_duration_fallback():
    hotspots = chat_analyzer.analyze_chat_explosions("", video_duration_sec=0.0, top_n=4)
    assert len(hotspots) == 4
    assert all(h.timestamp_seconds > 0 for h in hotspots)


def test_video_editor_blank_hook_overlay_and_failed_fallback(tmp_path, monkeypatch):
    out_png = tmp_path / "banner.png"
    video_editor.create_hook_banner_overlay("   ", str(out_png))
    assert out_png.exists()
    with Image.open(out_png) as img:
        assert img.size == (1080, 1920)

    fake_mp4 = tmp_path / "out.mp4"
    fake_mp4.write_bytes(b"stale_data_" * 200)
    monkeypatch.setattr(video_editor, "detect_best_h264_encoder", lambda: ["-c:v", "libx264"])
    monkeypatch.setattr(video_editor, "run_ffmpeg", lambda args, timeout=600: SimpleNamespace(returncode=1, stderr="boom"))

    ok = video_editor.render_viral_clip(
        source_video_path=str(tmp_path / "missing.mp4"),
        start_sec=0.0,
        duration_sec=35.0,
        output_clip_path=str(fake_mp4),
        hook_banner="TEST",
        clean_thumbnail_path=str(tmp_path / "clean.jpg"),
        post_thumbnail_path=str(tmp_path / "post.jpg"),
    )
    assert ok is False


@pytest.mark.parametrize("status,body,expected_substr", [
    (401, b"{}", "HTTP 401"),
    (403, b'{"error":{"errors":[{"reason":"quotaExceeded"}]}}', "quotaExceeded"),
    (403, b'{"error":{"errors":[{"reason":"accessNotConfigured"}]}}', "not enabled"),
    (403, b'{"error":{"errors":[{"reason":"youtubeSignupRequired"}]}}', "YouTube channel"),
])
def test_youtube_http_error_translations(status, body, expected_substr):
    err = HttpError(SimpleNamespace(status=status, reason="Error"), body)
    msg = youtube_uploader._parse_youtube_http_error(err)
    assert msg is not None
    assert expected_substr in msg


def test_youtube_upload_guards_corrupt_video_and_oversized_thumbnail(tmp_path, monkeypatch):
    tiny = tmp_path / "tiny.mp4"
    tiny.write_bytes(b"too_small")
    with pytest.raises(ValueError, match="empty or corrupt"):
        youtube_uploader.upload_short_to_youtube(str(tiny), "Title", "Desc", interactive=False)

    valid_mp4 = tmp_path / "valid.mp4"
    valid_mp4.write_bytes(b"0" * 2048)
    big_thumb = tmp_path / "big.jpg"
    big_thumb.write_bytes(b"x" * (2 * 1024 * 1024 + 10))

    class FakeInsertReq:
        def __init__(self):
            self.calls = 0

        def next_chunk(self):
            self.calls += 1
            return None, {"id": "yt123"}

    class FakeVideos:
        def insert(self, **kwargs):
            return FakeInsertReq()

    class FakeThumbnails:
        def __init__(self):
            self.called = False

        def set(self, **kwargs):
            self.called = True
            return self

    fake_thumbs = FakeThumbnails()
    fake_yt = SimpleNamespace(videos=lambda: FakeVideos(), thumbnails=lambda: fake_thumbs)
    monkeypatch.setattr(youtube_uploader, "get_authenticated_service", lambda interactive=True: fake_yt)

    res = youtube_uploader.upload_short_to_youtube(
        str(valid_mp4), "Clip Title", "Clip Desc", thumbnail_path=str(big_thumb), interactive=False
    )
    assert res["video_id"] == "yt123"
    assert res["thumbnail_set"] is False
    assert fake_thumbs.called is False


def test_instagram_meta_graph_aborts_immediately_on_status_error(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr(instagram_uploader.time, "sleep", lambda s: sleep_calls.append(s))

    def fake_post(url, data=None, headers=None, timeout=30):
        return SimpleNamespace(status_code=200, json=lambda: {"id": "container_1"})

    def fake_get(url, params=None, timeout=15):
        return SimpleNamespace(json=lambda: {"error": {"message": "Invalid OAuth 2.0 Access Token"}})

    monkeypatch.setattr(instagram_uploader.requests, "post", fake_post)
    monkeypatch.setattr(instagram_uploader.requests, "get", fake_get)

    res = instagram_uploader.upload_reel_meta_graph(
        "https://example.com/clip.mp4",
        "caption",
        access_token="bad_tok",
        account_id="act_1",
    )
    assert res["status"] == "error"
    assert "Invalid OAuth" in res["error"]
    assert len(sleep_calls) == 1


def test_streamlit_dashboard_apptest_smoke():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file("app.py", default_timeout=45).run()
    assert not list(at.exception)
    assert not list(at.error)
    at.selectbox(key="gallery_sort").select("Unposted First").run()
    assert not list(at.exception)

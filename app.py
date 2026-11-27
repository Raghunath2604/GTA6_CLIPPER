"""
GTA6 Viral Clipper Pro - Streamlit Control Dashboard.
Full-stack automated short-form content factory for gaming YouTube streamers.
Supports:
1. Direct YouTube video / stream links.
2. Multi-channel streamer live & VOD monitoring.
3. Configurable clip duration (>30s up to 60s).
4. Auto-subtitles (faster-whisper).
5. Dual thumbnails (Canva Pro clean + ready-to-post).
6. 1-Click YouTube Shorts Draft Upload.
"""

import os
import sys
import glob
import json
import hashlib
import shutil
import threading
import subprocess
import streamlit as st
from typing import Dict, Any, List

import config as cfg_module
from ai_clipper import verify_gemini_api_key
from ffmpeg_utils import check_ffmpeg_health, detect_best_h264_encoder
from logger import LOG_FILE
from media_packager import extract_tags_from_clip_info, format_seo_metadata_text
from processed_tracker import is_processed, mark_processed, get_all_processed, TRACKER_FILE
from stream_manager import get_channel_latest_stream_info, is_direct_video_url
from pipeline import run_parallel_clipper, ChannelTaskState, process_single_streamer, cleanup_stale_temp_dirs
from youtube_uploader import (
    get_oauth_status,
    get_authenticated_service,
    upload_short_to_youtube,
    is_client_secret_present,
    disconnect_youtube,
    CLIENT_SECRET_FILE,
)
from instagram_uploader import (
    upload_reel_to_instagram,
    is_instagram_connected,
    get_instagram_status,
    login_instagram_direct,
    save_instagram_credentials,
    disconnect_instagram,
)
from canva_connector import (
    get_canva_status,
    save_canva_credentials,
    disconnect_canva,
    upload_thumbnail_asset_to_canva,
)

st.set_page_config(
    page_title="GTA6 Viral Clipper Pro",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .main-title {
        font-size: 2.3rem;
        font-weight: 800;
        color: #FF4B4B;
        margin-bottom: 0px;
    }
    .sub-title {
        font-size: 1.05rem;
        color: #888888;
        margin-bottom: 20px;
    }
    .clip-card {
        background: #181920;
        border: 1px solid #2B2C3A;
        border-radius: 12px;
        padding: 18px;
        margin-bottom: 25px;
    }
    .badge-viral {
        background-color: #E63946;
        color: white;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: bold;
    }
    .badge-trigger {
        background-color: #457B9D;
        color: white;
        padding: 3px 8px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: bold;
    }
</style>
""", unsafe_allow_html=True)

class RunnerState:
    """Thread-safe shared state so background worker threads never access st.session_state directly."""
    def __init__(self):
        self.lock = threading.Lock()
        self.running: bool = False
        self.needs_gallery_refresh: bool = False
        self.logs: List[str] = []
        self.task_states: Dict[str, ChannelTaskState] = {}

    def reset_for_run(self, initial_log: str):
        with self.lock:
            self.running = True
            self.needs_gallery_refresh = False
            self.logs = [initial_log]
            self.task_states = {}

    def try_start(self, initial_log: str) -> bool:
        """Atomically start a pipeline run only if idle; prevents double-click race."""
        with self.lock:
            if self.running:
                return False
            self.running = True
            self.needs_gallery_refresh = False
            self.logs = [initial_log]
            self.task_states = {}
            return True

    def add_log(self, msg: str):
        with self.lock:
            self.logs.append(msg)
            if len(self.logs) > 200:
                self.logs.pop(0)

    def set_states(self, states: Dict[str, ChannelTaskState]):
        with self.lock:
            self.task_states = dict(states)

    def finish(self):
        with self.lock:
            self.running = False
            self.needs_gallery_refresh = True

    def consume_refresh_flag(self) -> bool:
        with self.lock:
            flag = self.needs_gallery_refresh
            self.needs_gallery_refresh = False
            return flag


@st.cache_resource
def get_runner() -> RunnerState:
    return RunnerState()


runner = get_runner()

if "config" not in st.session_state:
    st.session_state.config = cfg_module.load_config()

if "upload_status" not in st.session_state:
    st.session_state.upload_status = {}

def add_log(msg: str):
    runner.add_log(msg)

# Sidebar Configuration
with st.sidebar:
    st.title("⚙️ Clipper Configuration")
    
    gemini_key = st.text_input(
        "🔑 Gemini API Key",
        value=st.session_state.config.get("gemini_api_key", ""),
        type="password",
        help="Used for multimodal viral highlight selection (gemini-3-flash-preview / gemini-flash-latest)."
    )
    if gemini_key != st.session_state.config.get("gemini_api_key", ""):
        st.session_state.config = cfg_module.save_config({"gemini_api_key": gemini_key})
        st.toast("Saved Gemini API Key!", icon="💾")
    if st.button("⚡ Test Gemini Key", key="sb_test_gemini_btn", width="stretch"):
        with st.spinner("Testing Gemini API key..."):
            g_res = verify_gemini_api_key(gemini_key)
            if g_res.get("ok"):
                st.success(f"Gemini Key Valid ✅ (`{g_res.get('model')}`)")
            else:
                st.error(f"Gemini check failed: {g_res.get('error')}")

    st.subheader("⏱️ Clip Duration & Slicing")
    clip_duration = st.slider(
        "Clip Duration (>30s Shorts Format):",
        min_value=30,
        max_value=60,
        value=int(st.session_state.config.get("clip_duration_seconds", 35)),
        step=5,
        help="Exact duration of generated vertical shorts. Starts 5-8s before climax for high retention."
    )
    if clip_duration != st.session_state.config.get("clip_duration_seconds"):
        st.session_state.config["clip_duration_seconds"] = clip_duration
        cfg_module.save_config({"clip_duration_seconds": clip_duration})

    clips_per_stream = st.slider(
        "Clips Per Video / Stream:",
        min_value=1,
        max_value=5,
        value=int(st.session_state.config.get("clips_per_stream", 2)),
        help="Number of viral clips to extract."
    )
    if clips_per_stream != st.session_state.config.get("clips_per_stream"):
        st.session_state.config["clips_per_stream"] = clips_per_stream
        cfg_module.save_config({"clips_per_stream": clips_per_stream})

    st.subheader("⚡ Concurrency & Live Ingestion")
    parallel_workers = st.slider(
        "Parallel Workers:",
        min_value=1,
        max_value=4,
        value=int(st.session_state.config.get("parallel_workers", 3)),
        help="Concurrent channels processed via ThreadPoolExecutor."
    )
    if parallel_workers != st.session_state.config.get("parallel_workers"):
        st.session_state.config["parallel_workers"] = parallel_workers
        cfg_module.save_config({"parallel_workers": parallel_workers})

    live_mode = st.selectbox(
        "Live Broadcast Handling:",
        options=["record_buffer", "skip_active"],
        index=0 if st.session_state.config.get("live_mode") == "record_buffer" else 1,
        format_func=lambda x: "🎥 Record Timed Live Buffer" if x == "record_buffer" else "⏭️ Skip Active Broadcasts Until Finished",
    )
    if live_mode != st.session_state.config.get("live_mode"):
        st.session_state.config["live_mode"] = live_mode
        cfg_module.save_config({"live_mode": live_mode})

    max_duration = st.slider(
        "Max Stream Capture (Minutes):",
        min_value=10,
        max_value=90,
        value=int(st.session_state.config.get("max_capture_minutes", 30)),
        step=5
    )
    if max_duration != st.session_state.config.get("max_capture_minutes"):
        st.session_state.config["max_capture_minutes"] = max_duration
        cfg_module.save_config({"max_capture_minutes": max_duration})

    auto_subs = st.toggle("🔤 Auto-Subtitles (faster-whisper)", value=bool(st.session_state.config.get("auto_subtitles", True)))
    burn_banner = st.toggle("🏷️ Top Hook Banner & Dual Thumbnails", value=bool(st.session_state.config.get("burn_hook_banner", True)))
    auto_clean = st.toggle("🧹 Auto-Cleanup Temporary Files", value=bool(st.session_state.config.get("auto_cleanup", True)))

    if (auto_subs != st.session_state.config.get("auto_subtitles") or 
        burn_banner != st.session_state.config.get("burn_hook_banner") or 
        auto_clean != st.session_state.config.get("auto_cleanup")):
        st.session_state.config.update({
            "auto_subtitles": auto_subs,
            "burn_hook_banner": burn_banner,
            "auto_cleanup": auto_clean
        })
        cfg_module.save_config({
            "auto_subtitles": auto_subs,
            "burn_hook_banner": burn_banner,
            "auto_cleanup": auto_clean
        })

    st.divider()
    st.subheader("🩺 System Health")
    @st.cache_data(ttl=60)
    def _cached_system_health():
        ff = check_ffmpeg_health()
        enc = detect_best_h264_encoder()
        return {"ffmpeg_ready": ff["ready"], "encoder": enc[1] if len(enc) > 1 else "libx264"}

    health = _cached_system_health()
    if health["ffmpeg_ready"]:
        st.success(f"FFmpeg Ready • Encoder: `{health['encoder']}`")
    else:
        install_cmd = (
            "winget install Gyan.FFmpeg"
            if sys.platform == "win32"
            else ("brew install ffmpeg" if sys.platform == "darwin" else "sudo apt-get install ffmpeg")
        )
        st.error(f"FFmpeg not found! Run `{install_cmd}`.")

    if st.button("🧹 Clear Stale Temp Cache", key="sb_clear_tmp_btn", width="stretch"):
        removed_tmp = cleanup_stale_temp_dirs(max_age_sec=0)
        st.toast(f"Cleaned {removed_tmp} temp run folder(s).", icon="🧹")

    with st.expander("🪵 System Log Tail"):
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as lf:
                tail_lines = lf.readlines()[-35:]
            st.code("".join(tail_lines) or "Log empty.", language=None)
        else:
            st.caption("No logs written yet.")

    st.divider()
    st.subheader("🔴 YouTube Shorts OAuth")

    @st.cache_data(ttl=60)
    def _cached_yt_oauth():
        return get_oauth_status(validate_token=True)

    oauth_info = _cached_yt_oauth()
    if oauth_info.get("token_valid"):
        st.success("Authenticated & Connected ✅")
        yt_c1, yt_c2 = st.columns(2)
        with yt_c1:
            if st.button("🔄 Refresh", key="sb_yt_refresh_btn", width="stretch"):
                _cached_yt_oauth.clear()
                st.rerun()
        with yt_c2:
            if st.button("🔌 Disconnect", key="sb_yt_disconnect_btn", width="stretch"):
                disconnect_youtube()
                _cached_yt_oauth.clear()
                st.rerun()
    elif oauth_info.get("has_secret"):
        if oauth_info.get("needs_reauth"):
            st.warning("⚠️ Token Expired / Revoked — Re-authorize below")
        else:
            st.info(oauth_info["status_text"])
        if st.button("🔐 Connect / Re-authorize YouTube", key="sb_yt_auth_btn", type="primary", width="stretch"):
            with st.spinner("Waiting for Google OAuth sign-in in your browser..."):
                try:
                    get_authenticated_service(interactive=True)
                    _cached_yt_oauth.clear()
                    st.success("YouTube connected!")
                    st.rerun()
                except Exception as auth_err:
                    st.error(f"YouTube sign-in notice: {auth_err}")
        if st.button("🔄 Re-check Auth Status", key="sb_yt_recheck_btn", width="stretch"):
            _cached_yt_oauth.clear()
            st.rerun()
    else:
        st.warning(oauth_info["status_text"])

    with st.expander("📄 Update client_secret.json"):
        uploaded_secret = st.file_uploader("Upload Google OAuth JSON", type=["json"], key="sb_yt_secret_up")
        if uploaded_secret is not None:
            raw_bytes = uploaded_secret.getvalue()
            secret_hash = hashlib.sha256(raw_bytes).hexdigest()
            if st.session_state.get("last_secret_sha256") != secret_hash:
                try:
                    parsed_secret = json.loads(raw_bytes.decode("utf-8"))
                    if not isinstance(parsed_secret, dict) or not ("installed" in parsed_secret or "web" in parsed_secret):
                        raise ValueError("Expected Google Cloud OAuth JSON with an 'installed' or 'web' key.")
                    with open(CLIENT_SECRET_FILE, "wb") as sf:
                        sf.write(raw_bytes)
                    st.session_state["last_secret_sha256"] = secret_hash
                    _cached_yt_oauth.clear()
                    st.success("Saved client_secret.json!")
                    st.rerun()
                except Exception as sec_err:
                    st.error(f"Invalid Google OAuth JSON: {sec_err}")

    st.divider()
    st.subheader("📸 Instagram Reels Connection")
    ig_status = get_instagram_status()
    if ig_status["connected"]:
        st.success(f"{ig_status['status_text']} ({ig_status['method']})")
        if st.button("🔌 Disconnect Instagram", key="sb_ig_disconnect_btn", width="stretch"):
            disconnect_instagram()
            st.rerun()
    else:
        st.warning("Not Connected")
        with st.expander("🔑 Connect Instagram Account"):
            tab_direct, tab_meta = st.tabs(["Direct Login", "Meta Graph API"])
            with tab_direct:
                ig_user = st.text_input("Instagram Username", key="sb_ig_user")
                ig_pass = st.text_input("Instagram Password", type="password", key="sb_ig_pass")
                ig_2fa = st.text_input("2FA Code (only if enabled)", key="sb_ig_2fa")
                if st.button("Log In to Instagram", key="sb_ig_login_btn"):
                    if ig_user and ig_pass:
                        with st.spinner("Authenticating with Instagram..."):
                            res = login_instagram_direct(ig_user, ig_pass, verification_code=ig_2fa or None)
                            if res.get("status") == "success":
                                st.success(res.get("message"))
                                st.rerun()
                            elif res.get("status") == "two_factor_required":
                                st.warning(res.get("message"))
                            else:
                                st.error(res.get("message"))
                    else:
                        st.warning("Enter both username and password.")
            with tab_meta:
                meta_token = st.text_input("Meta Access Token", key="sb_meta_token")
                meta_act = st.text_input("Account ID", key="sb_meta_act")
                if st.button("Save Meta Credentials", key="sb_meta_save_btn"):
                    if meta_token and meta_act:
                        save_instagram_credentials(access_token=meta_token, account_id=meta_act)
                        st.success("Credentials saved to .env!")
                        st.rerun()
                    else:
                        st.warning("Enter both token and account ID.")

    st.divider()
    st.subheader("🎨 Canva Pro Connect")
    canva_info = get_canva_status()
    if canva_info["connected"]:
        st.success(canva_info["status_text"])
        if st.button("🔌 Disconnect Canva", key="sb_canva_disconnect_btn", width="stretch"):
            disconnect_canva()
            st.rerun()
    else:
        st.info(canva_info["status_text"])
        with st.expander("🛠️ Configure Canva Credentials"):
            c_id = st.text_input("Canva Client ID", key="sb_canva_id")
            c_sec = st.text_input("Canva Client Secret", type="password", key="sb_canva_sec")
            c_tmpl = st.text_input("Brand Template ID (Optional)", key="sb_canva_tmpl")
            if st.button("Save Canva Credentials", key="sb_canva_save_btn"):
                if c_id and c_sec:
                    save_canva_credentials(client_id=c_id, client_secret=c_sec, brand_template_id=c_tmpl or None)
                    st.success("Saved to .env! Run `Login_CLI.bat --service canva` to finish OAuth.")
                    st.rerun()
                else:
                    st.warning("Enter both Client ID and Client Secret.")


def _update_clip_metadata(folder_path: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Merge updated fields into a clip folder's metadata.json safely."""
    meta_path = os.path.join(folder_path, "metadata.json")
    meta_obj: Dict[str, Any] = {}
    if os.path.exists(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as mf:
                loaded = json.load(mf)
                if isinstance(loaded, dict):
                    meta_obj = loaded
        except Exception:
            meta_obj = {}
    meta_obj.update(updates)
    with open(meta_path, "w", encoding="utf-8") as mf:
        json.dump(meta_obj, mf, indent=2)
    return meta_obj


def get_existing_clips() -> List[Dict[str, Any]]:
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), st.session_state.config.get("output_dir", "Output_Clips"))
    if not os.path.exists(output_dir):
        return []

    clip_folders = sorted(
        [os.path.join(output_dir, d) for d in os.listdir(output_dir) if os.path.isdir(os.path.join(output_dir, d))],
        key=os.path.getmtime,
        reverse=True
    )

    clips = []
    for folder in clip_folders:
        folder_name = os.path.basename(folder)
        mp4_files = glob.glob(os.path.join(folder, "*.mp4"))
        clean_thumbs = glob.glob(os.path.join(folder, "*_clean.jpg"))
        post_thumbs = [p for p in glob.glob(os.path.join(folder, "*.jpg")) if not p.endswith("_clean.jpg")]
        srt_files = glob.glob(os.path.join(folder, "*.srt"))
        seo_files = glob.glob(os.path.join(folder, "SEO_metadata.txt"))
        meta_json_files = glob.glob(os.path.join(folder, "metadata.json"))

        title = folder_name
        video_id = folder_name.split("_Clip")[0]
        hook = ""
        caption = ""
        description = ""
        tags = []
        viral_score = 8
        trigger_type = "VIRAL_MOMENT"
        youtube_upload = None
        instagram_upload = None

        if meta_json_files and os.path.exists(meta_json_files[0]):
            try:
                with open(meta_json_files[0], "r", encoding="utf-8") as f:
                    mj = json.load(f)
                    video_id = mj.get("video_id") or video_id
                    title = mj.get("title") or title
                    hook = mj.get("hook_banner") or hook
                    caption = mj.get("caption") or caption
                    description = mj.get("description") or mj.get("seo_description") or description
                    tags = mj.get("tags") or mj.get("hashtags") or []
                    viral_score = mj.get("viral_score", 8)
                    trigger_type = mj.get("trigger_type", "VIRAL_MOMENT")
                    youtube_upload = mj.get("youtube_upload")
                    instagram_upload = mj.get("instagram_upload")
            except Exception:
                pass

        if (not caption or not description) and seo_files and os.path.exists(seo_files[0]):
            try:
                with open(seo_files[0], "r", encoding="utf-8") as f:
                    content = f.read()
                    if "[TITLE]" in content:
                        title = content.split("[TITLE]")[1].split("[HOOK BANNER]")[0].strip() or title
                    if "[HOOK BANNER]" in content:
                        hook = content.split("[HOOK BANNER]")[1].split("[CAPTION")[0].strip() or hook
                    if "[CAPTION" in content:
                        caption = content.split("[CAPTION (FOR TIKTOK / INSTAGRAM REELS / SHORTS)]")[1].split("[YOUTUBE")[0].strip() or caption
                    if "[YOUTUBE SHORTS DESCRIPTION]" in content:
                        description = content.split("[YOUTUBE SHORTS DESCRIPTION]")[1].split("[TAGS")[0].split("[METRICS")[0].strip() or description
                    if "Viral Potential Score :" in content:
                        score_str = content.split("Viral Potential Score :")[1].split("/")[0].strip()
                        viral_score = int(score_str)
                    if "Trigger Category      :" in content:
                        trigger_type = content.split("Trigger Category      :")[1].split("\n")[0].strip()
            except Exception:
                pass

        if not caption:
            caption = f"{title} 🔥 Watch this viral gaming moment! #Shorts #Gaming #Viral"
        if not description:
            description = f"{title}\n\nEnjoy this high-retention gaming short! Like and subscribe for more daily viral clips.\n\n#Shorts #Gaming #Viral #Trending"

        try:
            safe_score = max(1, min(10, int(round(float(viral_score)))))
        except (TypeError, ValueError):
            safe_score = 8

        clips.append({
            "folder": folder,
            "folder_name": folder_name,
            "video_id": video_id,
            "mp4": mp4_files[0] if mp4_files else None,
            "clean_thumb": clean_thumbs[0] if clean_thumbs else None,
            "post_thumb": post_thumbs[0] if post_thumbs else None,
            "srt": srt_files[0] if srt_files else None,
            "seo_file": seo_files[0] if seo_files else None,
            "title": title,
            "hook": hook,
            "caption": caption,
            "description": description,
            "tags": [str(t).lstrip("#") for t in tags] if isinstance(tags, list) else [],
            "viral_score": safe_score,
            "trigger_type": trigger_type,
            "youtube_upload": youtube_upload,
            "instagram_upload": instagram_upload,
        })
    return clips

# Main Dashboard Header
st.markdown('<div class="main-title">🎬 GTA6 Viral Clipper Pro</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Senior AI Short-Form Content Factory • Clips Live Streams & Direct YouTube Videos into >30s Shorts</div>', unsafe_allow_html=True)

# Tabs for Input Modes
tab_direct, tab_multi = st.tabs(["⚡ Direct YouTube Video / Stream Clipper", "📺 Multi-Streamer Parallel Monitor"])

with tab_direct:
    st.markdown("##### Clip Any YouTube Video or Live Stream Directly into Viral 9:16 Shorts (>30s)")
    col_d1, col_d2 = st.columns([3, 1])
    with col_d1:
        direct_url = st.text_input(
            "Paste YouTube Video / Live Stream URL:",
            placeholder="https://www.youtube.com/watch?v=... or https://www.youtube.com/live/...",
            key="direct_input"
        )
    with col_d2:
        st.write("")
        st.write("")
        clip_direct_btn = st.button("🚀 Clip This Video Now", type="primary", width="stretch", disabled=runner.running)

with tab_multi:
    st.markdown("##### Monitor Multiple Streamer Channels in Parallel")
    streamers_text = st.text_area(
        "Streamer Channel / Streams URLs (1 per line):",
        value=st.session_state.config.get("streamer_urls", ""),
        height=120
    )
    if streamers_text != st.session_state.config.get("streamer_urls", ""):
        st.session_state.config["streamer_urls"] = streamers_text
        cfg_module.save_config({"streamer_urls": streamers_text})

    col_m1, col_m2, col_m3 = st.columns([2, 1.5, 1.5])
    with col_m1:
        start_multi_btn = st.button("🚀 Start Multi-Channel Pipeline", type="primary", width="stretch", disabled=runner.running)
    with col_m2:
        if st.button("🔄 Refresh Gallery", width="stretch"):
            st.rerun()
    with col_m3:
        if st.button("🧹 Reset Deduplication", width="stretch"):
            if os.path.exists(TRACKER_FILE):
                with open(TRACKER_FILE, "w", encoding="utf-8") as f:
                    f.write("")
            st.toast("Cleared processed_videos.txt!", icon="✨")

    processed_ids = sorted(get_all_processed())
    with st.expander(f"📜 Processed Streams History ({len(processed_ids)} IDs tracked)"):
        if processed_ids:
            st.code("\n".join(processed_ids), language=None)
        else:
            st.caption("No stream IDs recorded yet.")

def pipeline_worker(target_urls: List[str], current_cfg: Dict[str, Any]):
    def log_cb(msg: str):
        runner.add_log(msg)
    def state_cb(states: Dict[str, ChannelTaskState]):
        runner.set_states(states)

    try:
        runner.add_log(f"Starting pipeline for {len(target_urls)} target(s)...")
        run_parallel_clipper(target_urls, current_cfg, log_cb, state_cb)
        runner.add_log("Pipeline run finished successfully!")
    except Exception as e:
        runner.add_log(f"Pipeline error: {e}")
    finally:
        runner.finish()

# Trigger Pipeline
if clip_direct_btn:
    if not direct_url.strip():
        st.error("Please enter a valid YouTube video or stream URL.")
    elif not runner.try_start(f"Direct Clipper initiated for: {direct_url.strip()}"):
        st.warning("A pipeline run is already in progress.")
    else:
        cfg_snapshot = dict(st.session_state.config)
        t = threading.Thread(target=pipeline_worker, args=([direct_url.strip()], cfg_snapshot), daemon=True)
        t.start()
        st.rerun()

if 'start_multi_btn' in locals() and start_multi_btn:
    urls = [u.strip() for u in st.session_state.config.get("streamer_urls", "").splitlines() if u.strip()]
    if not urls:
        st.error("Please add at least one streamer URL.")
    elif not runner.try_start(f"Multi-Channel Pipeline initiated with {len(urls)} channels..."):
        st.warning("A pipeline run is already in progress.")
    else:
        cfg_snapshot = dict(st.session_state.config)
        t = threading.Thread(target=pipeline_worker, args=(urls, cfg_snapshot), daemon=True)
        t.start()
        st.rerun()

# Real-Time Execution Status Section
@st.fragment(run_every=3 if runner.running else None)
def render_execution_monitor():
    with runner.lock:
        is_running = runner.running
        logs_snapshot = list(runner.logs)
        states_snapshot = dict(runner.task_states)

    if not is_running and runner.consume_refresh_flag():
        st.rerun()

    if is_running or logs_snapshot:
        with st.expander("📊 Live Execution Monitor & Worker Status", expanded=is_running):
            if states_snapshot:
                st.markdown("#### Worker Status")
                grid_cols = st.columns(min(4, max(1, len(states_snapshot))))
                for idx, (url, task_st) in enumerate(states_snapshot.items()):
                    with grid_cols[idx % len(grid_cols)]:
                        st.markdown(f"**{task_st.channel_name}**")
                        st.caption(f"Status: `{task_st.status}`")
                        st.progress(min(1.0, max(0.0, task_st.progress / 100.0)))
                        if task_st.stream_title:
                            st.caption(f"📺 {task_st.stream_title[:30]}...")

            st.markdown("#### Live Console")
            log_text = "\n".join(logs_snapshot)
            st.text_area("Console Output", value=log_text, height=160, disabled=True)

render_execution_monitor()

st.divider()

# Finished Clips Gallery
st.subheader("🎬 Generated Viral Clips Gallery")
clips_list = get_existing_clips()

if not clips_list:
    st.info("No clips generated yet. Paste a YouTube URL above and click 'Clip This Video Now' to create your first set of viral >30s shorts!")
else:
    avg_score = sum(float(c.get("viral_score", 0)) for c in clips_list) / len(clips_list)
    yt_posted = sum(1 for c in clips_list if c.get("youtube_upload") or c["folder_name"] in st.session_state.upload_status)
    ig_posted = sum(1 for c in clips_list if c.get("instagram_upload") or c["folder_name"] in st.session_state.get("ig_upload_status", {}))

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Total Clips", len(clips_list))
    m2.metric("Avg Viral Score", f"{avg_score:.1f} / 10")
    m3.metric("Posted to YouTube", yt_posted)
    m4.metric("Posted to Instagram", ig_posted)

    f1, f2, f3 = st.columns([2.0, 1.2, 1.3])
    with f1:
        filter_query = st.text_input("🔍 Filter clips (title, caption, or tag):", key="gallery_filter")
    with f2:
        min_score = st.slider("⭐ Min Viral Score:", min_value=1, max_value=10, value=1, key="gallery_min_score")
    with f3:
        sort_by = st.selectbox(
            "📊 Sort By:",
            options=["Newest First", "Highest Viral Score", "Unposted First"],
            index=0,
            key="gallery_sort",
        )

    q = filter_query.strip().lower()
    filtered_clips = [
        c for c in clips_list
        if int(c.get("viral_score", 0)) >= min_score
        and (
            not q
            or q in c["title"].lower()
            or q in c["caption"].lower()
            or q in " ".join(c.get("tags", [])).lower()
        )
    ]
    if sort_by == "Highest Viral Score":
        filtered_clips.sort(key=lambda c: float(c.get("viral_score", 0)), reverse=True)
    elif sort_by == "Unposted First":
        filtered_clips.sort(
            key=lambda c: bool(
                c.get("youtube_upload")
                or c.get("instagram_upload")
                or c["folder_name"] in st.session_state.upload_status
                or c["folder_name"] in st.session_state.get("ig_upload_status", {})
            )
        )

    st.caption(f"Showing {len(filtered_clips)} of {len(clips_list)} clip(s) organized in `Output_Clips/`:")
    if not filtered_clips:
        st.info("No clips match your current filter. Try clearing search or lowering Min Viral Score.")

    canva_connected = get_canva_status().get("connected", False)

    for idx, clip in enumerate(filtered_clips):
        folder_id = clip["folder_name"]

        st.markdown('<div class="clip-card">', unsafe_allow_html=True)
        col_vid, col_meta = st.columns([1, 1.4], gap="medium")

        with col_vid:
            if clip["mp4"] and os.path.exists(clip["mp4"]):
                st.video(clip["mp4"])
                if st.toggle("📦 Prepare MP4 Download", key=f"prep_dl_{folder_id}"):
                    with open(clip["mp4"], "rb") as vf:
                        st.download_button(
                            "⬇️ Download Clip MP4",
                            data=vf.read(),
                            file_name=f"{folder_id}.mp4",
                            mime="video/mp4",
                            key=f"dl_{folder_id}",
                            width="stretch",
                        )
            else:
                st.warning("Video file not found.")

            st.markdown("##### 🖼️ Dual Thumbnails")
            t_col1, t_col2 = st.columns(2)
            with t_col1:
                if clip["clean_thumb"] and os.path.exists(clip["clean_thumb"]):
                    st.image(clip["clean_thumb"], caption="Clean Canva Template", width="stretch")
                    if canva_connected and st.button("🎨 Send to Canva", key=f"canva_{folder_id}", width="stretch"):
                        with st.spinner("Uploading clean frame to Canva..."):
                            cres = upload_thumbnail_asset_to_canva(clip["clean_thumb"], asset_name=f"{folder_id}_clean")
                            if cres.get("status") == "success":
                                st.success(f"Sent to Canva (Asset `{cres.get('asset_id')}`)")
                            else:
                                st.error(cres.get("error", "Canva upload failed"))
            with t_col2:
                if clip["post_thumb"] and os.path.exists(clip["post_thumb"]):
                    st.image(clip["post_thumb"], caption="Ready-To-Post (Burned Hook)", width="stretch")

            if clip["srt"] and os.path.exists(clip["srt"]):
                with st.expander("📝 View / Edit Subtitles (.srt)"):
                    with open(clip["srt"], "r", encoding="utf-8", errors="replace") as sf:
                        srt_content = sf.read()
                    edited_srt = st.text_area("SRT Transcript", value=srt_content, height=140, key=f"srt_view_{folder_id}")
                    if st.button("💾 Save Subtitles", key=f"save_srt_{folder_id}", width="stretch"):
                        with open(clip["srt"], "w", encoding="utf-8") as sf:
                            sf.write(edited_srt)
                        st.toast("Saved subtitle edits to .srt!", icon="💾")

        with col_meta:
            st.markdown(
                f'<span class="badge-viral">🔥 Viral Score: {clip["viral_score"]}/10</span> '
                f'<span class="badge-trigger">🎯 {clip["trigger_type"]}</span>',
                unsafe_allow_html=True
            )
            st.markdown(f"**Hook Banner:** `{clip['hook']}`")
            st.write("")

            edit_title = st.text_input(
                f"Clip Title #{idx+1}:",
                value=clip["title"],
                key=f"title_{folder_id}",
                help="High-CTR title with #Shorts"
            )

            edit_caption = st.text_area(
                "Shorts Caption & Hashtags:",
                value=clip["caption"],
                key=f"caption_{folder_id}",
                height=70
            )

            edit_desc = st.text_area(
                "SEO Description:",
                value=clip["description"],
                key=f"desc_{folder_id}",
                height=100
            )

            meta_row1, meta_row2 = st.columns([2.2, 1.0])
            with meta_row1:
                edit_tags_str = st.text_input(
                    "SEO Tags (comma-separated):",
                    value=", ".join(clip.get("tags", [])),
                    key=f"tags_{folder_id}",
                )
            with meta_row2:
                yt_privacy = st.selectbox(
                    "YouTube Privacy:",
                    options=["private", "unlisted", "public"],
                    index=0,
                    key=f"priv_{folder_id}",
                )

            edited_explicit_tags = [t.strip().lstrip("#") for t in edit_tags_str.split(",") if t.strip()]
            effective_tags = extract_tags_from_clip_info(
                caption=edit_caption,
                description=edit_desc,
                trigger_type=clip["trigger_type"],
                explicit_tags=edited_explicit_tags or None,
            )

            yt_up = st.session_state.upload_status.get(folder_id) or clip.get("youtube_upload")
            ig_up = st.session_state.get("ig_upload_status", {}).get(folder_id) or clip.get("instagram_upload")

            btn_col1, btn_col2, btn_col3, btn_col4, btn_col5 = st.columns([1.7, 1.7, 1.0, 1.0, 0.9])

            with btn_col1:
                upload_key = f"upload_yt_{folder_id}"
                yt_label = "✅ Re-upload YT" if yt_up else "📤 Post to YouTube Shorts"
                yt_type = "secondary" if yt_up else "primary"
                if st.button(yt_label, key=upload_key, type=yt_type, width="stretch"):
                    if not clip["mp4"] or not os.path.exists(clip["mp4"]):
                        st.error("Video file does not exist.")
                    else:
                        with st.spinner(f"Uploading to YouTube Shorts ({yt_privacy})..."):
                            try:
                                res = upload_short_to_youtube(
                                    video_path=clip["mp4"],
                                    title=edit_title,
                                    description=edit_desc,
                                    tags=effective_tags,
                                    privacy_status=yt_privacy,
                                    thumbnail_path=clip.get("post_thumb"),
                                    interactive=False,
                                )
                                st.session_state.upload_status[folder_id] = res
                                _update_clip_metadata(clip["folder"], {"youtube_upload": res})
                                st.success(f"Uploaded to YouTube! Video ID: {res['video_id']}")
                            except Exception as up_err:
                                st.error(f"YouTube upload failed: {up_err}")

            with btn_col2:
                ig_key = f"upload_ig_{folder_id}"
                ig_label = "✅ Re-upload IG" if ig_up else "📸 Post to Instagram Reels"
                if st.button(ig_label, key=ig_key, width="stretch"):
                    if not is_instagram_connected():
                        st.info("💡 Connect your Instagram in the left sidebar (Direct Account Login or Meta Graph API) to post Reels.")
                    else:
                        with st.spinner("Publishing Reel to Instagram..."):
                            try:
                                ig_res = upload_reel_to_instagram(
                                    video_url_or_path=clip["mp4"],
                                    caption=f"{edit_caption}\n\n{edit_desc}",
                                    thumbnail_path=clip.get("post_thumb")
                                )
                                if ig_res.get("status") == "success":
                                    st.session_state.setdefault("ig_upload_status", {})[folder_id] = ig_res
                                    _update_clip_metadata(clip["folder"], {"instagram_upload": ig_res})
                                    reel_link = ig_res.get("url") or f"https://www.instagram.com/reel/{ig_res.get('code', '')}/"
                                    st.success(f"Published to Instagram Reel! 🔗 [Open Reel]({reel_link})")
                                else:
                                    st.warning(ig_res.get("message") or ig_res.get("error"))
                            except Exception as ig_err:
                                st.error(f"Instagram posting notice: {ig_err}")

            with btn_col3:
                if st.button("📁 Folder", key=f"open_{folder_id}", width="stretch"):
                    try:
                        if sys.platform == "win32":
                            os.startfile(clip["folder"])
                        elif sys.platform == "darwin":
                            subprocess.Popen(["open", clip["folder"]])
                        else:
                            subprocess.Popen(["xdg-open", clip["folder"]])
                    except Exception:
                        st.caption(f"Path: {clip['folder']}")

            with btn_col4:
                if st.button("💾 Save", key=f"save_{folder_id}", width="stretch"):
                    try:
                        _update_clip_metadata(clip["folder"], {
                            "title": edit_title,
                            "caption": edit_caption,
                            "description": edit_desc,
                            "seo_description": edit_desc,
                            "tags": effective_tags,
                        })

                        seo_target = clip["seo_file"] or os.path.join(clip["folder"], "SEO_metadata.txt")
                        with open(seo_target, "w", encoding="utf-8") as f:
                            f.write(format_seo_metadata_text(
                                title=edit_title,
                                hook_banner=clip["hook"],
                                caption=edit_caption,
                                description=edit_desc,
                                tags=effective_tags,
                                viral_score=clip["viral_score"],
                                trigger_type=clip["trigger_type"],
                                source_id=clip["video_id"],
                            ))
                        st.toast("Saved changes to metadata.json and SEO_metadata.txt!", icon="💾")
                    except Exception as save_err:
                        st.error(f"Save error: {save_err}")

            with btn_col5:
                if st.button("🗑️ Delete", key=f"del_{folder_id}", width="stretch"):
                    shutil.rmtree(clip["folder"], ignore_errors=True)
                    st.session_state.upload_status.pop(folder_id, None)
                    st.session_state.get("ig_upload_status", {}).pop(folder_id, None)
                    st.toast(f"Deleted clip folder: {clip['folder_name']}", icon="🗑️")
                    st.rerun()

            if yt_up:
                st.info(f"🔗 YouTube Shorts: [{yt_up.get('url')}]({yt_up.get('url')}) (Privacy: {yt_up.get('privacy', 'private')})")
            if ig_up:
                reel_link = ig_up.get("url") or f"https://www.instagram.com/reel/{ig_up.get('code', '')}/"
                st.info(f"📸 Instagram Reel: [{reel_link}]({reel_link})")

        st.markdown('</div>', unsafe_allow_html=True)

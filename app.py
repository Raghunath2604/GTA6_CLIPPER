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
import glob
import json
import shutil
import threading
import subprocess
import streamlit as st
from typing import Dict, Any, List

import config as cfg_module
from ffmpeg_utils import check_ffmpeg_health, detect_best_h264_encoder
from processed_tracker import is_processed, mark_processed, get_all_processed, TRACKER_FILE
from stream_manager import get_channel_latest_stream_info, is_direct_video_url
from pipeline import run_parallel_clipper, ChannelTaskState, process_single_streamer
from youtube_uploader import get_oauth_status, upload_short_to_youtube, is_client_secret_present
from instagram_uploader import (
    upload_reel_to_instagram,
    is_instagram_connected,
    get_instagram_status,
    login_instagram_direct,
    save_instagram_credentials
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
        self.logs: List[str] = []
        self.task_states: Dict[str, ChannelTaskState] = {}

    def reset_for_run(self, initial_log: str):
        with self.lock:
            self.running = True
            self.logs = [initial_log]
            self.task_states = {}

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
        help="Used for multimodal viral hook analysis (gemini-2.5-flash). Auto-saved in config.json."
    )
    if gemini_key != st.session_state.config.get("gemini_api_key", ""):
        st.session_state.config["gemini_api_key"] = gemini_key
        cfg_module.save_config({"gemini_api_key": gemini_key})
        st.toast("Saved Gemini API Key!", icon="💾")

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
        st.error("FFmpeg not found! Run `winget install Gyan.FFmpeg`.")

    st.divider()
    st.subheader("🔴 YouTube Shorts OAuth")
    oauth_info = get_oauth_status()
    if oauth_info["has_token"]:
        st.success("Authenticated & Connected ✅")
    elif oauth_info["has_secret"]:
        st.info("Ready for Login (client_secret.json found ✅)")
    else:
        st.warning(oauth_info["status_text"])

    st.divider()
    st.subheader("📸 Instagram Reels Connection")
    ig_status = get_instagram_status()
    if ig_status["connected"]:
        st.success(f"{ig_status['status_text']} ({ig_status['method']})")
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
        hook = ""
        caption = ""
        description = ""
        tags = []
        viral_score = 8
        trigger_type = "VIRAL_MOMENT"

        if meta_json_files and os.path.exists(meta_json_files[0]):
            try:
                with open(meta_json_files[0], "r", encoding="utf-8") as f:
                    mj = json.load(f)
                    title = mj.get("title") or title
                    hook = mj.get("hook_banner") or hook
                    caption = mj.get("caption") or caption
                    description = mj.get("description") or mj.get("seo_description") or description
                    tags = mj.get("tags") or mj.get("hashtags") or []
                    viral_score = mj.get("viral_score", 8)
                    trigger_type = mj.get("trigger_type", "VIRAL_MOMENT")
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

        clips.append({
            "folder": folder,
            "folder_name": folder_name,
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
            "viral_score": viral_score,
            "trigger_type": trigger_type
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
    else:
        runner.reset_for_run(f"Direct Clipper initiated for: {direct_url.strip()}")
        cfg_snapshot = dict(st.session_state.config)
        t = threading.Thread(target=pipeline_worker, args=([direct_url.strip()], cfg_snapshot), daemon=True)
        t.start()
        st.rerun()

if 'start_multi_btn' in locals() and start_multi_btn:
    urls = [u.strip() for u in st.session_state.config.get("streamer_urls", "").splitlines() if u.strip()]
    if not urls:
        st.error("Please add at least one streamer URL.")
    else:
        runner.reset_for_run(f"Multi-Channel Pipeline initiated with {len(urls)} channels...")
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
    st.caption(f"Showing {len(clips_list)} clips organized in `Output_Clips/`:")
    
    for idx, clip in enumerate(clips_list):
        folder_id = clip["folder_name"]
        
        st.markdown('<div class="clip-card">', unsafe_allow_html=True)
        col_vid, col_meta = st.columns([1, 1.4], gap="medium")
        
        with col_vid:
            if clip["mp4"] and os.path.exists(clip["mp4"]):
                st.video(clip["mp4"])
            else:
                st.warning("Video file not found.")

            st.markdown("##### 🖼️ Dual Thumbnails")
            t_col1, t_col2 = st.columns(2)
            with t_col1:
                if clip["clean_thumb"] and os.path.exists(clip["clean_thumb"]):
                    st.image(clip["clean_thumb"], caption="Clean Canva Template", width="stretch")
            with t_col2:
                if clip["post_thumb"] and os.path.exists(clip["post_thumb"]):
                    st.image(clip["post_thumb"], caption="Ready-To-Post (Burned Hook)", width="stretch")

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

            btn_col1, btn_col2, btn_col3, btn_col4 = st.columns([1.8, 1.8, 1.1, 1.1])
            
            with btn_col1:
                upload_key = f"upload_yt_{folder_id}"
                if st.button("📤 Post to YouTube Shorts", key=upload_key, type="primary", width="stretch"):
                    if not clip["mp4"] or not os.path.exists(clip["mp4"]):
                        st.error("Video file does not exist.")
                    else:
                        with st.spinner("Authorizing and uploading to YouTube Shorts as Private Draft..."):
                            try:
                                res = upload_short_to_youtube(
                                    video_path=clip["mp4"],
                                    title=edit_title,
                                    description=edit_desc,
                                    tags=clip.get("tags"),
                                    privacy_status="private",
                                    thumbnail_path=clip.get("post_thumb")
                                )
                                st.session_state.upload_status[folder_id] = res
                                st.success(f"Uploaded to YouTube! Video ID: {res['video_id']}")
                            except Exception as up_err:
                                st.error(f"YouTube upload failed: {up_err}")

            with btn_col2:
                ig_key = f"upload_ig_{folder_id}"
                if st.button("📸 Post to Instagram Reels", key=ig_key, width="stretch"):
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
                                    reel_link = ig_res.get("url") or f"https://www.instagram.com/reel/{ig_res.get('code', '')}/"
                                    st.success(f"Published to Instagram Reel! 🔗 [Open Reel]({reel_link})")
                                else:
                                    st.warning(ig_res.get("message") or ig_res.get("error"))
                            except Exception as ig_err:
                                st.error(f"Instagram posting notice: {ig_err}")

            with btn_col3:
                if st.button("📁 Folder", key=f"open_{folder_id}", width="stretch"):
                    try:
                        os.startfile(clip["folder"])
                    except Exception:
                        st.caption(f"Path: {clip['folder']}")

            with btn_col4:
                if st.button("💾 Save", key=f"save_{folder_id}", width="stretch"):
                    try:
                        meta_path = os.path.join(clip["folder"], "metadata.json")
                        if os.path.exists(meta_path):
                            with open(meta_path, "r", encoding="utf-8") as mf:
                                meta_obj = json.load(mf)
                        else:
                            meta_obj = {}
                        meta_obj.update({
                            "title": edit_title,
                            "caption": edit_caption,
                            "description": edit_desc,
                            "seo_description": edit_desc,
                        })
                        with open(meta_path, "w", encoding="utf-8") as mf:
                            json.dump(meta_obj, mf, indent=2)

                        seo_target = clip["seo_file"] or os.path.join(clip["folder"], "SEO_metadata.txt")
                        with open(seo_target, "w", encoding="utf-8") as f:
                            f.write(f"""==================================================
GTA6 VIRAL CLIPPER PRO - SEO & PUBLISHING METADATA
==================================================

[TITLE]
{edit_title}

[HOOK BANNER]
{clip['hook']}

[CAPTION (FOR TIKTOK / INSTAGRAM REELS / SHORTS)]
{edit_caption}

[YOUTUBE SHORTS DESCRIPTION]
{edit_desc}

[METRICS & VIRAL ANALYSIS]
Viral Potential Score : {clip['viral_score']} / 10
Trigger Category      : {clip['trigger_type']}
Source Stream ID      : {clip['folder_name'].split('_Clip')[0]}

[THUMBNAIL ASSETS GUIDE]
1. thumbnail_clean.jpg  -> Clean gameplay frame for Canva Pro Brand Templates
2. thumbnail.jpg        -> Ready-to-post thumbnail with hook banner
""")
                        st.toast("Saved changes to metadata.json and SEO_metadata.txt!", icon="💾")
                    except Exception as save_err:
                        st.error(f"Save error: {save_err}")

            if folder_id in st.session_state.upload_status:
                up_res = st.session_state.upload_status[folder_id]
                st.info(f"🔗 YouTube Shorts Draft: [{up_res['url']}]({up_res['url']}) (Privacy: {up_res['privacy']})")

        st.markdown('</div>', unsafe_allow_html=True)

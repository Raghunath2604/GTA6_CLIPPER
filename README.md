# GTAG & Gaming Viral Clipper Pro 🚀

An autonomous, senior-level viral clipper pipeline and web dashboard that turns YouTube livestreams, gamer streams, and video URLs into high-retention 9:16 vertical shorts (>30 seconds) optimized for YouTube Shorts and Instagram Reels.

---

## 🎥 Demo Preview

<p align="center">
  <a href="assets/gta6_clipper_demo.mp4">
    <img src="assets/gta6_clipper_demo.gif" alt="GTA6 Viral Clipper Demo" width="640">
  </a>
</p>

<p align="center">
  <a href="assets/gta6_clipper_demo.mp4">▶️ Watch Full 1080p Demo Video (MP4 with Audio)</a>
</p>

---

## 🌟 Key Features

1. **Dual Ingestion Engine**:
   - Supports direct YouTube video URLs (e.g. `https://youtu.be/...`).
   - Supports streamer `/streams` live tabs with automatic latest VOD fallback and muxed live audio+video capture.

2. **10-Second Chat Burst & Spaced Hotspot Velocity Math**:
   - Analyzes live replay chat to locate chat explosions (LMAO, W, clutch, OMG, screams).
   - Intelligent spaced hotspot fallback when chat is disabled or unavailable.

3. **Multimodal AI Viral Moment Selection (>30s)**:
   - Uses Google Gemini File API (`gemini-3.8-flash`, `gemini-3-flash-preview`, `gemini-flash-latest`) with Pydantic structured output.
   - Configurable clip duration (default 35s, strictly >30s).
   - Generates high-curiosity 3-5 word ALL-CAPS hook banners, high-CTR titles, captions, and SEO descriptions.

4. **Speech-to-Text Auto Subtitles**:
   - Powered by local `faster-whisper` (base model, int8 quantization on CPU) over FFmpeg-extracted 16 kHz mono WAV audio.
   - Clean `1080x1920` ASS yellow/black gaming subtitle styling in the lower third (`Alignment=2, MarginV=320`).

5. **Video Editing & Dual Thumbnails**:
   - Single-pass crops and renders vertical 9:16 (`1080x1920`) using Gyan.FFmpeg.
   - Generates **dual thumbnails**:
     - `thumbnail_{i}_clean.jpg`: High-quality clean video frame for custom editing in **Canva Pro**.
     - `thumbnail_{i}.jpg`: Ready-to-post thumbnail with burned hook banner.

6. **YouTube Shorts & Instagram Reels Publishing**:
   - Uploads directly to YouTube Shorts as **Private Drafts** (Category 20 Gaming with `#Shorts` and custom thumbnails).
   - Supports 1-click **Instagram Reels** publishing via Direct Login (`instagrapi`) or official Meta Graph API (Resumable Upload).

7. **Streamlit Web Dashboard**:
   - Live execution monitor with thread-safe auto-refreshing worker status.
   - Video player and gallery for all generated viral clips.
   - 1-Click **"📤 Post to YouTube Shorts"** and **"📸 Post to Instagram Reels"** buttons.

---

## 📁 Output Folder Structure

Every generated clip is stored in `Output_Clips/<VideoID>_Clip<N>_<Title>/`:
```
├── clip_1.mp4                 # 1080x1920 Vertical Short (>30s) with burned hook banner & subtitles
├── clip_1.srt                 # Accurate timestamped SRT subtitle file
├── thumbnail_1_clean.jpg      # Clean video frame for Canva Pro editing
├── thumbnail_1.jpg            # Ready-to-post thumbnail with hook banner
├── SEO_metadata.txt           # Copy-paste title, caption, hashtags, and tags
└── metadata.json              # Structured metadata for automation and dashboard
```

---

## 🚀 Quick Start

### 1. Launch the Web Dashboard
Double-click `Start_Clipper.bat` or run:
```powershell
streamlit run app.py
```
Open [http://localhost:8501](http://localhost:8501) in your browser.

### 2. Run via Command Line
```powershell
python run_real_clip.py "https://youtu.be/wEGdTkRL-uI?si=ZdQkAe9QdGajgsQM"
```

### 3. Authenticate YouTube
Double-click `Login_CLI.bat` or run:
```powershell
python login_cli.py
```

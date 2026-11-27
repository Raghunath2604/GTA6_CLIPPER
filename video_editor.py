"""
Video Editor & Dual Thumbnail Generator for GTA6 Viral Clipper Pro.
Handles:
1. >30-second trimming and 9:16 vertical cropping (1080x1920).
2. Clean text-free screenshot at 5-second mark for Canva Pro templates.
3. Top hook banner generation (bold ALL-CAPS white-box/black-text).
4. Subtitle burning (bold yellow-and-black gaming style in lower third).
5. Ready-to-post thumbnail with hook banner included.
"""

import os
import time
import shutil
import threading
from typing import Optional
from PIL import Image, ImageDraw, ImageFont

from ffmpeg_utils import run_ffmpeg, detect_best_h264_encoder
from logger import get_logger

log = get_logger("video_editor")

def create_hook_banner_overlay(
    text: str,
    output_path: str,
    width: int = 1080,
    height: int = 1920
) -> str:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    banner_text = text.upper().strip()

    font = None
    font_candidates = [
        "arialbd.ttf",
        "impact.ttf",
        "segui_sb.ttf",
        "calibrib.ttf",
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\impact.ttf"
    ]
    font_size = 54
    for fc in font_candidates:
        try:
            font = ImageFont.truetype(fc, font_size)
            break
        except Exception:
            continue
    if not font:
        font = ImageFont.load_default()

    words = banner_text.split() or ["WATCH", "THIS", "MOMENT"]
    lines = []
    curr_line = []
    for w in words:
        curr_line.append(w)
        test_str = " ".join(curr_line)
        bbox = draw.textbbox((0, 0), test_str, font=font)
        if (bbox[2] - bbox[0]) > (width - 160):
            if len(curr_line) > 1:
                curr_line.pop()
                lines.append(" ".join(curr_line))
                curr_line = [w]
            else:
                lines.append(test_str)
                curr_line = []
    if curr_line:
        lines.append(" ".join(curr_line))

    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_text_height = sum(line_heights) + (len(lines) - 1) * 12
    max_line_width = max(line_widths) if line_widths else 300

    pad_x = 36
    pad_y = 20
    box_w = max_line_width + pad_x * 2
    box_h = total_text_height + pad_y * 2

    box_top = 220
    box_left = (width - box_w) // 2
    box_right = box_left + box_w
    box_bottom = box_top + box_h

    shadow_offset = 8
    draw.rounded_rectangle(
        [box_left + shadow_offset, box_top + shadow_offset, box_right + shadow_offset, box_bottom + shadow_offset],
        radius=18,
        fill=(0, 0, 0, 160)
    )

    draw.rounded_rectangle(
        [box_left, box_top, box_right, box_bottom],
        radius=18,
        fill=(255, 255, 255, 250),
        outline=(20, 20, 20, 255),
        width=4
    )

    curr_y = box_top + pad_y
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        text_x = (width - text_w) // 2
        draw.text((text_x, curr_y), line, fill=(0, 0, 0, 255), font=font)
        curr_y += line_heights[i] + 12

    img.save(output_path, "PNG")
    return output_path

def render_viral_clip(
    source_video_path: str,
    start_sec: float,
    duration_sec: float,
    output_clip_path: str,
    hook_banner: str,
    clean_thumbnail_path: str,
    post_thumbnail_path: str,
    srt_path: Optional[str] = None,
    burn_banner: bool = True,
    burn_subtitles: bool = True
) -> bool:
    temp_dir = os.path.dirname(os.path.abspath(output_clip_path))
    os.makedirs(temp_dir, exist_ok=True)

    vf_crop = (
        "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
        "fps=30,setpts=PTS-STARTPTS"
    )

    thumb_sec = 5.0 if duration_sec >= 8.0 else max(0.5, duration_sec / 2.0)
    clean_thumb_args = [
        "-y",
        "-loglevel", "error",
        "-ss", f"{start_sec + thumb_sec:.3f}",
        "-i", source_video_path,
        "-vf", "crop=ih*9/16:ih:(iw-ih*9/16)/2:0,scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2",
        "-vframes", "1",
        "-update", "1",
        "-q:v", "2",
        clean_thumbnail_path
    ]
    run_ffmpeg(clean_thumb_args, timeout=60)
    log.info(f"Exported Canva clean frame at {thumb_sec:.1f}s: {clean_thumbnail_path}")

    unique_suffix = f"{os.getpid()}_{threading.get_ident()}"
    banner_png_path = os.path.join(temp_dir, f"temp_banner_{unique_suffix}.png")
    if hook_banner and burn_banner:
        create_hook_banner_overlay(hook_banner, banner_png_path)
        if os.path.exists(clean_thumbnail_path):
            try:
                base_img = Image.open(clean_thumbnail_path).convert("RGBA")
                banner_img = Image.open(banner_png_path).convert("RGBA")
                composed = Image.alpha_composite(base_img, banner_img)
                composed.convert("RGB").save(post_thumbnail_path, "JPEG", quality=95)
                log.info(f"Created ready-to-post thumbnail: {post_thumbnail_path}")
            except Exception as e:
                log.warning(f"Error compositing post thumbnail: {e}")
                shutil.copyfile(clean_thumbnail_path, post_thumbnail_path)
    else:
        if os.path.exists(clean_thumbnail_path):
            shutil.copyfile(clean_thumbnail_path, post_thumbnail_path)

    input_files = ["-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}", "-i", source_video_path]
    filters = [f"[0:v]{vf_crop}[vcrop]"]
    current_v = "[vcrop]"

    if burn_banner and os.path.exists(banner_png_path):
        input_files.extend(["-i", banner_png_path])
        filters.append(f"{current_v}[1:v]overlay=0:0:eof_action=repeat[vbanner]")
        current_v = "[vbanner]"

    temp_burn_ass = os.path.join(temp_dir, f"temp_burn_{unique_suffix}.ass")
    if burn_subtitles and srt_path and os.path.exists(srt_path):
        try:
            _convert_srt_to_vertical_ass(srt_path, temp_burn_ass)
            ass_escaped = (
                temp_burn_ass.replace("\\", "/")
                .replace(":", "\\:")
                .replace("'", "\\'")
                .replace("[", "\\[")
                .replace("]", "\\]")
            )
            filters.append(f"{current_v}ass='{ass_escaped}'[vsubs]")
            current_v = "[vsubs]"
        except Exception as e:
            log.warning(f"Warning preparing subtitle file for burn: {e}")

    t0 = time.time()
    vcodec_args = detect_best_h264_encoder()
    filter_str = ";".join(filters)

    def _build_render_args(enc_args):
        return [
            "-threads", "0",
            "-y",
            *input_files,
            "-filter_complex", filter_str,
            "-map", current_v,
            "-map", "0:a?",
            *enc_args,
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            output_clip_path
        ]

    log.info(f"Single-pass rendering 9:16 video ({duration_sec:.1f}s) from {start_sec:.1f}s using {vcodec_args[1]}...")
    res = run_ffmpeg(_build_render_args(vcodec_args), timeout=600)
    if res.returncode != 0 and vcodec_args[1] != "libx264":
        log.warning(f"GPU encoder {vcodec_args[1]} failed on filter chain; retrying with CPU libx264...")
        res = run_ffmpeg(_build_render_args(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "21"]), timeout=600)

    render_ok = res.returncode == 0
    if not render_ok:
        log.warning(f"Overlay filter render failed ({res.stderr[-300:]}); falling back to clean 9:16 crop...")
        fallback_args = [
            "-threads", "0", "-y",
            "-ss", f"{start_sec:.3f}", "-t", f"{duration_sec:.3f}",
            "-i", source_video_path,
            "-vf", vf_crop,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "21",
            "-c:a", "aac", "-b:a", "192k", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            output_clip_path
        ]
        fb_res = run_ffmpeg(fallback_args, timeout=600)
        render_ok = fb_res.returncode == 0
    else:
        log.info(f"Rendered {os.path.basename(output_clip_path)} in {time.time() - t0:.1f}s")

    for tmp in [banner_png_path, temp_burn_ass]:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

    return render_ok and os.path.exists(output_clip_path) and os.path.getsize(output_clip_path) > 1000


def _srt_ts_to_ass(ts: str) -> str:
    """Convert HH:MM:SS,mmm to H:MM:SS.cc for ASS dialogue lines."""
    ts = ts.strip().replace(",", ".")
    parts = ts.split(":")
    if len(parts) != 3:
        return "0:00:00.00"
    h = int(parts[0])
    m = int(parts[1])
    sec_parts = parts[2].split(".")
    s = int(sec_parts[0])
    ms = int((sec_parts[1] + "000")[:3]) if len(sec_parts) > 1 else 0
    return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"


def _convert_srt_to_vertical_ass(srt_path: str, ass_path: str) -> None:
    """Convert SRT cues to a crisp 1080x1920 vertical gaming ASS subtitle file."""
    with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read().strip()

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: ViralYellow,Arial,58,&H0000FFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,320,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    blocks = raw.replace("\r\n", "\n").split("\n\n")
    for blk in blocks:
        lines = [ln.strip() for ln in blk.split("\n") if ln.strip()]
        if len(lines) < 2:
            continue
        time_idx = next((idx for idx, ln in enumerate(lines) if "-->" in ln), -1)
        if time_idx == -1 or time_idx + 1 >= len(lines):
            continue
        t_parts = lines[time_idx].split("-->")
        if len(t_parts) != 2:
            continue
        start_ass = _srt_ts_to_ass(t_parts[0])
        end_ass = _srt_ts_to_ass(t_parts[1])
        text = " ".join(lines[time_idx + 1:]).upper()
        text = text.replace("{", "(").replace("}", ")").replace("\\", "")
        events.append(f"Dialogue: 0,{start_ass},{end_ass},ViralYellow,,0,0,0,,{text}")

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(events) + "\n")

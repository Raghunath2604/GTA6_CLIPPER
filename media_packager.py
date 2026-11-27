"""
Media packager for GTA6 Viral Clipper Pro.
Organizes output clips into organized folders with:
- clip_{i}.mp4 (vertical 9:16 video)
- thumbnail_{i}_clean.jpg (Canva Pro clean frame)
- thumbnail_{i}.jpg (ready-to-post thumbnail with hook banner)
- clip_{i}.srt (subtitles file)
- SEO_metadata.txt (Title, description, tags, hashtags)
- metadata.json (Structured metadata for dashboard and YouTube upload)
"""

import os
import re
import json
import shutil
from typing import Dict, Any, List, Optional

def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "_", name)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned[:50].strip("._")

def package_clip(
    output_base_dir: str,
    vid_id: str,
    clip_index: int,
    clip_info: Dict[str, Any],
    rendered_mp4_path: str,
    clean_thumb_path: str,
    post_thumb_path: str,
    srt_path: Optional[str] = None,
    stream_title: str = "",
    channel_name: str = ""
) -> str:
    title = clip_info.get("title", f"Clip_{clip_index}")
    folder_slug = sanitize_filename(f"{vid_id}_Clip{clip_index}_{title}")
    clip_folder = os.path.join(output_base_dir, folder_slug)
    os.makedirs(clip_folder, exist_ok=True)

    dest_mp4 = os.path.join(clip_folder, f"clip_{clip_index}.mp4")
    if os.path.exists(rendered_mp4_path):
        shutil.copyfile(rendered_mp4_path, dest_mp4)

    dest_clean_thumb = os.path.join(clip_folder, f"thumbnail_{clip_index}_clean.jpg")
    if os.path.exists(clean_thumb_path):
        shutil.copyfile(clean_thumb_path, dest_clean_thumb)

    dest_post_thumb = os.path.join(clip_folder, f"thumbnail_{clip_index}.jpg")
    if os.path.exists(post_thumb_path):
        shutil.copyfile(post_thumb_path, dest_post_thumb)

    dest_srt = os.path.join(clip_folder, f"clip_{clip_index}.srt")
    if srt_path and os.path.exists(srt_path):
        shutil.copyfile(srt_path, dest_srt)

    seo_txt_path = os.path.join(clip_folder, "SEO_metadata.txt")
    raw_tags = clip_info.get("tags")
    if not raw_tags:
        text_for_tags = " ".join([
            str(clip_info.get("caption", "") or ""),
            str(clip_info.get("description", "") or ""),
            str(clip_info.get("seo_description", "") or ""),
        ])
        extracted_tags = re.findall(r"#([A-Za-z0-9_]+)", text_for_tags)
        raw_tags = extracted_tags + [
            "Gaming", "Shorts", "Trending", "Viral",
            clip_info.get("trigger_type", "MOMENT")
        ]
    tags = []
    seen_tags = set()
    for t in raw_tags:
        clean_t = str(t).lstrip("#").strip()
        if clean_t and clean_t.lower() not in seen_tags:
            seen_tags.add(clean_t.lower())
            tags.append(clean_t)
    seo_content = f"""=== YOUTUBE SHORTS SEO METADATA ===
[TITLE]
{clip_info.get('title', '')}

[HOOK BANNER]
{clip_info.get('hook_banner', '')}

[CAPTION (FOR TIKTOK / INSTAGRAM REELS / SHORTS)]
{clip_info.get('caption', '')}

[YOUTUBE SHORTS DESCRIPTION]
{clip_info.get('description', '')}

[TAGS]
{', '.join(tags)}

[CATEGORY]
20 (Gaming)

[METRICS]
Viral Potential Score : {clip_info.get('viral_score', 8)}/10
Trigger Category      : {clip_info.get('trigger_type', 'GAMING')}
"""
    with open(seo_txt_path, "w", encoding="utf-8") as f:
        f.write(seo_content)

    meta_json_path = os.path.join(clip_folder, "metadata.json")
    meta_data = {
        "video_id": vid_id,
        "clip_index": clip_index,
        "title": clip_info.get("title", ""),
        "hook_banner": clip_info.get("hook_banner", ""),
        "caption": clip_info.get("caption", ""),
        "description": clip_info.get("description", ""),
        "viral_score": clip_info.get("viral_score", 8),
        "trigger_type": clip_info.get("trigger_type", "GAMING_MOMENT"),
        "start_seconds": clip_info.get("start_seconds", 0.0),
        "end_seconds": clip_info.get("end_seconds", 35.0),
        "stream_title": stream_title,
        "channel_name": channel_name,
        "tags": tags,
        "mp4_file": f"clip_{clip_index}.mp4",
        "clean_thumbnail": f"thumbnail_{clip_index}_clean.jpg",
        "post_thumbnail": f"thumbnail_{clip_index}.jpg",
        "srt_file": f"clip_{clip_index}.srt" if os.path.exists(dest_srt) else None
    }
    with open(meta_json_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, indent=2)

    print(f"[Packager] Packaged Clip #{clip_index} into: {clip_folder}")
    return clip_folder

def cleanup_temporary_stream_files(file_paths: List[Optional[str]]) -> None:
    for p in file_paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

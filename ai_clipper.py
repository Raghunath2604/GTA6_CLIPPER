"""
Senior Viral Clipper AI integration with Google GenAI SDK.
Cross-references live chat hotspots with multimodal video analysis to extract
high-impact shorts (>30s) using Pydantic structured outputs and resilient retries.
"""

import time
import random
from typing import List, Optional
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from chat_analyzer import ChatHotspot
from logger import get_logger

log = get_logger("ai_clipper")


class ViralClip(BaseModel):
    start_seconds: float = Field(description="Exact start time in seconds, starting 5-8 seconds before the climax")
    end_seconds: float = Field(description="Exact end time in seconds, exactly matching the requested clip duration")
    viral_score: int = Field(description="Viral potential score from 1 to 10", ge=1, le=10)
    trigger_type: str = Field(description="Category: LOBBY_BEEF, INSANE_JUKE, SCREAM_JUMPSCARE, CHAT_EXPLOSION, GLITCH_FAIL")
    hook_banner: str = Field(description="3-5 word high-impact ALL-CAPS hook text for top banner, e.g. HE DID NOT SURVIVE")
    title: str = Field(description="High-CTR YouTube Shorts title under 60 characters")
    caption: str = Field(description="Short caption with hashtags like #GTA6 #gaming #livestream #viral #shorts")
    description: str = Field(description="Keyword-rich SEO description for YouTube Shorts algorithm")


class ViralAnalysisResult(BaseModel):
    clips: List[ViralClip] = Field(description="List of top viral clips extracted from the stream or video")
    analysis_summary: str = Field(description="Brief overview of stream highlights and detected moments")


def sanitize_clips(
    clips: List[ViralClip],
    video_duration: float,
    target_duration: float,
    max_clips: int
) -> List[ViralClip]:
    """Clamp timestamps, enforce >30s target duration, clean text fields, and drop overlapping slices."""
    dur = max(30.0, float(target_duration))
    safe_vid_dur = max(dur, float(video_duration or dur))
    max_start = max(0.0, safe_vid_dur - dur)

    cleaned: List[ViralClip] = []
    for clip in clips:
        start = min(max(0.0, float(clip.start_seconds)), max_start)
        # Skip clips that heavily overlap an already chosen clip when multiple clips exist
        if any(abs(start - c.start_seconds) < (dur * 0.4) for c in cleaned):
            continue
        end = round(min(safe_vid_dur, start + dur), 1)
        if end - start < 30.0 and safe_vid_dur >= 30.0:
            start = max(0.0, safe_vid_dur - 30.0)
            end = round(safe_vid_dur, 1)

        hook = " ".join((clip.hook_banner or "WATCH THIS MOMENT").upper().split()[:6])
        title = (clip.title or "Insane Stream Moment #Shorts").strip()[:100]
        caption = (clip.caption or f"{title} #Gaming #Shorts #Viral").strip()[:2200]
        desc = (clip.description or f"{title}\n\n{caption}").strip()[:5000]

        cleaned.append(ViralClip(
            start_seconds=round(start, 1),
            end_seconds=end,
            viral_score=max(1, min(10, int(clip.viral_score))),
            trigger_type=(clip.trigger_type or "CHAT_EXPLOSION").strip()[:40],
            hook_banner=hook,
            title=title,
            caption=caption,
            description=desc
        ))
        if len(cleaned) >= max_clips:
            break

    return cleaned


def _generate_fallback_clips(
    hotspots: List[ChatHotspot],
    video_duration: float,
    count: int,
    clip_duration: float = 35.0
) -> ViralAnalysisResult:
    clips: List[ViralClip] = []
    scenarios = [
        ("INSANE_CLUTCH", "WATCH UNTIL THE END", "Unbelievable Moment Caught Live on Stream!", "#gaming #livestream #clutch #viral #shorts"),
        ("SCREAM_JUMPSCARE", "BRO WAS NOT READY", "Funniest Live Stream Reaction Ever!", "#gaming #reaction #funny #viral #shorts"),
        ("LOBBY_BEEF", "THINGS GOT OUT OF HAND", "Heated Stream Moment Caught Live!", "#gaming #drama #streamer #funny #shorts"),
        ("CHAT_EXPLOSION", "CHAT WENT COMPLETELY INSANE", "Everyone in Chat Lost It At This Moment!", "#gaming #chat #viral #clips #shorts"),
        ("GLITCH_FAIL", "HOW IS THIS POSSIBLE", "Impossible Fail Caught Live on Stream!", "#gaming #fail #glitch #clips #shorts")
    ]

    total = max(1, min(count, len(scenarios)))
    clip_dur = max(30.0, float(clip_duration))

    for i in range(total):
        if i < len(hotspots) and hotspots[i].timestamp_seconds > 5.0:
            peak = hotspots[i].timestamp_seconds
        else:
            peak = max(20.0, (i + 1) * (video_duration / (total + 1)))

        start = max(0.0, peak - 6.0)
        end = start + clip_dur
        if end > video_duration and video_duration >= clip_dur:
            start = max(0.0, video_duration - clip_dur)
            end = video_duration

        trigger, hook, title, tags = scenarios[i % len(scenarios)]
        clips.append(ViralClip(
            start_seconds=round(start, 1),
            end_seconds=round(start + clip_dur, 1),
            viral_score=9 - i,
            trigger_type=trigger,
            hook_banner=hook,
            title=title,
            caption=f"{title} {tags}",
            description=f"{title}\n\nWatch this viral gaming moment captured live! Like and subscribe for more daily stream clips and highlights.\n\n{tags}"
        ))

    safe_clips = sanitize_clips(clips, video_duration, clip_dur, total)
    return ViralAnalysisResult(
        clips=safe_clips,
        analysis_summary=f"Extracted {len(safe_clips)} high-retention {clip_dur:.0f}s viral moments based on chat explosion hotspots."
    )


def analyze_stream_for_viral_clips(
    video_path: str,
    hotspots: List[ChatHotspot],
    video_duration: float,
    clips_per_stream: int = 2,
    clip_duration_seconds: int = 35,
    api_key: Optional[str] = None
) -> ViralAnalysisResult:
    target_duration = max(30, int(clip_duration_seconds))

    if not api_key:
        log.info("No Gemini API key provided; using hotspot heuristic fallback.")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    if not genai:
        log.warning("google-genai library not available; using fallback.")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    client = genai.Client(api_key=api_key)
    uploaded_file = None

    try:
        log.info(f"Uploading {video_path} to Gemini File API...")
        uploaded_file = client.files.upload(file=video_path)
        log.info(f"Uploaded as '{uploaded_file.name}'. Awaiting ACTIVE state...")

        start_wait = time.time()
        while True:
            file_info = client.files.get(name=uploaded_file.name)
            state_str = str(file_info.state)
            if "ACTIVE" in state_str:
                log.info(f"Gemini file ready in {int(time.time() - start_wait)}s.")
                break
            elif "FAILED" in state_str:
                raise RuntimeError(f"Gemini File processing failed: {getattr(file_info, 'error', 'Unknown error')}")

            if time.time() - start_wait > 300:
                raise TimeoutError("Gemini file processing timed out after 5 minutes.")
            time.sleep(3)

        hotspots_text = "\n".join([
            f"- Timestamp {h.formatted_time} ({h.timestamp_seconds:.1f}s): Message spike score {h.spike_score:.1f} ({h.message_count} msgs in 10s)"
            for h in hotspots
        ])

        prompt = f"""
You are a Senior Viral Content Editor and Algorithm Optimization Engineer specializing in gaming (GTA 6, live stream highlights, reactions) and short-form content for YouTube Shorts, Instagram Reels, and TikTok.

Analyze this video accurately (including game, streamer, and dialogue) and cross-reference the following LIVE CHAT EXPLOSION HOTSPOTS:
{hotspots_text}

Video Duration: {video_duration:.1f} seconds.
Target Clip Duration: EXACTLY {target_duration} SECONDS (>30 seconds format).

YOUR OBJECTIVE:
Select exactly {clips_per_stream} non-overlapping viral moments with the absolute highest retention and watch-time potential.
Specifically hunt for:
1. CHARACTER FIGHTS / LOBBY BEEF: Heated arguments, hilarious accusations, trolling, voice-chat chaos.
2. CHAT EXPLOSIONS: Moments where viewers went crazy over an unexpected event.
3. INSANE CLUTCHES / HIGHLIGHTS: Wild escapes, gameplay stunts, secret reveals, crazy wins.
4. SCREAMS / REACTIONS: Loud reactions, rage moments, terrifying surprises, funny glitches.

CRITICAL RULES FOR EACH CLIP:
- EXACT DURATION: Each clip must be EXACTLY {target_duration} seconds long (end_seconds = start_seconds + {target_duration}.0).
- BOUNDS: start_seconds must be >= 0 and end_seconds <= {video_duration:.1f}.
- PACING & SETUP: start_seconds MUST start 5 to 8 seconds BEFORE the climax occurs so the viewer gets context and suspense buildup.
- HOOK BANNER: 3 to 5 words ALL-CAPS, high curiosity, e.g. "HE REGRETTED THIS INSTANTLY" or "NOBODY EXPECTED THIS".
- TITLE: High-CTR YouTube Shorts title under 60 characters matching the actual game/creator in the video.
- CAPTION: Short, catchy caption with 3-5 relevant hashtags matching the creator/game + #gaming #shorts.
- DESCRIPTION: Keyword-rich SEO description crafted to rank in YouTube Shorts search and recommendations.
"""

        candidate_models = ["gemini-3.8-flash", "gemini-3-flash-preview", "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.5-flash"]
        response = None
        last_model_err = None

        for model_name in candidate_models:
            for attempt in range(2):
                try:
                    log.info(f"Requesting structured clip analysis from {model_name} (attempt {attempt + 1})...")
                    response = client.models.generate_content(
                        model=model_name,
                        contents=[uploaded_file, prompt],
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=ViralAnalysisResult,
                            temperature=0.3
                        )
                    )
                    if response and response.text:
                        break
                except Exception as m_err:
                    last_model_err = m_err
                    err_s = str(m_err)
                    is_transient = any(code in err_s for code in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "timed out"))
                    log.warning(f"Model {model_name} failed: {err_s[:180]}")
                    if is_transient and attempt == 0:
                        time.sleep(1.5 + random.uniform(0.2, 0.8))
                        continue
                    break
            if response and response.text:
                break

        if not response or not response.text:
            raise RuntimeError(f"All candidate Gemini models failed. Last error: {last_model_err}")

        parsed_result = ViralAnalysisResult.model_validate_json(response.text)
        sanitized = sanitize_clips(parsed_result.clips, video_duration, target_duration, clips_per_stream)
        if not sanitized:
            return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)
        parsed_result.clips = sanitized
        log.info(f"Extracted and validated {len(parsed_result.clips)} viral clips from Gemini.")
        return parsed_result

    except Exception as e:
        log.warning(f"Gemini multimodal analysis fell back to heuristics: {e}")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    finally:
        if uploaded_file:
            try:
                client.files.delete(name=uploaded_file.name)
                log.info(f"Cleaned up remote Gemini file '{uploaded_file.name}'.")
            except Exception as del_err:
                log.warning(f"Could not delete remote Gemini file {uploaded_file.name}: {del_err}")

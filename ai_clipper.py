"""
Senior Viral Clipper AI integration with Google GenAI SDK (gemini-2.5-flash).
Cross-references live chat hotspots with multimodal video analysis to extract
high-impact shorts (>30s) using Pydantic structured outputs.
"""

import os
import time
import json
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

try:
    from google import genai
    from google.genai import types
except ImportError:
    genai = None
    types = None

from chat_analyzer import ChatHotspot

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
        
    return ViralAnalysisResult(
        clips=clips,
        analysis_summary=f"Extracted {len(clips)} high-retention {clip_dur:.0f}s viral moments based on chat explosion hotspots."
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
        print("[AI Clipper] No Gemini API key provided. Using intelligent hotspot fallback generator.")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    if not genai:
        print("[AI Clipper] google-genai library not available. Using fallback.")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    client = genai.Client(api_key=api_key)
    uploaded_file = None

    try:
        print(f"[AI Clipper] Uploading {video_path} to Gemini File API...")
        uploaded_file = client.files.upload(file=video_path)
        print(f"[AI Clipper] Uploaded as '{uploaded_file.name}'. Awaiting processing state ACTIVE...")

        start_wait = time.time()
        while True:
            file_info = client.files.get(name=uploaded_file.name)
            state_str = str(file_info.state)
            if "ACTIVE" in state_str:
                print(f"[AI Clipper] Video ready for multimodal analysis in {int(time.time() - start_wait)}s.")
                break
            elif "FAILED" in state_str:
                raise RuntimeError(f"Gemini File processing failed: {file_info.error if hasattr(file_info, 'error') else 'Unknown error'}")
            
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
Select exactly {clips_per_stream} viral moments with the absolute highest retention and watch-time potential.
Specifically hunt for:
1. CHARACTER FIGHTS / LOBBY BEEF: Heated arguments, hilarious accusations, trolling, voice-chat chaos.
2. CHAT EXPLOSIONS: Moments where viewers went crazy over an unexpected event.
3. INSANE CLUTCHES / HIGHLIGHTS: Wild escapes, gameplay stunts, secret reveals, crazy wins.
4. SCREAMS / REACTIONS: Loud reactions, rage moments, terrifying surprises, funny glitches.

CRITICAL RULES FOR EACH CLIP:
- EXACT DURATION: Each clip must be EXACTLY {target_duration} seconds long (end_seconds = start_seconds + {target_duration}.0).
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
            try:
                print(f"[AI Clipper] Sending prompt to {model_name} with structured output schema...")
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
                print(f"[AI Clipper] Model {model_name} returned error: {m_err}. Trying next candidate...")
                last_model_err = m_err

        if not response or not response.text:
            raise RuntimeError(f"All candidate Gemini models failed. Last error: {last_model_err}")

        parsed_result = ViralAnalysisResult.model_validate_json(response.text)
        print(f"[AI Clipper] Successfully extracted {len(parsed_result.clips)} viral clips from Gemini!")
        return parsed_result

    except Exception as e:
        print(f"[AI Clipper] Exception during Gemini multimodal analysis: {e}. Falling back to hotspot heuristics.")
        return _generate_fallback_clips(hotspots, video_duration, clips_per_stream, target_duration)

    finally:
        if uploaded_file:
            try:
                print(f"[AI Clipper] Cleaning up remote file '{uploaded_file.name}' from Google servers...")
                client.files.delete(name=uploaded_file.name)
                print("[AI Clipper] Remote file deleted successfully.")
            except Exception as del_err:
                print(f"[AI Clipper] Warning cleaning up {uploaded_file.name}: {del_err}")

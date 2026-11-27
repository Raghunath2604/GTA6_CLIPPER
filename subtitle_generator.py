"""
Local speech-to-text transcription and punchy Shorts SRT subtitle generator.
Uses FFmpeg 16kHz PCM extraction + faster-whisper (base, int8) with 4-5 word phrasing.
"""

import os
import wave
import threading
import numpy as np
from typing import List, Tuple, Optional

from ffmpeg_utils import run_ffmpeg, get_media_duration
from logger import get_logger

log = get_logger("subtitle_generator")

_WHISPER_LOCK = threading.Lock()
_CACHED_MODEL = None


def _format_srt_timestamp(seconds: float) -> str:
    total_ms = int(round(max(0.0, seconds) * 1000))
    hours, rem = divmod(total_ms, 3600000)
    mins, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def split_words_into_short_cues(
    word_items: List[Tuple[float, float, str]],
    max_words: int = 5,
    max_cue_sec: float = 2.4
) -> List[Tuple[int, float, float, str]]:
    """Group word-level timestamps into fast-paced 1-2 line vertical Shorts cues."""
    cues: List[Tuple[int, float, float, str]] = []
    buf: List[Tuple[float, float, str]] = []
    cue_idx = 1

    for w_start, w_end, w_txt in word_items:
        clean = w_txt.strip().upper()
        if not clean:
            continue
        buf.append((w_start, w_end, clean))
        span = buf[-1][1] - buf[0][0]
        ends_sentence = clean.endswith((".", "!", "?"))
        if len(buf) >= max_words or span >= max_cue_sec or (ends_sentence and len(buf) >= 2):
            cues.append((cue_idx, buf[0][0], max(buf[-1][1], buf[0][0] + 0.3), " ".join(x[2] for x in buf)))
            cue_idx += 1
            buf = []

    if buf:
        cues.append((cue_idx, buf[0][0], max(buf[-1][1], buf[0][0] + 0.3), " ".join(x[2] for x in buf)))

    return cues


def split_segment_proportionally(
    start: float,
    end: float,
    text: str,
    max_words: int = 5
) -> List[Tuple[float, float, str]]:
    """Fallback splitter when word timestamps are unavailable for a segment."""
    words = text.strip().upper().split()
    if not words:
        return []
    chunks = [words[i:i + max_words] for i in range(0, len(words), max_words)]
    total_dur = max(0.3, end - start)
    step = total_dur / len(chunks)
    return [
        (round(start + i * step, 3), round(start + (i + 1) * step, 3), " ".join(ch))
        for i, ch in enumerate(chunks)
    ]


def _generate_synthetic_gaming_subtitles(duration: float, srt_path: str, hook_banner: str = "") -> None:
    cues = [
        ("WAIT FOR THIS MOMENT!", 0.8, 3.2),
        ("BRO WHAT ARE YOU DOING?!", 3.6, 6.5),
        ("NO WAY THAT JUST HAPPENED!", 6.9, 10.2),
        (f"{hook_banner if hook_banner else 'WATCH TILL THE END!'}", 10.6, 14.5),
        ("EVERYONE SAW THAT LIVE!", 15.0, 18.5),
        ("HOW DID HE SURVIVE THAT?!", 19.0, 23.2),
        ("CHAT DID YOU SEE THAT?!", 23.8, 27.5),
        ("INSANE STREAM MOMENT!", 28.0, min(duration, 32.5)),
        ("DROP A LIKE AND SUBSCRIBE!", 33.0, min(duration, max(34.0, duration - 0.5)))
    ]
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, (text, start, end) in enumerate(cues, 1):
            if start >= duration:
                break
            end = min(end, duration)
            f.write(f"{idx}\n")
            f.write(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n")
            f.write(f"{text.upper()}\n\n")


def _get_whisper_model():
    global _CACHED_MODEL
    if _CACHED_MODEL is None:
        from faster_whisper import WhisperModel
        log.info("Loading local faster-whisper model (base, cpu, int8)...")
        _CACHED_MODEL = WhisperModel("base", device="cpu", compute_type="int8")
    return _CACHED_MODEL


def generate_subtitles_for_clip(
    clip_video_path: str,
    output_srt_path: str,
    hook_banner: str = "",
    api_key: Optional[str] = None,
    start_sec: float = 0.0,
    duration_sec: Optional[float] = None
) -> str:
    temp_wav = f"{output_srt_path}.temp16k.wav"
    try:
        try:
            import av
            orig_av_open = av.open
            def patched_av_open(*args, **kwargs):
                kwargs.pop("metadata_errors", None)
                return orig_av_open(*args, **kwargs)
            av.open = patched_av_open
        except Exception:
            pass

        ff_args = ["-y", "-loglevel", "error"]
        if start_sec and start_sec > 0:
            ff_args.extend(["-ss", f"{start_sec:.3f}"])
        if duration_sec and duration_sec > 0:
            ff_args.extend(["-t", f"{duration_sec:.3f}"])
        ff_args.extend([
            "-i", clip_video_path,
            "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            temp_wav
        ])

        res = run_ffmpeg(ff_args, timeout=120)
        audio_input = None
        if res.returncode == 0 and os.path.exists(temp_wav) and os.path.getsize(temp_wav) > 1024:
            with wave.open(temp_wav, "rb") as wf:
                raw = wf.readframes(wf.getnframes())
                if raw:
                    audio_input = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

        if audio_input is None or len(audio_input) < 1600:
            raise RuntimeError("No audio track or insufficient audio samples in clip slice.")

        with _WHISPER_LOCK:
            model = _get_whisper_model()
            segments, _ = model.transcribe(audio_input, beam_size=1, vad_filter=True, word_timestamps=True)
            word_items: List[Tuple[float, float, str]] = []
            fallback_cues: List[Tuple[int, float, float, str]] = []
            fb_idx = 1

            for seg in segments:
                words = getattr(seg, "words", None) or []
                if words:
                    for w in words:
                        word_items.append((float(w.start), float(w.end), str(w.word)))
                else:
                    for s_t, e_t, txt in split_segment_proportionally(seg.start, seg.end, seg.text):
                        fallback_cues.append((fb_idx, s_t, e_t, txt))
                        fb_idx += 1

        cues = split_words_into_short_cues(word_items) if word_items else fallback_cues

        if cues:
            with open(output_srt_path, "w", encoding="utf-8") as f:
                for idx, start, end, text in cues:
                    f.write(f"{idx}\n")
                    f.write(f"{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n")
                    f.write(f"{text}\n\n")
            log.info(f"Generated {len(cues)} punchy subtitle cues via faster-whisper.")
            return output_srt_path
    except Exception as e:
        log.warning(f"faster-whisper transcription fell back ({e}).")
    finally:
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
            except Exception:
                pass

    duration = float(duration_sec) if duration_sec else get_media_duration(clip_video_path)
    if duration <= 0:
        duration = 35.0
    _generate_synthetic_gaming_subtitles(duration, output_srt_path, hook_banner)
    return output_srt_path

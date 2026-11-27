"""
FFmpeg discovery, GPU encoder detection, and subprocess wrapper utilities.
"""

import os
import glob
import shutil
import threading
import subprocess
from typing import Optional, List, Dict, Any

_ENCODER_LOCK = threading.Lock()
_CACHED_H264_ENCODER: Optional[List[str]] = None


def find_ffmpeg_binary(binary_name: str = "ffmpeg.exe") -> str:
    """Locate ffmpeg/ffprobe binary on PATH or common Windows install locations."""
    bare = os.path.splitext(binary_name)[0]
    path_bin = shutil.which(binary_name) or shutil.which(bare)
    if path_bin:
        return path_bin

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        winget_pattern = os.path.join(
            local_app_data,
            "Microsoft", "WinGet", "Packages",
            "Gyan.FFmpeg*", "*", "bin", binary_name
        )
        matches = glob.glob(winget_pattern)
        if matches and os.path.exists(matches[0]):
            return matches[0]

        direct_winget = os.path.join(
            local_app_data,
            "Microsoft", "WinGet", "Links", binary_name
        )
        if os.path.exists(direct_winget):
            return direct_winget

    common_paths = [
        rf"C:\ffmpeg\bin\{binary_name}",
        rf"C:\Program Files\ffmpeg\bin\{binary_name}",
        rf"C:\ProgramData\chocolatey\bin\{binary_name}"
    ]
    for cp in common_paths:
        if os.path.exists(cp):
            return cp

    return binary_name


def get_ffmpeg_path() -> str:
    return find_ffmpeg_binary("ffmpeg.exe" if os.name == "nt" else "ffmpeg")


def get_ffprobe_path() -> str:
    return find_ffmpeg_binary("ffprobe.exe" if os.name == "nt" else "ffprobe")


def check_ffmpeg_health() -> Dict[str, Any]:
    """Run a quick preflight check verifying FFmpeg and FFprobe binaries are executable."""
    ff = get_ffmpeg_path()
    fp = get_ffprobe_path()
    ff_ok = bool(shutil.which(ff) or os.path.exists(ff))
    fp_ok = bool(shutil.which(fp) or os.path.exists(fp))
    return {
        "ready": ff_ok and fp_ok,
        "ffmpeg_path": ff,
        "ffprobe_path": fp,
    }


def detect_best_h264_encoder() -> List[str]:
    """Probe hardware H.264 encoders (NVENC -> QSV -> AMF) once and cache CPU fallback."""
    global _CACHED_H264_ENCODER
    with _ENCODER_LOCK:
        if _CACHED_H264_ENCODER is not None:
            return list(_CACHED_H264_ENCODER)

        ffmpeg_exe = get_ffmpeg_path()
        candidates = [
            ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "21", "-b:v", "0"],
            ["-c:v", "h264_qsv", "-preset", "faster", "-global_quality", "22"],
            ["-c:v", "h264_amf", "-quality", "speed", "-rc", "cqp", "-qp_i", "21", "-qp_p", "23"],
        ]
        for opt in candidates:
            try:
                res = subprocess.run(
                    [
                        ffmpeg_exe, "-hide_banner", "-loglevel", "error",
                        "-f", "lavfi", "-i", "color=c=black:s=720x1280:d=0.2",
                        *opt, "-f", "null", "-"
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=15
                )
                if res.returncode == 0:
                    _CACHED_H264_ENCODER = opt
                    return list(_CACHED_H264_ENCODER)
            except Exception:
                continue

        _CACHED_H264_ENCODER = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "21"]
        return list(_CACHED_H264_ENCODER)


def run_ffmpeg(args: List[str], timeout: Optional[int] = 600) -> subprocess.CompletedProcess:
    """Execute FFmpeg safely with utf-8 decoding and timeout protection."""
    ffmpeg_exe = get_ffmpeg_path()
    cmd = [ffmpeg_exe] + args
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace"
    )


def get_media_duration(file_path: str) -> float:
    """Return media duration in seconds using FFprobe, or 0.0 on failure."""
    if not file_path or not os.path.exists(file_path):
        return 0.0
    ffprobe_exe = get_ffprobe_path()
    cmd = [
        ffprobe_exe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        file_path
    ]
    try:
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        return float(res.stdout.strip())
    except Exception:
        return 0.0

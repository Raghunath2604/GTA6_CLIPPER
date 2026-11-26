"""
FFmpeg discovery and wrapper utilities for Windows.
"""

import os
import glob
import shutil
import subprocess
from typing import Optional, List

def find_ffmpeg_binary(binary_name: str = "ffmpeg.exe") -> str:
    path_bin = shutil.which(binary_name)
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
    return find_ffmpeg_binary("ffmpeg.exe")

def get_ffprobe_path() -> str:
    return find_ffmpeg_binary("ffprobe.exe")

def run_ffmpeg(args: List[str], timeout: Optional[int] = 300) -> subprocess.CompletedProcess:
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

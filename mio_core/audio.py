from __future__ import annotations

import json
import subprocess
from pathlib import Path

from mio_core.config import Settings

MAGIC = {
    ".flac": (b"fLaC",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".ogg": (b"OggS",),
    ".opus": (b"OggS",),
    ".wav": (b"RIFF",),
    ".m4a": (b"\x00\x00\x00",),
}


class AudioValidationError(ValueError):
    pass


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def defender_scan(path: Path, settings: Settings) -> None:
    if not settings.enable_defender_scan:
        return
    result = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Start-MpScan -ScanType CustomScan -ScanPath $args[0]",
            str(path),
        ],
        timeout=300,
    )
    if result.returncode != 0:
        raise AudioValidationError("Windows Defender 扫描未成功完成")


def inspect_audio(path: Path, settings: Settings) -> dict:
    suffix = path.suffix.lower()
    header = path.read_bytes()[:12]
    if suffix not in MAGIC or not any(header.startswith(prefix) for prefix in MAGIC[suffix]):
        raise AudioValidationError("文件头与音频扩展名不匹配")
    result = _run(
        [
            settings.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration:format_tags:stream=index,codec_type,codec_name:stream_tags",
            "-of",
            "json",
            str(path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise AudioValidationError(f"FFprobe 无法解析音频：{result.stderr[-400:]}")
    payload = json.loads(result.stdout)
    audio_streams = [
        stream
        for stream in payload.get("streams", [])
        if stream.get("codec_type") == "audio"
    ]
    if not audio_streams:
        raise AudioValidationError("文件中没有音频流")
    raw_tags = payload.get("format", {}).get("tags", {}) or {}
    tags = {
        str(key).lower(): str(value).replace("\x00", "")[:500]
        for key, value in raw_tags.items()
        if isinstance(key, str) and isinstance(value, (str, int, float))
    }
    has_cover = any(stream.get("codec_type") == "video" for stream in payload.get("streams", []))
    return {
        "title": tags.get("title"),
        "artist": tags.get("artist") or tags.get("album_artist"),
        "album": tags.get("album"),
        "track_number": _parse_track(tags.get("track")),
        "duration": float(payload.get("format", {}).get("duration") or 0),
        "has_cover": has_cover,
    }


def _parse_track(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value.split("/", 1)[0])
    except ValueError:
        return None


def extract_cover(audio_path: Path, output_path: Path, settings: Settings) -> bool:
    result = _run(
        [
            settings.ffmpeg_path,
            "-y",
            "-v",
            "error",
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-frames:v",
            "1",
            str(output_path),
        ],
        timeout=120,
    )
    return result.returncode == 0 and output_path.is_file() and output_path.stat().st_size > 0

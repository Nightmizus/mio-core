from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import HTTPException

ALLOWED_AUDIO_EXTENSIONS = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav"}
BLOCKED_EXTENSIONS = {
    ".7z",
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".hta",
    ".iso",
    ".jar",
    ".js",
    ".lnk",
    ".msi",
    ".ps1",
    ".rar",
    ".scr",
    ".vbs",
    ".zip",
}
WINDOWS_DEVICE_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def safe_filename(filename: str) -> str:
    if not filename or "\x00" in filename:
        raise HTTPException(status_code=400, detail="无效文件名")
    if Path(filename).is_absolute() or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="文件名不得包含路径")
    cleaned = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", filename).strip(" .")
    if not cleaned or cleaned.split(".", 1)[0].lower() in WINDOWS_DEVICE_NAMES:
        raise HTTPException(status_code=400, detail="Windows 保留文件名不可用")
    suffix = Path(cleaned).suffix.lower()
    if suffix in BLOCKED_EXTENSIONS or suffix not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="只接受受支持的音频文件")
    return cleaned[:240]


def contained_path(root: Path, *parts: str) -> Path:
    root = root.resolve()
    candidate = root.joinpath(*parts).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes configured root") from exc
    return candidate


def reject_reparse_points(path: Path, stop_at: Path) -> None:
    stop = stop_at.resolve()
    current = path.resolve(strict=False)
    while current != stop:
        if current.exists():
            if current.is_symlink():
                raise ValueError("symbolic links are not allowed")
            stat = os.lstat(current)
            file_attributes = getattr(stat, "st_file_attributes", 0)
            if file_attributes & 0x400:
                raise ValueError("Windows reparse points are not allowed")
        if current.parent == current:
            raise ValueError("path is outside configured root")
        current = current.parent


def slugify(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-")
    if not normalized:
        raise ValueError("cannot create a safe release slug")
    return normalized[:120]

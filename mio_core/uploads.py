from __future__ import annotations

import hashlib
import math
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mio_core.config import Settings
from mio_core.models import Upload, UploadState, now
from mio_core.path_security import contained_path, reject_reparse_points, safe_filename


def create_upload(
    db: Session,
    settings: Settings,
    user_id: str,
    filename: str,
    size: int,
    total_chunks: int,
    expected_sha256: str | None,
) -> Upload:
    if size > settings.max_file_size:
        raise HTTPException(status_code=413, detail="单文件不能超过 500 MiB")
    expected_chunks = math.ceil(size / settings.chunk_size)
    if total_chunks != expected_chunks:
        raise HTTPException(status_code=400, detail=f"分块数应为 {expected_chunks}")
    active_bytes = db.scalar(
        select(func.coalesce(func.sum(Upload.size), 0)).where(
            Upload.user_id == user_id,
            Upload.state.in_([UploadState.created, UploadState.uploading]),
        )
    )
    if active_bytes + size > settings.max_batch_size:
        raise HTTPException(status_code=413, detail="进行中的上传批次不能超过 5 GiB")
    upload = Upload(
        user_id=user_id,
        original_name=filename,
        safe_name=safe_filename(filename),
        size=size,
        total_chunks=total_chunks,
        expected_sha256=expected_sha256,
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)
    contained_path(settings.data_dir / "quarantine", upload.id).mkdir(parents=True, exist_ok=True)
    return upload


async def store_chunk(
    db: Session,
    settings: Settings,
    upload: Upload,
    index: int,
    chunk: UploadFile,
    expected_digest: str | None,
) -> None:
    if upload.state not in {UploadState.created, UploadState.uploading}:
        raise HTTPException(status_code=409, detail="上传已结束")
    if index < 0 or index >= upload.total_chunks:
        raise HTTPException(status_code=400, detail="分块序号越界")
    root = settings.data_dir / "quarantine"
    directory = contained_path(root, upload.id)
    reject_reparse_points(directory, root)
    target = contained_path(directory, f"{index:06d}.part")
    digest = hashlib.sha256()
    total = 0
    data = await chunk.read(settings.chunk_size + 1)
    if len(data) > settings.chunk_size:
        raise HTTPException(status_code=413, detail="分块不能超过 8 MiB")
    total += len(data)
    digest.update(data)
    if index < upload.total_chunks - 1 and total != settings.chunk_size:
        raise HTTPException(status_code=400, detail="非末尾分块必须恰好为 8 MiB")
    if expected_digest and digest.hexdigest().lower() != expected_digest.lower():
        raise HTTPException(status_code=400, detail="分块 SHA-256 不匹配")
    existed = target.exists()
    target.write_bytes(data)
    upload.state = UploadState.uploading
    if not existed:
        upload.received_chunks += 1
    upload.updated_at = now()
    db.commit()


def finalize_upload(db: Session, settings: Settings, upload: Upload) -> Upload:
    if upload.received_chunks != upload.total_chunks:
        raise HTTPException(
            status_code=409,
            detail=f"仍缺少 {upload.total_chunks - upload.received_chunks} 个分块",
        )
    root = settings.data_dir / "quarantine"
    directory = contained_path(root, upload.id)
    final_dir = contained_path(settings.data_dir / "uploads", upload.id)
    final_dir.mkdir(parents=True, exist_ok=True)
    target = contained_path(final_dir, upload.safe_name)
    digest = hashlib.sha256()
    written = 0
    with target.open("wb") as output:
        for index in range(upload.total_chunks):
            part = contained_path(directory, f"{index:06d}.part")
            if not part.is_file():
                raise HTTPException(status_code=409, detail=f"分块 {index} 缺失")
            with part.open("rb") as source:
                while data := source.read(1024 * 1024):
                    digest.update(data)
                    written += len(data)
                    output.write(data)
    actual = digest.hexdigest()
    if written != upload.size or (upload.expected_sha256 and actual != upload.expected_sha256):
        target.unlink(missing_ok=True)
        upload.state = UploadState.rejected
        upload.error = "文件大小或 SHA-256 校验失败"
        db.commit()
        raise HTTPException(status_code=400, detail=upload.error)
    duplicate = db.scalar(
        select(Upload).where(
            Upload.sha256 == actual,
            Upload.id != upload.id,
            Upload.state.in_([UploadState.quarantined, UploadState.validated]),
        )
    )
    if duplicate:
        target.unlink(missing_ok=True)
        upload.state = UploadState.rejected
        upload.error = "相同内容的音乐已上传"
        db.commit()
        raise HTTPException(status_code=409, detail=upload.error)
    upload.sha256 = actual
    upload.stored_path = str(target)
    upload.state = UploadState.quarantined
    upload.updated_at = now()
    db.commit()
    return upload

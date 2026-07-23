from __future__ import annotations

import socket
import time
from pathlib import Path

from sqlalchemy import select

from mio_core.audio import AudioValidationError, defender_scan, extract_cover, inspect_audio
from mio_core.config import get_settings
from mio_core.database import SessionLocal, initialize_database
from mio_core.git_pipeline import MusicPublisher, PipelineError
from mio_core.models import (
    Job,
    JobEvent,
    JobState,
    Publication,
    UploadState,
    User,
    now,
)
from mio_core.path_security import slugify

WORKER_ID = f"{socket.gethostname()}-{__import__('os').getpid()}"


def event(db, job: Job, message: str, public: bool = False) -> None:
    db.add(JobEvent(job_id=job.id, state=job.state.value, message=message, public=public))
    job.updated_at = now()
    db.commit()


def next_job(db) -> Job | None:
    job = db.scalar(
        select(Job)
        .where(
            Job.state.in_([JobState.analyzing, JobState.importing]),
            Job.claimed_by.is_(None),
        )
        .order_by(Job.created_at)
        .limit(1)
    )
    if not job:
        return None
    job.claimed_by = WORKER_ID
    db.commit()
    return job


def process_job(job_id: str) -> None:
    settings = get_settings()
    publisher = MusicPublisher(settings)
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job:
            return
        upload = job.upload
        try:
            if job.state == JobState.analyzing:
                path = Path(upload.stored_path or "")
                defender_scan(path, settings)
                metadata = inspect_audio(path, settings)
                upload.state = UploadState.validated
                job.metadata_json = metadata
                missing = [
                    field
                    for field in ("title", "artist", "album", "track_number")
                    if not metadata.get(field)
                ]
                cover = settings.data_dir / "uploads" / upload.id / "cover.jpg"
                if metadata.get("has_cover") and not extract_cover(path, cover, settings):
                    metadata["has_cover"] = False
                if not metadata.get("has_cover"):
                    missing.append("cover")
                job.required_fields = missing
                if missing:
                    job.state = JobState.awaiting_input
                    job.claimed_by = None
                    event(db, job, f"Mio 需要补充：{', '.join(missing)}")
                    return
                job.state = JobState.importing
                event(db, job, "音频验证完成，准备导入 Music Mizu")

            metadata = dict(job.metadata_json)
            cover = settings.data_dir / "uploads" / upload.id / "cover.jpg"
            with publisher.lock():
                worktree = publisher.prepare_worktree(job.id)
                try:
                    slug = publisher.import_track(worktree, upload, metadata, cover)
                    job.state = JobState.building
                    event(db, job, "正在构建并检查 Faircamp 站点")
                    publisher.build(worktree)
                    job.state = JobState.committing
                    event(db, job, "构建通过，正在生成提交")
                    user = db.get(User, job.user_id)
                    commit = publisher.commit(
                        worktree, job, user.username if user else job.user_id
                    )
                    job.commit_sha = commit
                    job.state = JobState.pushing
                    event(db, job, "正在推送 Music Mizu main 分支")
                    publisher.push(worktree)
                    job.state = JobState.live
                    db.add(
                        Publication(
                            job_id=job.id,
                            user_id=job.user_id,
                            release_slug=slug,
                            title=metadata["title"],
                            commit_sha=commit,
                        )
                    )
                    event(db, job, f"《{metadata['title']}》已发布", public=True)
                finally:
                    publisher.cleanup(worktree)
        except (AudioValidationError, PipelineError, OSError, ValueError) as exc:
            if job.state == JobState.pushing and job.attempt < 1:
                job.state = JobState.importing
                job.attempt += 1
                job.claimed_by = None
                event(db, job, "远端 main 已变化，Mio 将从最新版本自动重建一次")
                return
            job.state = JobState.failed
            job.last_error = str(exc)[:4000]
            job.claimed_by = None
            upload.error = job.last_error
            event(db, job, f"发布失败：{job.last_error}")


def recover_interrupted_jobs() -> None:
    settings = get_settings()
    publisher = MusicPublisher(settings)
    with SessionLocal() as db:
        jobs = db.scalars(
            select(Job).where(
                Job.state.in_(
                    [
                        JobState.analyzing,
                        JobState.importing,
                        JobState.building,
                        JobState.committing,
                        JobState.pushing,
                    ]
                )
            )
        ).all()
        for job in jobs:
            if job.state == JobState.pushing:
                try:
                    commit = publisher.find_remote_job_commit(job.id)
                except PipelineError:
                    commit = None
                if commit:
                    job.commit_sha = commit
                    job.state = JobState.live
                    metadata = dict(job.metadata_json)
                    existing = db.scalar(select(Publication).where(Publication.job_id == job.id))
                    if not existing:
                        db.add(
                            Publication(
                                job_id=job.id,
                                user_id=job.user_id,
                                release_slug=slugify(metadata["album"]),
                                title=metadata["title"],
                                commit_sha=commit,
                            )
                        )
                    db.add(
                        JobEvent(
                            job_id=job.id,
                            state=job.state.value,
                            message="服务重启后确认远端提交已上线",
                            public=True,
                        )
                    )
                    continue
            if job.state not in {JobState.analyzing, JobState.importing}:
                job.state = JobState.importing
            job.claimed_by = None
            db.add(
                JobEvent(
                    job_id=job.id,
                    state=job.state.value,
                    message="服务重启，任务已安全恢复到队列",
                )
            )
        db.commit()


def run() -> None:
    initialize_database()
    recover_interrupted_jobs()
    while True:
        with SessionLocal() as db:
            job = next_job(db)
            job_id = job.id if job else None
        if job_id:
            process_job(job_id)
        else:
            time.sleep(2)


if __name__ == "__main__":
    run()

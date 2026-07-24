from __future__ import annotations

import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    File,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mio_core.config import Settings, get_settings
from mio_core.database import get_db, initialize_database
from mio_core.git_pipeline import MusicPublisher, PipelineError
from mio_core.models import (
    Conversation,
    Invite,
    Job,
    JobEvent,
    JobState,
    LlmAudit,
    Message,
    Publication,
    Role,
    Upload,
    User,
    WebSession,
    now,
)
from mio_core.persona import mio_persona_message
from mio_core.providers import DeepSeekProvider
from mio_core.schemas import (
    BootstrapRequest,
    ChatRequest,
    InviteAcceptRequest,
    InviteCreateRequest,
    JobAnswers,
    LoginRequest,
    UploadCreateRequest,
)
from mio_core.security import (
    current_session,
    current_user,
    hash_password,
    hash_token,
    issue_session,
    require_admin,
    require_csrf,
    validate_username,
    verify_password,
)
from mio_core.uploads import create_upload, finalize_upload, store_chunk

settings = get_settings()
provider = DeepSeekProvider(settings)
login_attempts: dict[str, list[datetime]] = {}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    initialize_database()
    await provider.capabilities()
    yield


app = FastAPI(
    title="Mio Core",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)


def set_session_cookie(response: Response, token: str, active: Settings) -> None:
    response.set_cookie(
        "mio_session",
        token,
        max_age=30 * 24 * 3600,
        httponly=True,
        secure=active.secure_cookies,
        samesite="strict",
        path="/",
    )


def user_payload(user: User, csrf: str | None = None) -> dict:
    payload = {"id": user.id, "username": user.username, "role": user.role.value}
    if csrf:
        payload["csrfToken"] = csrf
    return payload


@app.get("/api/health")
async def health(db: Session = Depends(get_db)) -> dict:
    db.scalar(select(func.count(User.id)))
    return {"status": "ok", "llmConfigured": bool(settings.llm_api_key)}


@app.post("/api/auth/bootstrap")
def bootstrap(body: BootstrapRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(func.count(User.id))) > 0:
        raise HTTPException(status_code=409, detail="管理员已初始化")
    if not settings.bootstrap_token or not secrets.compare_digest(
        body.token, settings.bootstrap_token
    ):
        raise HTTPException(status_code=403, detail="初始化令牌无效")
    try:
        user = User(
            username=validate_username(body.username),
            password_hash=hash_password(body.password),
            role=Role.admin,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.add(user)
    db.commit()
    db.refresh(user)
    raw, session = issue_session(db, user)
    set_session_cookie(response, raw, settings)
    return user_payload(user, session.csrf_token)


@app.post("/api/auth/invites/accept")
def accept_invite(
    body: InviteAcceptRequest, response: Response, db: Session = Depends(get_db)
) -> dict:
    invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(body.token)))
    current = datetime.now(UTC)
    if (
        not invite
        or invite.used_at
        or invite.revoked_at
        or invite.expires_at.replace(tzinfo=UTC) <= current
    ):
        raise HTTPException(status_code=400, detail="邀请无效或已过期")
    try:
        user = User(
            username=validate_username(body.username),
            password_hash=hash_password(body.password),
            role=invite.role,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if db.scalar(select(User).where(User.username == user.username)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    invite.used_at = current
    db.add(user)
    db.commit()
    db.refresh(user)
    raw, session = issue_session(db, user)
    set_session_cookie(response, raw, settings)
    return user_payload(user, session.csrf_token)


@app.post("/api/auth/login")
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> dict:
    key = request.client.host if request.client else "unknown"
    cutoff = datetime.now(UTC) - timedelta(minutes=10)
    attempts = [stamp for stamp in login_attempts.get(key, []) if stamp > cutoff]
    if len(attempts) >= 8:
        raise HTTPException(status_code=429, detail="登录尝试过多，请稍后再试")
    user = db.scalar(select(User).where(User.username == body.username.strip()))
    if not user or not user.active or not verify_password(user.password_hash, body.password):
        attempts.append(datetime.now(UTC))
        login_attempts[key] = attempts
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    login_attempts.pop(key, None)
    raw, session = issue_session(db, user)
    set_session_cookie(response, raw, settings)
    return user_payload(user, session.csrf_token)


@app.post("/api/auth/logout")
def logout(
    response: Response,
    session: WebSession = Depends(current_session),
    _user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    db.delete(session)
    db.commit()
    response.delete_cookie("mio_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def me(session: WebSession = Depends(current_session)) -> dict:
    return user_payload(session.user, session.csrf_token)


@app.post("/api/admin/invites")
def create_invite(
    body: InviteCreateRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    raw = secrets.token_urlsafe(36)
    invite = Invite(
        token_hash=hash_token(raw),
        role=body.role,
        created_by=admin.id,
        expires_at=datetime.now(UTC) + timedelta(hours=body.expires_hours),
    )
    db.add(invite)
    db.commit()
    return {
        "id": invite.id,
        "url": f"{settings.public_url.rstrip('/')}/invite/{raw}",
        "expiresAt": invite.expires_at.isoformat(),
    }


@app.delete("/api/admin/invites/{invite_id}")
def revoke_invite(
    invite_id: str,
    _admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    invite = db.get(Invite, invite_id)
    if not invite or invite.used_at:
        raise HTTPException(status_code=404, detail="邀请不存在或已使用")
    invite.revoked_at = now()
    db.commit()
    return {"ok": True}


@app.post("/api/uploads")
def begin_upload(
    body: UploadCreateRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    upload = create_upload(
        db,
        settings,
        user.id,
        body.filename,
        body.size,
        body.total_chunks,
        body.sha256,
    )
    return upload_payload(upload)


@app.get("/api/uploads/{upload_id}")
def get_upload(
    upload_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> dict:
    upload = owned_upload(db, upload_id, user)
    return upload_payload(upload)


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def put_chunk(
    upload_id: str,
    index: int,
    file: UploadFile = File(...),
    x_chunk_sha256: str | None = Header(default=None),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    upload = owned_upload(db, upload_id, user)
    await store_chunk(db, settings, upload, index, file, x_chunk_sha256)
    return upload_payload(upload)


@app.post("/api/uploads/{upload_id}/finalize")
def finish_upload(
    upload_id: str, user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> dict:
    upload = finalize_upload(db, settings, owned_upload(db, upload_id, user))
    job = Job(user_id=user.id, upload_id=upload.id, state=JobState.analyzing)
    db.add(job)
    db.flush()
    db.add(JobEvent(job_id=job.id, state=job.state.value, message="上传完成，等待 Mio 分析"))
    db.commit()
    return {"upload": upload_payload(upload), "job": job_payload(job)}


@app.post("/api/jobs/{job_id}/answers")
def answer_job(
    job_id: str,
    body: JobAnswers,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    job = owned_job(db, job_id, user)
    if job.state != JobState.awaiting_input:
        raise HTTPException(status_code=409, detail="此任务当前不等待补充信息")
    metadata = dict(job.metadata_json)
    for key, value in body.model_dump(exclude_none=True).items():
        metadata[key] = value
    job.metadata_json = metadata
    job.required_fields = [
        field for field in job.required_fields if field == "cover" or not metadata.get(field)
    ]
    if not job.required_fields:
        job.state = JobState.importing
        job.claimed_by = None
        db.add(JobEvent(job_id=job.id, state=job.state.value, message="信息已补齐，继续发布"))
    db.commit()
    return job_payload(job)


@app.post("/api/jobs/{job_id}/cover")
async def upload_cover(
    job_id: str,
    file: UploadFile = File(...),
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> dict:
    job = owned_job(db, job_id, user)
    content = await file.read(12 * 1024 * 1024 + 1)
    if len(content) > 12 * 1024 * 1024 or not (
        content.startswith(b"\xff\xd8\xff") or content.startswith(b"\x89PNG\r\n\x1a\n")
    ):
        raise HTTPException(status_code=400, detail="封面必须是 12 MiB 以内的 JPEG 或 PNG")
    cover = settings.data_dir / "uploads" / job.upload_id / "cover.jpg"
    cover.write_bytes(content)
    metadata = dict(job.metadata_json)
    metadata["has_cover"] = True
    job.metadata_json = metadata
    job.required_fields = [field for field in job.required_fields if field != "cover"]
    if not job.required_fields:
        job.state = JobState.importing
        job.claimed_by = None
    db.commit()
    return job_payload(job)


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return job_payload(owned_job(db, job_id, user))


@app.get("/api/jobs/{job_id}/events")
async def job_events(
    job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)
) -> StreamingResponse:
    owned_job(db, job_id, user)

    async def stream():
        last_id = 0
        while True:
            with next(get_db()) as event_db:
                events = event_db.scalars(
                    select(JobEvent)
                    .where(JobEvent.job_id == job_id, JobEvent.id > last_id)
                    .order_by(JobEvent.id)
                ).all()
                for item in events:
                    last_id = item.id
                    payload = json.dumps(event_payload(item), ensure_ascii=False)
                    yield f"id: {item.id}\ndata: {payload}\n\n"
                state = event_db.scalar(select(Job.state).where(Job.id == job_id))
                if state in {JobState.live, JobState.failed}:
                    return
            yield ": keepalive\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/activity")
def activity(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(Publication, User)
        .join(User, User.id == Publication.user_id)
        .order_by(Publication.published_at.desc())
        .limit(50)
    ).all()
    return [
        {
            "id": publication.id,
            "title": publication.title,
            "releaseSlug": publication.release_slug,
            "publishedBy": user.username,
            "publishedAt": publication.published_at.isoformat(),
            "commitSha": publication.commit_sha,
            "reverted": bool(publication.reverted_by_commit),
        }
        for publication, user in rows
    ]


@app.post("/api/conversations")
def create_conversation(
    user: User = Depends(require_csrf), db: Session = Depends(get_db)
) -> dict:
    conversation = Conversation(user_id=user.id)
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation_payload(conversation)


@app.get("/api/conversations")
def list_conversations(
    user: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    conversations = db.scalars(
        select(Conversation)
        .where(Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
    ).all()
    return [conversation_payload(item) for item in conversations]


@app.get("/api/conversations/{conversation_id}")
def get_conversation(
    conversation_id: str,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> dict:
    conversation = owned_conversation(db, conversation_id, user)
    return {
        **conversation_payload(conversation),
        "messages": [
            {"id": item.id, "role": item.role, "content": item.content}
            for item in conversation.messages
        ],
    }


@app.post("/api/conversations/{conversation_id}/messages")
async def chat(
    conversation_id: str,
    body: ChatRequest,
    user: User = Depends(require_csrf),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    conversation = owned_conversation(db, conversation_id, user)
    message = Message(conversation_id=conversation.id, role="user", content=body.content)
    conversation.updated_at = now()
    db.add(message)
    db.commit()
    history = [
        {"role": item.role, "content": item.content}
        for item in conversation.messages[-30:]
        if item.role in {"user", "assistant"}
    ]
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_job_status",
                "description": "读取当前用户一个音乐发布任务的状态和仍需补充的字段。",
                "parameters": {
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_my_recent_jobs",
                "description": "列出当前用户最近的音乐发布任务。",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            },
        },
    ]

    async def stream():
        text = ""
        status_value = "ok"
        error_code = None
        prompt_tokens = None
        completion_tokens = None
        try:
            model_messages = [mio_persona_message(), *history]
            capabilities = await provider.capabilities(body.model)
            for _round in range(3):
                pending_calls: list[dict] = []
                round_text = ""
                async for chunk in provider.stream_chat(
                    model_messages,
                    tools if capabilities.tool_calls else [],
                    user.id,
                    body.model,
                ):
                    if chunk.usage:
                        prompt_tokens = chunk.usage.get("prompt_tokens")
                        completion_tokens = chunk.usage.get("completion_tokens")
                    if chunk.tool_calls:
                        pending_calls.extend(chunk.tool_calls)
                    if chunk.text:
                        text += chunk.text
                        round_text += chunk.text
                        payload = json.dumps(
                            {"type": "delta", "text": chunk.text}, ensure_ascii=False
                        )
                        yield f"data: {payload}\n\n"
                if not pending_calls:
                    break
                model_messages.append(
                    {
                        "role": "assistant",
                        "content": round_text or None,
                        "tool_calls": pending_calls,
                    }
                )
                for call in pending_calls:
                    result = execute_safe_tool(call, user.id)
                    model_messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id") or "",
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            status_value = "error"
            error_code = type(exc).__name__
            safe_error = model_error_message(exc)
            payload = json.dumps(
                {"type": "error", "message": safe_error},
                ensure_ascii=False,
            )
            yield f"data: {payload}\n\n"
        finally:
            with next(get_db()) as save_db:
                if text:
                    save_db.add(
                        Message(conversation_id=conversation.id, role="assistant", content=text)
                    )
                save_db.add(
                    LlmAudit(
                        user_id=user.id,
                        provider="deepseek",
                        model=body.model,
                        status=status_value,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        error_code=error_code,
                    )
                )
                save_db.commit()

    return StreamingResponse(stream(), media_type="text/event-stream")


def model_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return "DeepSeek API Key 无效或已失效；请联系管理员更新密钥。"
        if status == 402:
            return "DeepSeek 账户余额不足；音乐上传和发布队列仍可正常工作。"
        if status == 403:
            return "DeepSeek 拒绝了当前请求；请检查账户权限、余额或模型访问资格。"
        if status == 429:
            return "DeepSeek 当前请求较多，请稍后再和我说一次。"
    if isinstance(exc, httpx.TimeoutException):
        return "DeepSeek 响应超时；音乐上传和发布队列仍可正常工作。"
    return "Mio 暂时无法连接 DeepSeek；音乐上传和发布队列仍可正常工作。"


def execute_safe_tool(call: dict, user_id: str) -> dict:
    function = call.get("function") or {}
    name = function.get("name")
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {"error": "invalid_arguments"}
    with next(get_db()) as db:
        if name == "get_job_status":
            job_id = str(arguments.get("job_id") or "")
            job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user_id))
            if not job:
                return {"error": "job_not_found"}
            return {
                "job_id": job.id,
                "state": job.state.value,
                "title": str(job.metadata_json.get("title") or "")[:255],
                "required_fields": list(job.required_fields),
            }
        if name == "list_my_recent_jobs":
            jobs = db.scalars(
                select(Job)
                .where(Job.user_id == user_id)
                .order_by(Job.created_at.desc())
                .limit(10)
            ).all()
            return {
                "jobs": [
                    {
                        "job_id": job.id,
                        "state": job.state.value,
                        "title": str(job.metadata_json.get("title") or "")[:255],
                    }
                    for job in jobs
                ]
            }
    return {"error": "unsupported_tool"}


@app.get("/api/admin/jobs")
def admin_jobs(
    _admin: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    if _admin.role != Role.admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()
    return [job_payload(item, include_error=True) for item in jobs]


@app.get("/api/admin/jobs/{job_id}/events")
def admin_job_events(
    job_id: str, admin: User = Depends(current_user), db: Session = Depends(get_db)
) -> list[dict]:
    if admin.role != Role.admin or not db.get(Job, job_id):
        raise HTTPException(status_code=404, detail="任务不存在")
    events = db.scalars(
        select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id)
    ).all()
    return [event_payload(item) for item in events]


@app.post("/api/admin/jobs/{job_id}/retry")
def retry_job(
    job_id: str, _admin: User = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    job = db.get(Job, job_id)
    if not job or job.state != JobState.failed:
        raise HTTPException(status_code=409, detail="只能重试失败任务")
    job.state = JobState.analyzing
    job.claimed_by = None
    job.last_error = None
    job.attempt += 1
    db.add(JobEvent(job_id=job.id, state=job.state.value, message="管理员已重试任务"))
    db.commit()
    return job_payload(job)


@app.post("/api/admin/publications/{publication_id}/revert")
def revert_publication(
    publication_id: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    publication = db.get(Publication, publication_id)
    if not publication or publication.reverted_by_commit:
        raise HTTPException(status_code=409, detail="发布不存在或已经回滚")
    try:
        commit = MusicPublisher(settings).revert(publication, admin.username)
    except PipelineError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    publication.reverted_by_commit = commit
    db.commit()
    return {"ok": True, "commitSha": commit}


def owned_upload(db: Session, upload_id: str, user: User) -> Upload:
    upload = db.get(Upload, upload_id)
    if not upload or (upload.user_id != user.id and user.role != Role.admin):
        raise HTTPException(status_code=404, detail="上传不存在")
    return upload


def owned_job(db: Session, job_id: str, user: User) -> Job:
    job = db.get(Job, job_id)
    if not job or (job.user_id != user.id and user.role != Role.admin):
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def owned_conversation(db: Session, conversation_id: str, user: User) -> Conversation:
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.id:
        raise HTTPException(status_code=404, detail="对话不存在")
    return conversation


def upload_payload(upload: Upload) -> dict:
    return {
        "id": upload.id,
        "filename": upload.original_name,
        "size": upload.size,
        "state": upload.state.value,
        "receivedChunks": upload.received_chunks,
        "totalChunks": upload.total_chunks,
        "sha256": upload.sha256,
        "error": upload.error,
    }


def job_payload(job: Job, include_error: bool = False) -> dict:
    payload = {
        "id": job.id,
        "uploadId": job.upload_id,
        "state": job.state.value,
        "metadata": job.metadata_json,
        "requiredFields": job.required_fields,
        "commitSha": job.commit_sha,
        "createdAt": job.created_at.isoformat(),
        "updatedAt": job.updated_at.isoformat(),
    }
    if include_error:
        payload["lastError"] = job.last_error
    return payload


def event_payload(item: JobEvent) -> dict:
    return {
        "id": item.id,
        "state": item.state,
        "message": item.message,
        "createdAt": item.created_at.isoformat(),
    }


def conversation_payload(item: Conversation) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "createdAt": item.created_at.isoformat(),
        "updatedAt": item.updated_at.isoformat(),
    }


frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        candidate = frontend_dist / path
        if candidate.is_file() and candidate.resolve().is_relative_to(frontend_dist.resolve()):
            return FileResponse(candidate)
        return FileResponse(frontend_dist / "index.html")


def run() -> None:
    uvicorn.run("mio_core.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    run()

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from mio_core.config import Settings, get_settings
from mio_core.database import get_db
from mio_core.models import Role, User, WebSession

password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=4)
username_pattern = re.compile(r"^[\w\u3040-\u30ff\u3400-\u9fff-]{2,48}$", re.UNICODE)


def hash_token(token: str, settings: Settings | None = None) -> str:
    active = settings or get_settings()
    return hmac.new(
        active.session_secret.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def hash_password(password: str) -> str:
    if len(password) < 10 or len(password) > 256:
        raise ValueError("密码长度需为 10–256 个字符")
    return password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def validate_username(username: str) -> str:
    value = username.strip()
    if not username_pattern.fullmatch(value):
        raise ValueError("用户名需为 2–48 个字母、数字、中文、日文、下划线或连字符")
    return value


def issue_session(db: Session, user: User) -> tuple[str, WebSession]:
    raw_token = secrets.token_urlsafe(48)
    session = WebSession(
        token_hash=hash_token(raw_token),
        csrf_token=secrets.token_urlsafe(32),
        user_id=user.id,
        expires_at=datetime.now(UTC) + timedelta(days=30),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return raw_token, session


def current_session(
    request: Request,
    db: Session = Depends(get_db),
    mio_session: str | None = Cookie(default=None),
) -> WebSession:
    if not mio_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    token_hash = hash_token(mio_session)
    session = db.scalar(select(WebSession).where(WebSession.token_hash == token_hash))
    now = datetime.now(UTC)
    if not session or session.expires_at.replace(tzinfo=UTC) <= now or not session.user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效")
    session.last_seen_at = now
    db.commit()
    request.state.web_session = session
    return session


def current_user(session: WebSession = Depends(current_session)) -> User:
    return session.user


def require_csrf(
    session: WebSession = Depends(current_session),
    x_csrf_token: str | None = Header(default=None),
) -> User:
    if not x_csrf_token or not secrets.compare_digest(x_csrf_token, session.csrf_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF 校验失败")
    return session.user


def require_admin(user: User = Depends(require_csrf)) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="需要管理员权限")
    return user

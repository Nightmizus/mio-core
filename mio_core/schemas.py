from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from mio_core.models import Role


class BootstrapRequest(BaseModel):
    token: str
    username: str
    password: str


class InviteAcceptRequest(BaseModel):
    token: str
    username: str
    password: str


class LoginRequest(BaseModel):
    username: str
    password: str


class InviteCreateRequest(BaseModel):
    role: Role = Role.member
    expires_hours: int = Field(default=72, ge=1, le=720)


class UploadCreateRequest(BaseModel):
    filename: str
    size: int = Field(gt=0)
    total_chunks: int = Field(gt=0, le=10000)
    sha256: str | None = None

    @field_validator("sha256")
    @classmethod
    def valid_hash(cls, value: str | None) -> str | None:
        invalid = value is not None and (
            len(value) != 64 or any(c not in "0123456789abcdefABCDEF" for c in value)
        )
        if invalid:
            raise ValueError("sha256 must be a 64 character hexadecimal digest")
        return value.lower() if value else None


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class JobAnswers(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    artist: str | None = Field(default=None, max_length=255)
    album: str | None = Field(default=None, max_length=255)
    track_number: int | None = Field(default=None, ge=1, le=999)

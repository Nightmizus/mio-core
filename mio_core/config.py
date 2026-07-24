from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_prefix="MIO_", case_sensitive=False, extra="ignore"
    )

    env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8787
    public_url: str = "http://127.0.0.1:8787"
    data_dir: Path = Path("data")
    workspaces_dir: Path = Path("workspaces")
    database_url: str = "sqlite:///data/mio.db"
    session_secret: str = "development-only-change-me-please-32"
    bootstrap_token: str = ""
    secure_cookies: bool = False

    llm_api_key: str = ""
    llm_base_url: str = "https://api.deepseek.com"
    llm_model: str = "deepseek-v4-flash"
    llm_timeout_seconds: float = 60.0
    llm_global_concurrency: int = 2

    music_remote: str = "git@github.com:shizwd/musicmizu.git"
    music_branch: str = "main"
    git_ssh_command: str = ""
    faircamp_path: str = "faircamp.exe"
    ffmpeg_path: str = "ffmpeg.exe"
    ffprobe_path: str = "ffprobe.exe"
    powershell_path: str = "powershell.exe"
    enable_defender_scan: bool = True

    chunk_size: int = 8 * 1024 * 1024
    max_file_size: int = 500 * 1024 * 1024
    max_batch_size: int = 5 * 1024 * 1024 * 1024
    command_timeout_seconds: int = 900

    @field_validator("host")
    @classmethod
    def local_bind_only(cls, value: str) -> str:
        if value not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError(
                "Mio Core must bind to loopback; use a reverse tunnel for public access"
            )
        return value

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "quarantine").mkdir(exist_ok=True)
        (self.data_dir / "uploads").mkdir(exist_ok=True)
        (self.data_dir / "keys").mkdir(exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

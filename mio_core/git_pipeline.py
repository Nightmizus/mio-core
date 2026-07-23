from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from mio_core.config import Settings
from mio_core.models import Job, Publication, Upload
from mio_core.path_security import contained_path, slugify

ALLOWED_CHANGED_ROOTS = {"catalog", "dist"}
ALLOWED_INDEX_FILES = {"README.md"}


class PipelineError(RuntimeError):
    pass


@dataclass(slots=True)
class CommandResult:
    stdout: str
    stderr: str


class MusicPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.repo = settings.workspaces_dir / "musicmizu.git"

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_OPTIONAL_LOCKS": "0",
            }
        )
        if self.settings.git_ssh_command:
            env["GIT_SSH_COMMAND"] = self.settings.git_ssh_command
        return env

    @contextmanager
    def lock(self):
        lock_path = self.settings.workspaces_dir / "publish.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0)
            if handle.read(1) == b"":
                handle.seek(0)
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _run(self, args: list[str], cwd: Path | None = None, timeout: int | None = None) -> CommandResult:
        result = subprocess.run(
            args,
            cwd=cwd,
            env=self._env(),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout or self.settings.command_timeout_seconds,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            command = Path(args[0]).name
            raise PipelineError(f"{command} 失败：{result.stderr[-1200:]}")
        return CommandResult(result.stdout, result.stderr)

    def ensure_bare_repository(self) -> None:
        if not self.repo.exists():
            self.repo.parent.mkdir(parents=True, exist_ok=True)
            self._run(["git", "clone", "--bare", self.settings.music_remote, str(self.repo)])
        self._run(
            [
                "git",
                "--git-dir",
                str(self.repo),
                "fetch",
                "--prune",
                "origin",
                f"+refs/heads/{self.settings.music_branch}:refs/remotes/origin/{self.settings.music_branch}",
            ]
        )

    def _worktree(self, job_id: str) -> Path:
        return contained_path(self.settings.workspaces_dir / "jobs", job_id)

    def prepare_worktree(self, job_id: str) -> Path:
        self.ensure_bare_repository()
        worktree = self._worktree(job_id)
        if worktree.exists():
            shutil.rmtree(worktree)
        worktree.parent.mkdir(parents=True, exist_ok=True)
        self._run(
            [
                "git",
                "--git-dir",
                str(self.repo),
                "worktree",
                "add",
                "--detach",
                str(worktree),
                f"refs/remotes/origin/{self.settings.music_branch}",
            ]
        )
        return worktree

    def import_track(self, worktree: Path, upload: Upload, metadata: dict, cover: Path) -> str:
        album = clean_eno(metadata["album"])
        slug = slugify(album)
        release = contained_path(worktree / "catalog", slug)
        track_number = int(metadata["track_number"])
        track = contained_path(release, f"{track_number:02d}")
        if track.exists():
            raise PipelineError(f"曲序 {track_number:02d} 已存在于专辑 {album}")
        track.mkdir(parents=True)
        release.mkdir(parents=True, exist_ok=True)
        release_file = release / "release.eno"
        if not release_file.exists():
            release_file.write_text(
                "\n".join(
                    [
                        f"title: {album}",
                        f"permalink: {slug}",
                        f"release_artist: {clean_eno(metadata['artist'])}",
                        "release_download_access: disabled",
                        "track_download_access: disabled",
                        "track_numbering: arabic-dotted",
                        "",
                        "cover:",
                        f"description = {clean_eno(album)} cover",
                        "file = cover.jpg",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
        source = Path(upload.stored_path or "")
        title = clean_eno(metadata["title"])
        destination = track / f"{track_number:02d} - {title}{source.suffix.lower()}"
        shutil.copy2(source, destination)
        shutil.copy2(cover, track / "cover.jpg")
        if not (release / "cover.jpg").exists():
            shutil.copy2(cover, release / "cover.jpg")
        (track / "track.eno").write_text(
            "\n".join(
                [
                    f"title: {title}",
                    f"track_artist: {clean_eno(metadata['artist'])}",
                    "track_download_access: disabled",
                    "",
                    "cover:",
                    f"description = {title} cover",
                    "file = cover.jpg",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return slug

    def build(self, worktree: Path) -> None:
        script = worktree / "scripts" / "build.ps1"
        if not script.is_file():
            raise PipelineError("目标仓库缺少 scripts/build.ps1")
        env_faircamp = self.settings.faircamp_path
        self._run(
            [
                self.settings.powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-FaircampPath",
                env_faircamp,
            ],
            cwd=worktree,
        )
        self._validate_changes(worktree)
        self._validate_output(worktree)

    def _validate_changes(self, worktree: Path) -> None:
        output = self._run(["git", "status", "--porcelain=v1", "-z"], cwd=worktree).stdout
        for entry in output.split("\0"):
            if not entry:
                continue
            relative = entry[3:].replace("\\", "/")
            root = relative.split("/", 1)[0]
            if root not in ALLOWED_CHANGED_ROOTS and relative not in ALLOWED_INDEX_FILES:
                raise PipelineError(f"构建修改了未授权路径：{relative}")

    def _validate_output(self, worktree: Path) -> None:
        required = [
            worktree / "dist" / "index.html",
            worktree / "dist" / "library.json",
            worktree / "dist" / "custom.js",
        ]
        missing = [str(path.relative_to(worktree)) for path in required if not path.is_file()]
        if missing:
            raise PipelineError(f"构建输出不完整：{', '.join(missing)}")

    def commit(self, worktree: Path, job: Job, username: str) -> str:
        self._run(["git", "add", "--", "catalog", "dist", "README.md"], cwd=worktree)
        self._run(
            [
                "git",
                "-c",
                "user.name=Mio Core",
                "-c",
                "user.email=mio-core@users.noreply.github.com",
                "commit",
                "-m",
                f"Publish music from {username} (job {job.id})",
            ],
            cwd=worktree,
        )
        return self._run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()

    def push(self, worktree: Path) -> None:
        self._run(
            ["git", "push", "origin", f"HEAD:{self.settings.music_branch}"], cwd=worktree
        )

    def find_remote_job_commit(self, job_id: str) -> str | None:
        self.ensure_bare_repository()
        result = self._run(
            [
                "git",
                "--git-dir",
                str(self.repo),
                "log",
                f"refs/remotes/origin/{self.settings.music_branch}",
                "--fixed-strings",
                "--grep",
                f"job {job_id}",
                "-1",
                "--format=%H",
            ]
        )
        return result.stdout.strip() or None

    def cleanup(self, worktree: Path) -> None:
        if not worktree.exists():
            return
        self._run(
            ["git", "--git-dir", str(self.repo), "worktree", "remove", "--force", str(worktree)]
        )

    def revert(self, publication: Publication, admin_name: str) -> str:
        with self.lock():
            worktree = self.prepare_worktree(f"revert-{publication.id}")
            try:
                self._run(
                    [
                        "git",
                        "-c",
                        "user.name=Mio Core",
                        "-c",
                        "user.email=mio-core@users.noreply.github.com",
                        "revert",
                        "--no-edit",
                        publication.commit_sha,
                    ],
                    cwd=worktree,
                )
                self.build(worktree)
                self._run(
                    [
                        "git",
                        "commit",
                        "--amend",
                        "-m",
                        f"Revert publication {publication.id} by {admin_name}",
                    ],
                    cwd=worktree,
                )
                commit = self._run(["git", "rev-parse", "HEAD"], cwd=worktree).stdout.strip()
                self.push(worktree)
                return commit
            finally:
                self.cleanup(worktree)


def clean_eno(value: object) -> str:
    text = str(value).replace("\x00", " ").replace("\r", " ").replace("\n", " ").strip()
    if not text:
        raise PipelineError("元数据不能为空")
    return text[:255]

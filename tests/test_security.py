import pytest

from mio_core.path_security import contained_path, safe_filename, slugify
from mio_core.security import hash_password, verify_password


def test_argon2_password_roundtrip():
    password_hash = hash_password("correct horse battery staple")
    assert password_hash.startswith("$argon2id$")
    assert verify_password(password_hash, "correct horse battery staple")
    assert not verify_password(password_hash, "wrong password")


@pytest.mark.parametrize("name", ["../song.flac", "C:\\song.flac", "song.exe", "NUL.flac"])
def test_unsafe_upload_names_are_rejected(name):
    with pytest.raises(Exception):
        safe_filename(name)


def test_paths_cannot_escape(tmp_path):
    with pytest.raises(ValueError):
        contained_path(tmp_path, "..", "secret")


def test_release_slug_is_deterministic():
    assert slugify("Project Mili / 2026") == "project-mili-2026"

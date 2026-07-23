import hashlib

from mio_core.path_security import safe_filename


def test_supported_audio_name_is_preserved():
    assert safe_filename("真夜中のドア.flac") == "真夜中のドア.flac"


def test_chunk_hash_example():
    assert hashlib.sha256(b"mio").hexdigest() == (
        "ea31ac1d48d1880bfd2b4179f5da29f202f5daf778715f1aeb3a1f06149941a2"
    )

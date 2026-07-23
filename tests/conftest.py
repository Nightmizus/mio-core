import os
import tempfile
from pathlib import Path

import pytest

_TEST_ROOT = Path(tempfile.mkdtemp(prefix="mio-core-tests-"))
os.environ["MIO_DATA_DIR"] = str(_TEST_ROOT / "data")
os.environ["MIO_WORKSPACES_DIR"] = str(_TEST_ROOT / "workspaces")
os.environ["MIO_DATABASE_URL"] = f"sqlite:///{(_TEST_ROOT / 'mio.db').as_posix()}"
os.environ["MIO_SESSION_SECRET"] = "test-secret-that-is-more-than-32-characters"
os.environ["MIO_BOOTSTRAP_TOKEN"] = "test-bootstrap-token"
os.environ["MIO_ENABLE_DEFENDER_SCAN"] = "false"


@pytest.fixture(autouse=True)
def isolated_settings():
    yield

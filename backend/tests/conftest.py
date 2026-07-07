import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Must be set before config/main are imported by test modules
os.environ["ADMIN_USERNAME"] = "testadmin"
os.environ["ADMIN_PASSWORD"] = "testpass-123"
os.environ["SESSION_SECRET"] = "unit-test-secret"
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="rsm-test-")
os.environ["STATIC_DIR"] = ""

import config  # noqa: E402

config.settings = config.Settings.from_env()


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    import auth
    import main

    auth._attempts.clear()
    with TestClient(main.app) as c:
        yield c

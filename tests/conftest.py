import base64
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="sangam-test-")
os.environ.update({
    "SANGAM_DATABASE_URL": f"sqlite:///{Path(_tmp) / 'test.db'}",
    "SANGAM_MASTER_KEY": base64.b64encode(b"k" * 32).decode(),
    "SANGAM_RUN_WORKERS": "false",
    "SANGAM_BASE_URL": "http://testserver",
    "SANGAM_RATE_LIMIT_SCALE": "1000",
})
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from sangam.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def merchant(client):
    r = client.post("/v1/merchants", json={"name": "Chai Point", "email": "owner@chai.example", "country": "IN"})
    assert r.status_code == 201, r.text
    key = r.json()["secret_key_test"]
    return {"key": key, "h": {"Authorization": f"Bearer {key}"}, "live": r.json()["secret_key_live"]}

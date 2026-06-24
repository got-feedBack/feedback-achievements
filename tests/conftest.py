import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # Re-import server fresh so DATA_DIR / DB_PATH bind to tmp_path.
    for mod in ("server",):
        sys.modules.pop(mod, None)
    import server
    server.DATA_DIR = tmp_path
    server.DB_PATH = tmp_path / "wall.db"
    server.init_db()
    server._invalidate_cache()
    server._rate.clear()
    c = TestClient(server.app)
    c._server = server
    return c


TOKEN = {"X-Client-Token": "fb-wall-v1"}

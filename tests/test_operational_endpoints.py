import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("API_TOKEN", "CHANGE_ME")

from fastapi.testclient import TestClient  # noqa: E402
from apps.inventory_agent.main import app as inventory_app  # noqa: E402
from apps.supplier_agent.main import app as supplier_app  # noqa: E402


def test_inventory_live_endpoint():
    response = TestClient(inventory_app).get("/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_supplier_live_endpoint():
    response = TestClient(supplier_app).get("/live")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"


def test_inventory_diagnostics_requires_authentication():
    response = TestClient(inventory_app).get("/diagnostics")
    assert response.status_code == 401


def test_supplier_diagnostics_requires_authentication():
    response = TestClient(supplier_app).get("/diagnostics")
    assert response.status_code == 401

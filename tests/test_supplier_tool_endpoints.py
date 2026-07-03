import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "openai")

from fastapi.testclient import TestClient  # noqa: E402
from apps.supplier_agent.main import app  # noqa: E402


client = TestClient(app)


def test_list_suppliers_returns_reference_data():
    response = client.get("/suppliers")

    assert response.status_code == 200
    suppliers = response.json()["suppliers"]
    assert len(suppliers) >= 3
    assert any(s["supplier_name"] == "XYZ Metais" for s in suppliers)


def test_get_supplier_returns_structured_profile():
    response = client.get("/suppliers/XYZ%20Metais")

    assert response.status_code == 200
    supplier = response.json()["supplier"]
    assert supplier["supplier_id"] == "SUP001"
    assert supplier["city"] == "São Paulo"
    assert supplier["rating"] == "A"
    assert supplier["buyer"] == "João Silva"


def test_get_supplier_products_returns_product_codes():
    response = client.get("/suppliers/XYZ%20Metais/products")

    assert response.status_code == 200
    assert response.json()["products"] == ["PARAFUSO-M20"]


def test_get_supplier_contracts_returns_active_contract():
    response = client.get("/suppliers/XYZ%20Metais/contracts")

    assert response.status_code == 200
    contracts = response.json()["contracts"]
    assert contracts[0]["contract_id"] == "CTR-XYZ-2026-001"
    assert contracts[0]["status"] == "active"


def test_get_supplier_performance_returns_sla_metrics():
    response = client.get("/suppliers/XYZ%20Metais/performance")

    assert response.status_code == 200
    performance = response.json()["performance"]
    assert performance["risk_level"] == "low"
    assert performance["sla_on_time_delivery_percent"] == 96.5


def test_get_unknown_supplier_returns_404():
    response = client.get("/suppliers/Fornecedor%20Inexistente")

    assert response.status_code == 404
    assert "Supplier not found" in response.json()["detail"]

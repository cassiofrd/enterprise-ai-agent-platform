import os

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "openai")
os.environ.setdefault("API_TOKEN", "CHANGE_ME")

from fastapi.testclient import TestClient  # noqa: E402
from apps.inventory_agent.main import app  # noqa: E402


client = TestClient(app)
AUTH_HEADERS = {"Authorization": "Bearer CHANGE_ME"}


def test_get_product_requires_authentication():
    response = client.get("/products/PARAFUSO-M20")
    assert response.status_code == 401


def test_get_product_rejects_invalid_token():
    response = client.get(
        "/products/PARAFUSO-M20",
        headers={"Authorization": "Bearer WRONG_TOKEN"},
    )
    assert response.status_code == 401


def test_get_product_returns_structured_catalog_and_policy():
    response = client.get("/products/PARAFUSO-M20", headers=AUTH_HEADERS)

    assert response.status_code == 200
    product = response.json()["product"]
    assert product["code"] == "PARAFUSO-M20"
    assert product["preferred_supplier"] == "XYZ Metais"
    assert product["abc_class"] == "B"
    assert product["lead_time_days"] == 14
    assert product["inventory_policy"]["safety_stock_units"] == 200


def test_get_inventory_policy_combines_product_class_and_policy():
    response = client.get("/inventory-policy/PARAFUSO-M20", headers=AUTH_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_code"] == "PARAFUSO-M20"
    assert payload["abc_class"] == "B"
    assert payload["policy"]["safety_stock_units"] == 200
    assert payload["policy"]["critical_level_units"] == 100


def test_get_unknown_product_returns_404():
    response = client.get("/products/PARAFUSO-M30", headers=AUTH_HEADERS)

    assert response.status_code == 404
    assert "Product not found" in response.json()["detail"]


def test_get_products_by_supplier_returns_matching_products():
    response = client.get("/suppliers/XYZ%20Metais/products", headers=AUTH_HEADERS)

    assert response.status_code == 200
    products = response.json()["products"]
    assert len(products) == 1
    assert products[0]["code"] == "PARAFUSO-M20"

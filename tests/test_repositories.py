from __future__ import annotations

import json

import pytest

from shared.repositories.inventory_repository import InventoryRepository
from shared.repositories.supplier_repository import SupplierRepository


def test_inventory_repository_loads_product_policy_and_supplier_lookup():
    repository = InventoryRepository()

    product = repository.get_product("parafuso_m20")
    assert product is not None
    assert product["code"] == "PARAFUSO-M20"

    payload = repository.build_product_payload(product)
    assert payload["inventory_policy"]["safety_stock_units"] == 200

    supplier_products = repository.get_products_by_supplier("xyz metais")
    assert [item["code"] for item in supplier_products] == ["PARAFUSO-M20"]


def test_inventory_repository_returns_copies():
    repository = InventoryRepository()
    first = repository.get_product("PARAFUSO-M20")
    assert first is not None
    first["preferred_supplier"] = "Changed"

    second = repository.get_product("PARAFUSO-M20")
    assert second is not None
    assert second["preferred_supplier"] == "XYZ Metais"


def test_supplier_repository_finds_canonical_and_legal_names():
    repository = SupplierRepository()

    canonical_name, supplier = repository.find_supplier("xyz metais")
    assert canonical_name == "XYZ Metais"
    assert supplier is not None
    assert supplier["supplier_id"] == "SUP001"

    legal_name, legal_supplier = repository.find_supplier("XYZ Metais Ltda.")
    assert legal_name == "XYZ Metais"
    assert legal_supplier is not None


def test_repositories_validate_missing_data_sections(tmp_path):
    invalid_file = tmp_path / "invalid_inventory.json"
    invalid_file.write_text(json.dumps({"products": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="missing sections"):
        InventoryRepository(invalid_file)

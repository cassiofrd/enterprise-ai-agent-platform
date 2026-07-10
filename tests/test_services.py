from __future__ import annotations

import pytest

from shared.repositories.inventory_repository import InventoryRepository
from shared.repositories.supplier_repository import SupplierRepository
from shared.services.inventory_service import InventoryNotFoundError, InventoryService
from shared.services.supplier_service import SupplierNotFoundError, SupplierService


def test_inventory_service_composes_product_and_policy():
    service = InventoryService(InventoryRepository())

    payload = service.get_product_payload("parafuso_m20")
    assert payload["code"] == "PARAFUSO-M20"
    assert payload["preferred_supplier"] == "XYZ Metais"
    assert payload["inventory_policy"]["critical_level_units"] == 100


def test_inventory_service_prefers_primary_lookup_over_local_repository():
    service = InventoryService(
        InventoryRepository(),
        azure_product_lookup=lambda code: {
            "code": code,
            "product_name": code,
            "abc_class": "A",
            "preferred_supplier": "Azure Supplier",
            "lead_time_days": 3,
            "source": "azure_ai_search",
        },
    )

    payload = service.get_product_payload("PARAFUSO-M20")
    assert payload["preferred_supplier"] == "Azure Supplier"
    assert payload["source"] == "azure_ai_search"
    assert payload["inventory_policy"]["safety_stock_units"] == 500


def test_inventory_service_raises_domain_error_for_unknown_product():
    service = InventoryService(InventoryRepository())

    with pytest.raises(InventoryNotFoundError, match="Product not found"):
        service.get_product("UNKNOWN-001")


def test_supplier_service_returns_summary_contracts_and_performance():
    service = SupplierService(SupplierRepository())

    summary = service.get_supplier_summary("XYZ Metais")
    contracts = service.get_supplier_contracts("XYZ Metais")
    performance = service.get_supplier_performance("XYZ Metais")

    assert summary["supplier_id"] == "SUP001"
    assert contracts["contracts"][0]["status"] == "active"
    assert performance["performance"]["risk_level"] == "low"


def test_supplier_service_prefers_primary_lookup():
    service = SupplierService(
        SupplierRepository(),
        azure_supplier_lookup=lambda name: (
            "Cloud Supplier",
            {
                "supplier_id": "AZ001",
                "legal_name": "Cloud Supplier Ltd.",
                "city": "Recife",
                "state": "PE",
                "country": "Brasil",
                "rating": "A+",
                "risk_level": "low",
                "payment_terms": "15 dias",
                "buyer": "Cloud Buyer",
                "average_lead_time_days": 5,
                "products": ["CLOUD-001"],
                "contracts": [],
                "sla_on_time_delivery_percent": 99.0,
                "quality_score": 98,
                "source": "azure_ai_search",
            },
        ),
    )

    summary = service.get_supplier_summary("anything")
    assert summary["supplier_name"] == "Cloud Supplier"
    assert summary["source"] == "azure_ai_search"


def test_supplier_service_raises_domain_error_for_unknown_supplier():
    service = SupplierService(SupplierRepository())

    with pytest.raises(SupplierNotFoundError, match="Supplier not found"):
        service.get_supplier_summary("Unknown Supplier")

from __future__ import annotations

from copy import deepcopy
from typing import Callable

from shared.repositories.supplier_repository import SupplierRepository


class SupplierNotFoundError(LookupError):
    """Raised when structured supplier data cannot be found."""


class SupplierService:
    """Application service for structured supplier capabilities."""

    def __init__(
        self,
        repository: SupplierRepository,
        azure_supplier_lookup: Callable[[str], tuple[str, dict] | tuple[None, None]] | None = None,
    ) -> None:
        self.repository = repository
        self.azure_supplier_lookup = azure_supplier_lookup

    def find_supplier(self, name: str) -> tuple[str, dict]:
        if self.azure_supplier_lookup is not None:
            canonical_name, supplier = self.azure_supplier_lookup(name)
            if supplier is not None:
                return str(canonical_name or name), deepcopy(supplier)

        canonical_name, supplier = self.repository.find_supplier(name)
        if supplier is None or canonical_name is None:
            raise SupplierNotFoundError(f"Supplier not found: {name}")

        return canonical_name, supplier

    def list_supplier_summaries(self) -> list[dict]:
        return [
            self.repository.supplier_summary(name, supplier)
            for name, supplier in self.repository.list_suppliers()
        ]

    def get_supplier_summary(self, name: str) -> dict:
        canonical_name, supplier = self.find_supplier(name)
        return self.repository.supplier_summary(canonical_name, supplier)

    def get_supplier_products(self, name: str) -> dict:
        canonical_name, supplier = self.find_supplier(name)
        return {
            "supplier_name": canonical_name,
            "supplier_id": supplier["supplier_id"],
            "products": deepcopy(supplier["products"]),
        }

    def get_supplier_contracts(self, name: str) -> dict:
        canonical_name, supplier = self.find_supplier(name)
        return {
            "supplier_name": canonical_name,
            "supplier_id": supplier["supplier_id"],
            "contracts": deepcopy(supplier["contracts"]),
        }

    def get_supplier_performance(self, name: str) -> dict:
        canonical_name, supplier = self.find_supplier(name)
        return {
            "supplier_name": canonical_name,
            "supplier_id": supplier["supplier_id"],
            "performance": {
                "rating": supplier["rating"],
                "risk_level": supplier["risk_level"],
                "sla_on_time_delivery_percent": supplier[
                    "sla_on_time_delivery_percent"
                ],
                "quality_score": supplier["quality_score"],
                "average_lead_time_days": supplier["average_lead_time_days"],
            },
        }

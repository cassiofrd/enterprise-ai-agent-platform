from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


class SupplierRepository:
    """Read-only repository for local supplier reference data."""

    def __init__(self, data_path: Path | str | None = None) -> None:
        self.data_path = Path(data_path) if data_path else (
            Path(__file__).resolve().parents[2] / "data" / "supplier_reference_data.json"
        )
        self._suppliers = self._load()

    def _load(self) -> dict:
        if not self.data_path.exists():
            raise FileNotFoundError(f"Supplier reference data not found: {self.data_path}")

        with self.data_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, dict):
            raise ValueError("Supplier reference data must be a JSON object.")
        return data

    @staticmethod
    def normalize_supplier_name(name: str) -> str:
        return " ".join(name.strip().lower().split())

    def list_suppliers(self) -> list[tuple[str, dict]]:
        return [
            (name, deepcopy(supplier))
            for name, supplier in self._suppliers.items()
        ]

    def find_supplier(self, name: str) -> tuple[str, dict] | tuple[None, None]:
        normalized = self.normalize_supplier_name(name)

        for supplier_name, supplier in self._suppliers.items():
            aliases = {
                self.normalize_supplier_name(supplier_name),
                self.normalize_supplier_name(supplier.get("legal_name", "")),
            }
            if normalized in aliases:
                return supplier_name, deepcopy(supplier)

        return None, None

    @staticmethod
    def supplier_summary(name: str, supplier: dict) -> dict:
        return {
            "supplier_name": name,
            "supplier_id": supplier["supplier_id"],
            "legal_name": supplier["legal_name"],
            "city": supplier["city"],
            "state": supplier["state"],
            "country": supplier["country"],
            "rating": supplier["rating"],
            "risk_level": supplier["risk_level"],
            "payment_terms": supplier["payment_terms"],
            "buyer": supplier["buyer"],
            "average_lead_time_days": supplier["average_lead_time_days"],
            "products": deepcopy(supplier["products"]),
            "source": supplier.get("source", "structured_supplier_reference_data"),
        }

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path


class InventoryRepository:
    """Read-only repository for local inventory reference data.

    The API layer depends on this repository instead of knowing whether the
    fallback data comes from JSON, Azure AI Search, Cosmos DB, or another source.
    """

    def __init__(self, data_path: Path | str | None = None) -> None:
        self.data_path = Path(data_path) if data_path else (
            Path(__file__).resolve().parents[2] / "data" / "inventory_reference_data.json"
        )
        self._data = self._load()

    def _load(self) -> dict:
        if not self.data_path.exists():
            raise FileNotFoundError(f"Inventory reference data not found: {self.data_path}")

        with self.data_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        required_sections = {"products", "abc_policies", "purchasing_policy"}
        missing = required_sections.difference(data)
        if missing:
            raise ValueError(
                "Inventory reference data is missing sections: "
                + ", ".join(sorted(missing))
            )
        return data

    @staticmethod
    def normalize_product_code(code: str) -> str:
        return code.strip().replace("_", "-").upper()

    def get_product(self, code: str) -> dict | None:
        normalized = self.normalize_product_code(code)
        product = self._data["products"].get(normalized)
        return deepcopy(product) if product else None

    def list_products(self) -> list[dict]:
        return [deepcopy(product) for product in self._data["products"].values()]

    def get_products_by_supplier(self, supplier_name: str) -> list[dict]:
        normalized_supplier = " ".join(supplier_name.strip().lower().split())
        return [
            deepcopy(product)
            for product in self._data["products"].values()
            if " ".join(product.get("preferred_supplier", "").lower().split())
            == normalized_supplier
        ]

    def get_abc_policy(self, abc_class: str) -> dict | None:
        policy = self._data["abc_policies"].get(abc_class.strip().upper())
        return deepcopy(policy) if policy else None

    def get_purchasing_policy(self) -> dict:
        return deepcopy(self._data["purchasing_policy"])

    def build_product_payload(self, product: dict) -> dict:
        abc_class = str(product["abc_class"]).upper()
        policy = self.get_abc_policy(abc_class)
        if policy is None:
            raise ValueError(f"Inventory policy not found for ABC class: {abc_class}")

        return {
            **deepcopy(product),
            "inventory_policy": policy,
            "source": product.get("source", "structured_inventory_reference_data"),
        }

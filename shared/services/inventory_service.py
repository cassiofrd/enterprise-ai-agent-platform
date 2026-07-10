from __future__ import annotations

from copy import deepcopy
from typing import Callable

from shared.repositories.inventory_repository import InventoryRepository


class InventoryNotFoundError(LookupError):
    """Raised when structured inventory data cannot be found."""


class InventoryService:
    """Application service for structured inventory capabilities.

    The service owns source selection and business composition. The API layer
    only handles HTTP concerns, while repositories remain focused on persistence.
    """

    def __init__(
        self,
        repository: InventoryRepository,
        azure_product_lookup: Callable[[str], dict | None] | None = None,
    ) -> None:
        self.repository = repository
        self.azure_product_lookup = azure_product_lookup

    def normalize_product_code(self, code: str) -> str:
        return self.repository.normalize_product_code(code)

    def _get_product_from_primary_source(self, code: str) -> dict | None:
        normalized = self.normalize_product_code(code)

        if self.azure_product_lookup is not None:
            product = self.azure_product_lookup(normalized)
            if product is not None:
                return deepcopy(product)

        return self.repository.get_product(normalized)

    def get_product(self, code: str) -> dict:
        normalized = self.normalize_product_code(code)
        product = self._get_product_from_primary_source(normalized)
        if product is None:
            raise InventoryNotFoundError(f"Product not found: {normalized}")
        return product

    def get_product_payload(self, code: str) -> dict:
        product = self.get_product(code)
        return self.repository.build_product_payload(product)

    def get_inventory_policy(self, code: str) -> dict:
        product = self.get_product(code)
        abc_class = str(product["abc_class"]).upper()
        policy = self.repository.get_abc_policy(abc_class)

        if policy is None:
            raise InventoryNotFoundError(
                f"Inventory policy not found for ABC class: {abc_class}"
            )

        return {
            "product_code": product["code"],
            "abc_class": abc_class,
            "policy": policy,
            "source": product.get(
                "source",
                "structured_inventory_reference_data",
            ),
        }

    def get_products_by_supplier(self, supplier_name: str) -> list[dict]:
        products = [
            self.repository.build_product_payload(product)
            for product in self.repository.get_products_by_supplier(supplier_name)
        ]

        if not products:
            raise InventoryNotFoundError(
                f"No products found for supplier: {supplier_name}"
            )

        return products

    def get_purchasing_policy(self) -> dict:
        return self.repository.get_purchasing_policy()

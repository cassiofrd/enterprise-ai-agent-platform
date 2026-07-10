"""Repository abstractions for structured supply-chain reference data."""
from .inventory_repository import InventoryRepository
from .supplier_repository import SupplierRepository

__all__ = ["InventoryRepository", "SupplierRepository"]

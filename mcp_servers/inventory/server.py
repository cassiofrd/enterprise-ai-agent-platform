from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("inventory-tools")


@mcp.tool()
def get_reorder_policy(sku: str) -> str:
    """Return SKU-specific reorder policy from the inventory tool server."""
    return (
        f"Reorder policy for {sku}: "
        "minimum stock = 100 units; "
        "recommended reorder quantity = 500 units; "
        "preferred supplier = ACME Logistics; "
        "priority is high when backorder is above 100 units."
    )


if __name__ == "__main__":
    mcp.run()

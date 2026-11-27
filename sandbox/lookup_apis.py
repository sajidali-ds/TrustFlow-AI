from __future__ import annotations

from typing import Any, Dict

from sandbox.store import load


class ToolError(Exception):
    """A simulated API call failed (not found, etc.)."""


def get_customer(customer_id: str) -> Dict[str, Any]:
    customers = load("customers")
    if customer_id not in customers:
        raise ToolError(f"customer_db: no customer found with id {customer_id!r}")
    return customers[customer_id]


def get_order(order_id: str) -> Dict[str, Any]:
    orders = load("orders")
    if order_id not in orders:
        raise ToolError(f"order_api: no order found with id {order_id!r}")
    return orders[order_id]


def get_inventory(sku: str) -> Dict[str, Any]:
    inventory = load("inventory")
    if sku not in inventory:
        raise ToolError(f"inventory_api: no inventory record for sku {sku!r}")
    return inventory[sku]

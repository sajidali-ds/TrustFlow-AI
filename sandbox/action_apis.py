from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from sandbox import chaos
from sandbox.store import load, save


class TransientError(Exception):
    """Timeout-like error: the caller cannot know whether the action happened."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _blocked(action: str, reason: str) -> Dict[str, Any]:
    return {"status": "blocked", "action": action, "reason": reason}


def _replay(key: str):
    rec = load("ledger").get(key)
    return {**rec, "replayed": True} if rec else None


def _commit(key: str, result: Dict[str, Any]) -> None:
    ledger = load("ledger")
    ledger[key] = result
    save("ledger", ledger)


def issue_refund(order_id: str, amount: float, reason: str, idempotency_key: str) -> Dict[str, Any]:
    if (r := _replay(idempotency_key)):
        return r
    orders = load("orders")
    order = orders.get(order_id)
    if order is None:
        return _blocked("refund", f"order {order_id} not found")
    if order.get("refund_issued"):
        return _blocked("refund", f"order {order_id} already has a refund on record")
    if order.get("status") in ("cancelled", "replacement_shipped"):
        return _blocked("refund", f"order {order_id} is '{order['status']}'; refund no longer applicable")
    value = round(order["unit_price"] * order["quantity"], 2)
    if amount > value + 0.001:
        return _blocked("refund", f"requested amount {amount} exceeds order value {value}")

    result = {"status": "success", "action": "refund", "order_id": order_id, "amount": amount,
              "transaction_id": f"RFND-{uuid.uuid4().hex[:8].upper()}", "issued_at": _now()}

    if chaos.consume("silent_failure"):          # lies: reports success, writes nothing
        return result

    order.update(refund_issued=True, refund_amount=amount, refund_reason=reason,
                 refund_txn=result["transaction_id"], status="refunded")
    orders[order_id] = order
    save("orders", orders)
    _commit(idempotency_key, result)

    if chaos.consume("timeout_after_commit"):    # committed, but the caller never hears back
        raise TransientError("payment_gateway: request timed out (outcome unknown)")
    return result


def issue_replacement(order_id: str, idempotency_key: str) -> Dict[str, Any]:
    if (r := _replay(idempotency_key)):
        return r
    orders = load("orders")
    order = orders.get(order_id)
    if order is None:
        return _blocked("replacement", f"order {order_id} not found")
    if order.get("replacement_issued"):
        return _blocked("replacement", f"order {order_id} already has a replacement on record")
    if order.get("status") in ("refunded", "cancelled"):
        return _blocked("replacement", f"order {order_id} is '{order['status']}'")

    sku = order["sku"]
    inventory = load("inventory")
    if sku not in inventory:
        return _blocked("replacement", f"no inventory record for {sku}")

    if chaos.consume("stock_race"):               # someone else grabs the last unit first
        inventory[sku]["on_hand"] = 0
        save("inventory", inventory)

    inv = inventory[sku]
    if inv["on_hand"] <= 0:
        return _blocked("replacement", f"sku {sku} is out of stock (restock_eta_days={inv['restock_eta_days']})")

    result = {"status": "success", "action": "replacement", "order_id": order_id, "sku": sku,
              "shipment_id": f"SHIP-{uuid.uuid4().hex[:8].upper()}", "issued_at": _now()}

    if chaos.consume("silent_failure"):
        return result

    inv["on_hand"] -= 1
    inventory[sku] = inv
    save("inventory", inventory)
    order.update(replacement_issued=True, replacement_shipment=result["shipment_id"],
                 status="replacement_shipped")
    orders[order_id] = order
    save("orders", orders)
    _commit(idempotency_key, result)

    if chaos.consume("timeout_after_commit"):
        raise TransientError("fulfilment_api: request timed out (outcome unknown)")
    return result


def cancel_order(order_id: str, idempotency_key: str) -> Dict[str, Any]:
    if (r := _replay(idempotency_key)):
        return r
    orders = load("orders")
    order = orders.get(order_id)
    if order is None:
        return _blocked("cancellation", f"order {order_id} not found")
    if order["status"] not in ("processing", "pending_shipment"):
        return _blocked("cancellation", f"order {order_id} has status '{order['status']}' and can no "
                        "longer be cancelled per POL-004 (only 'processing' or 'pending_shipment')")

    result = {"status": "success", "action": "cancellation", "order_id": order_id, "cancelled_at": _now()}
    if chaos.consume("silent_failure"):
        return result
    order["status"] = "cancelled"
    orders[order_id] = order
    save("orders", orders)
    _commit(idempotency_key, result)
    if chaos.consume("timeout_after_commit"):
        raise TransientError("order_api: request timed out (outcome unknown)")
    return result

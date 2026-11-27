from __future__ import annotations

from typing import Any, Dict, List, Optional

from sandbox.store import load

EXPECTED_STATUS = {"refund": "refunded", "replacement": "replacement_shipped", "cancellation": "cancelled"}


def verify_resolution(order_id: str, action: str, *, amount: Optional[float] = None,
                      on_hand_before: Optional[int] = None) -> Dict[str, Any]:
    order = load("orders").get(order_id)
    if order is None:
        return {"verified": False, "checks": [], "reason": f"order {order_id} not found during verification"}

    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    exp = EXPECTED_STATUS[action]
    check("order_status", order.get("status") == exp, f"expected '{exp}', live system shows '{order.get('status')}'")

    if action == "refund":
        check("refund_amount", order.get("refund_amount") == amount,
              f"expected {amount}, live system shows {order.get('refund_amount')}")
        check("refund_transaction_recorded", bool(order.get("refund_txn")), "refund_txn present on order")
    elif action == "replacement":
        check("shipment_recorded", bool(order.get("replacement_shipment")), "replacement_shipment present on order")
        if on_hand_before is not None:
            now = load("inventory")[order["sku"]]["on_hand"]
            check("inventory_decremented", now == on_hand_before - 1,
                  f"on_hand {on_hand_before} -> {now} (expected {on_hand_before - 1})")

    failed = [c for c in checks if not c["ok"]]
    return {"verified": not failed, "order_id": order_id, "checks": checks,
            "reason": "; ".join(c["detail"] for c in failed) or None,
            "actual_status": order.get("status")}

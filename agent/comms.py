from __future__ import annotations

from typing import Any, Dict, List

from sandbox.store import load

NEXT_STEP = {
    "POL-003": "Review refund history. If the claim looks genuine, approve the refund of the order value; otherwise offer store credit.",
    "POL-004": "Order is past the cancellation window. Start a return, or refuse politely if outside return terms.",
    "POL-005": "Review and decide manually; the agent could not match a permitted automated action.",
    "POL-007": "Verify the caller's identity before discussing or changing this order.",
    "POL-008": "Goodwill review: claim is outside the automated window. Replacement stock level is in the facts below.",
}


def _first_name(state: Dict[str, Any]) -> str:
    name = (state.get("customer_info") or {}).get("name", "")
    return name.split()[0] if name else "there"


def customer_reply(state: Dict[str, Any]) -> str:
    hi = f"Hi {_first_name(state)},"
    outcome, action = state["outcome"], state.get("final_action")
    res = state.get("action_result") or {}
    order = state.get("order_info") or {}
    item = order.get("item_name", "your order")

    if outcome == "resolved" and action == "refund":
        return (f"{hi} I'm sorry about your {item}. I've issued a full refund of ${res['amount']:.2f} "
                f"(reference {res['transaction_id']}).")
    if outcome == "resolved" and action == "replacement":
        extra = " You don't need to send the damaged item back." if order.get("unit_price", 999) < 75 else ""
        return (f"{hi} I'm sorry about your {item}. A replacement is on its way "
                f"(shipment {res['shipment_id']}).{extra}")
    if outcome == "resolved" and action == "cancellation":
        return f"{hi} Your order {state['order_id']} ({item}) has been cancelled."
    if outcome == "needs_clarification":
        return (f"{hi} I'm sorry your {item} stopped working. This is covered, and the choice is yours: "
                f"{state['question']} Reply with one word and I'll take care of it right away.")
    if outcome == "already_resolved":
        done = ("a refund" if order.get("refund_issued") else
                "a replacement" if order.get("replacement_issued") else "a cancellation")
        ref = order.get("refund_txn") or order.get("replacement_shipment") or ""
        return (f"{hi} Good news: {done} was already processed for order {state['order_id']}"
                f"{f' (reference {ref})' if ref else ''}, so there is nothing further to do on this order.")
    return (f"{hi} Thanks for your patience. I've passed your case to a specialist with all the details, "
            "so you won't need to repeat yourself. They will pick it up from here.")


def handoff_brief(state: Dict[str, Any], reasons: List[Dict[str, str]]) -> Dict[str, Any]:
    intent = state.get("intent", {})
    v = state.get("verdict") or {}
    f = v.get("facts") or {}
    pids = [r["policy"] for r in reasons]
    high = bool(intent.get("injection_flags")) or "POL-007" in pids or intent.get("disputes_facts")
    return {
        "case_id": state["case_id"],
        "priority": "high" if high else "normal",
        "summary": f"{intent.get('issue_type', 'unknown')} claim on {state['order_id']}; "
                   f"customer asked for: {intent.get('requested_outcome', 'none')}.",
        "why_escalated": reasons,
        "flags": (["possible prompt-injection / manipulation attempt in customer text"]
                  if intent.get("injection_flags") else []),
        "facts": f,
        "already_tried": [f"{c['action']} ({c['kind']}): {c['reason']}" for c in state.get("constraints", [])],
        "suggested_next_step": NEXT_STEP.get(pids[0], NEXT_STEP["POL-005"]) if pids else NEXT_STEP["POL-005"],
        "customer_message": state["customer_message"],
    }

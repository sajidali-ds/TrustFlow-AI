from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from agent.intent import Intent

VALUE_CEILING = 200.0           # POL-005
ABUSE_REFUNDS_90D = 3           # POL-003
ABUSE_MIN_VALUE = 50.0          # POL-003
WINDOW_DAMAGE_DAYS = 30         # POL-001 / POL-002
WINDOW_HARDWARE_DAYS = 90       # POL-006
TERMINAL = ("refunded", "cancelled", "replacement_shipped")


@dataclass
class Facts:
    order_value: float = 0.0
    days_since_delivery: Optional[int] = None
    stock_on_hand: Optional[int] = None
    customer_tier: str = "standard"
    refunds_90d: int = 0
    order_status: str = ""
    ownership_ok: bool = True


@dataclass
class Verdict:
    """What policy says about this case. `allowed` is ordered most-preferred first."""
    escalate: List[Tuple[str, str]] = field(default_factory=list)       # (policy_id, reason)
    already_resolved: Optional[Tuple[str, str]] = None
    clarify: Optional[str] = None                                       # question to ask the customer first
    allowed: Dict[str, List[str]] = field(default_factory=dict)         # action -> policy ids
    forbidden: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # action -> (policy id, reason)
    facts: Optional[Facts] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def build_facts(order: Dict[str, Any], customer: Dict[str, Any], inventory: Optional[Dict[str, Any]],
                requesting_customer_id: str, as_of: date) -> Facts:
    days = None
    if order.get("delivered_date"):
        days = (as_of - date.fromisoformat(order["delivered_date"])).days
    return Facts(
        order_value=round(order["unit_price"] * order["quantity"], 2),
        days_since_delivery=days,
        stock_on_hand=None if inventory is None else inventory["on_hand"],
        customer_tier=customer.get("loyalty_tier", "standard"),
        refunds_90d=customer.get("past_refunds_last_90_days", 0),
        order_status=order.get("status", ""),
        ownership_ok=order.get("customer_id") == requesting_customer_id,
    )


def evaluate(intent: Intent, f: Facts) -> Verdict:
    v = Verdict(facts=f)

    # ---- hard stops: nothing automated may happen --------------------------------
    if not f.ownership_ok:
        v.escalate.append(("POL-007", "order does not belong to the requesting customer; identity verification needed"))
    if intent.disputes_facts:
        v.escalate.append(("POL-005", "customer disputes the facts on record"))
    if f.order_value > VALUE_CEILING:
        v.escalate.append(("POL-005", f"resolution value ${f.order_value:.2f} exceeds the ${VALUE_CEILING:.0f} ceiling"))
    if v.escalate:
        return v

    # ---- duplicate / already handled ---------------------------------------------
    if f.order_status in TERMINAL:
        v.already_resolved = ("POL-010", f"order is already '{f.order_status}'; no second action is taken")
        return v

    # ---- cancellation -------------------------------------------------------------
    if intent.issue_type == "cancel":
        if f.order_status in ("processing", "pending_shipment"):
            v.allowed["cancellation"] = ["POL-004"]
        else:
            v.escalate.append(("POL-004", f"order status is '{f.order_status}' so it cannot be cancelled; "
                                          "route to returns/refund handling by a human"))
        return v

    # ---- defect claims -------------------------------------------------------------
    if intent.issue_type not in ("hardware_failure", "partial_defect", "damaged"):
        v.escalate.append(("POL-005", f"reported issue ('{intent.issue_type}') matches no automated policy"))
        return v

    d = f.days_since_delivery
    hardware = intent.issue_type == "hardware_failure"
    window = WINDOW_HARDWARE_DAYS if hardware else WINDOW_DAMAGE_DAYS
    if d is None or d > window:
        v.escalate.append(("POL-008", f"claim is {d} days after delivery; automated window is {window} days"))
        return v

    in_stock = (f.stock_on_hand or 0) > 0
    core = "POL-006" if hardware else ("POL-002" if intent.issue_type == "partial_defect" else "POL-001")

    if in_stock:
        v.allowed["replacement"] = [core]
    else:
        v.forbidden["replacement"] = (core, "SKU is out of stock")

    # refund eligibility
    refund_ok_by_entitlement = hardware or (not in_stock) or intent.declines_replacement
    abuse_flag = (f.refunds_90d > ABUSE_REFUNDS_90D and f.customer_tier != "gold"
                  and f.order_value > ABUSE_MIN_VALUE and not hardware)
    if not refund_ok_by_entitlement:
        v.forbidden["refund"] = (core, "replacement is available and the customer has not declined it")
    elif abuse_flag:
        v.forbidden["refund"] = ("POL-003", f"{f.refunds_90d} refunds in 90 days (non-gold) and value "
                                           f"${f.order_value:.2f} > ${ABUSE_MIN_VALUE:.0f}: manual review required")
    else:
        v.allowed["refund"] = [core] + (["POL-006"] if hardware and core != "POL-006" else [])

    # ordering: honour an explicit customer preference only if policy allows it
    pref = intent.requested_outcome
    if pref in v.allowed:
        v.allowed = {pref: v.allowed[pref], **{k: x for k, x in v.allowed.items() if k != pref}}

    # POL-006 says replacement-or-refund is the CUSTOMER'S choice: if they have not said, ask.
    if hardware and set(v.allowed) == {"replacement", "refund"} and pref == "none":
        v.clarify = "Would you like a replacement or a refund?"

    if not v.allowed:
        reason = v.forbidden.get("refund", ("POL-005", "no permitted action"))
        v.escalate.append(("POL-005" if reason[0] != "POL-003" else "POL-003",
                           f"no permitted automated action ({reason[1]})"))
    return v


def validate_proposal(plan: Dict[str, Any], v: Verdict) -> Tuple[bool, List[str]]:
    """The guardrail. Returns (ok, violations). Escalation is always permitted."""
    action = plan.get("action")
    if action == "escalate":
        return True, []
    if action == "ask_customer":
        return (v.clarify is not None), ([] if v.clarify else ["nothing to clarify; policy already determines the outcome"])
    if action == "no_action":
        return (v.already_resolved is not None), ([] if v.already_resolved else ["no_action only valid for already-resolved orders"])

    problems: List[str] = []
    if v.escalate:
        problems.append(f"{v.escalate[0][0]}: policy requires escalation ({v.escalate[0][1]})")
    elif v.already_resolved:
        problems.append(f"{v.already_resolved[0]}: {v.already_resolved[1]}")
    elif v.clarify and action in ("refund", "replacement"):
        problems.append("POL-006: replacement vs refund is the customer's choice and they have not made it yet")
    elif action in v.forbidden:
        pid, why = v.forbidden[action]
        problems.append(f"{pid}: '{action}' is forbidden ({why})")
    elif action not in v.allowed:
        problems.append(f"'{action}' is not a permitted action for this case")

    if action == "refund" and not problems:
        amt = plan.get("params", {}).get("amount")
        if amt is None or abs(float(amt) - v.facts.order_value) > 0.005:
            problems.append(f"POL-001: refund must equal the order value ${v.facts.order_value:.2f}, got {amt}")
    return (not problems), problems

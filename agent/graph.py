from __future__ import annotations

from datetime import date
from typing import Any, Dict

from agent import comms
from agent.intent import parse_intent_auto
from agent.policy_engine import build_facts, evaluate, validate_proposal
from agent.proposer import propose
from agent.runtime import END, Flow
from agent.state import CaseState, emit
from sandbox.action_apis import TransientError, cancel_order, issue_refund, issue_replacement
from sandbox.lookup_apis import ToolError, get_customer, get_inventory, get_order
from sandbox.policy_rag import retrieve_policies
from sandbox.verification_api import verify_resolution

MAX_PROPOSALS = 2        # guardrail rejections tolerated before escalating
MAX_TRANSIENT_RETRIES = 2


def understand(s: CaseState) -> Dict[str, Any]:
    intent = parse_intent_auto(s["customer_message"]).as_dict()
    emit(s, "understand", f"issue={intent['issue_type']}, asked_for={intent['requested_outcome']}"
         f"{', DISPUTE' if intent['disputes_facts'] else ''}"
         f"{', INJECTION-ATTEMPT (ignored per POL-009)' if intent['injection_flags'] else ''}")
    return {"intent": intent}


def retrieve(s: CaseState) -> Dict[str, Any]:
    customer = order = inventory = None
    try:
        customer = get_customer(s["customer_id"])
    except ToolError as e:
        emit(s, "retrieve", str(e))
    try:
        order = get_order(s["order_id"])
        emit(s, "retrieve", f"order {order['order_id']}: status={order['status']}, "
             f"item={order['item_name']}, value=${order['unit_price'] * order['quantity']:.2f}")
        inventory = get_inventory(order["sku"])
        emit(s, "retrieve", f"inventory {order['sku']}: on_hand={inventory['on_hand']}")
    except ToolError as e:
        emit(s, "retrieve", str(e))
    docs = retrieve_policies(s["customer_message"], top_k=3)
    return {"customer_info": customer, "order_info": order, "inventory_info": inventory, "policy_docs": docs}


def assess(s: CaseState) -> Dict[str, Any]:
    from agent.policy_engine import Verdict
    if not s.get("order_info") or not s.get("customer_info"):
        what = "order" if not s.get("order_info") else "customer"
        v = Verdict(escalate=[("POL-005", f"{what} could not be located in the system of record")])
    else:
        facts = build_facts(s["order_info"], s["customer_info"], s.get("inventory_info"),
                            s["customer_id"], date.fromisoformat(s["as_of"]))
        v = evaluate(_intent_obj(s), facts)
    summary = (f"escalate={[p for p, _ in v.escalate]}" if v.escalate else
               f"already_resolved={v.already_resolved[0]}" if v.already_resolved else
               f"allowed={list(v.allowed)}, forbidden={ {k: x[0] for k, x in v.forbidden.items()} }")
    emit(s, "assess", f"policy verdict: {summary}")
    return {"verdict": v.as_dict(), "_verdict_obj": v}


def _intent_obj(s: CaseState):
    from agent.intent import Intent
    return Intent(**{k: val for k, val in s["intent"].items()})


def propose_node(s: CaseState) -> Dict[str, Any]:
    ctx = {"verdict": s["_verdict_obj"], "intent": s["intent"], "constraints": s["constraints"],
           "rejections": s["rejections"], "policy_docs": s["policy_docs"], "message": s["customer_message"],
           "order": s.get("order_info"), "customer": s.get("customer_info"), "inventory": s.get("inventory_info")}
    plan = propose(s["proposer"], ctx)
    amt = f" amount=${plan['params']['amount']:.2f}" if plan["action"] == "refund" else ""
    emit(s, "propose", f"[{plan['proposer']}] action={plan['action']}{amt} :: {plan['rationale']}")
    return {"plan": plan}


def guard(s: CaseState) -> Dict[str, Any]:
    plan = s["plan"]
    if not s.get("guardrail", True):
        emit(s, "guard", "GUARDRAIL DISABLED (ablation run): proposal passes unchecked")
        return {"guard_ok": True}
    ok, problems = validate_proposal(plan, s["_verdict_obj"])
    if ok:
        emit(s, "guard", f"proposal '{plan['action']}' APPROVED by policy engine")
        return {"guard_ok": True}
    rejections = s["rejections"] + [{"action": plan["action"], "params": plan.get("params"), "violations": problems}]
    emit(s, "guard", f"proposal '{plan['action']}' REJECTED: {'; '.join(problems)}")
    if len(rejections) >= MAX_PROPOSALS:
        escalate_plan = {"action": "escalate", "params": {}, "proposer": "guardrail",
                         "rationale": f"proposer could not produce a policy-compliant action: {problems[0]}"}
        return {"rejections": rejections, "plan": escalate_plan, "guard_ok": True}
    return {"rejections": rejections, "guard_ok": False}


def route_after_guard(s: CaseState) -> str:
    if not s["guard_ok"]:
        return "propose"          # rejected: ask the proposer again, now with the rejection as feedback
    return {"escalate": "escalate", "no_action": "close_duplicate",
            "ask_customer": "clarify"}.get(s["plan"]["action"], "execute")


def execute(s: CaseState) -> Dict[str, Any]:
    plan, oid = s["plan"], s["order_id"]
    action = plan["action"]
    key = f"{s['case_id']}:{oid}:{action}"       # same key across retries => at-most-once effect
    try:
        before = get_inventory(s["order_info"]["sku"])["on_hand"] if s.get("order_info") else None
    except ToolError:
        before = None
    calls = {"refund": lambda: issue_refund(oid, plan["params"]["amount"], plan["rationale"], key),
             "replacement": lambda: issue_replacement(oid, key),
             "cancellation": lambda: cancel_order(oid, key)}
    result: Dict[str, Any] = {"status": "blocked", "action": action, "reason": "unrecognised action"}
    for attempt in range(1, MAX_TRANSIENT_RETRIES + 2):
        try:
            result = calls[action]()
            break
        except TransientError as e:
            emit(s, "execute", f"{action}: transient failure ({e}); attempt {attempt}, retrying with SAME idempotency key")
            result = {"status": "unknown", "action": action, "reason": str(e)}
    executed = s["executed"] + [{"action": action, "params": plan.get("params"), "status": result["status"]}]
    if result["status"] == "success":
        emit(s, "execute", f"{action}: API reported success" + (" (replayed from ledger, no second effect)" if result.get("replayed") else ""))
    elif result["status"] == "blocked":
        emit(s, "execute", f"{action}: BLOCKED by system: {result['reason']}")
    else:
        emit(s, "execute", f"{action}: outcome unknown after retries; verifying live state")
    return {"action_result": result, "idempotency_key": key, "on_hand_before": before, "executed": executed}


def route_after_execute(s: CaseState) -> str:
    return "replan" if s["action_result"]["status"] == "blocked" else "verify"


def verify(s: CaseState) -> Dict[str, Any]:
    plan = s["plan"]
    v = verify_resolution(s["order_id"], plan["action"], amount=plan.get("params", {}).get("amount"),
                          on_hand_before=s.get("on_hand_before"))
    ok = [c["check"] for c in v["checks"] if c["ok"]]
    bad = [c["check"] for c in v["checks"] if not c["ok"]]
    emit(s, "verify", ("PASSED " if v["verified"] else "FAILED ") + f"checks_ok={ok} checks_failed={bad}"
         + ("" if v["verified"] else f" :: {v['reason']}"))
    return {"verification": v}


def route_after_verify(s: CaseState) -> str:
    return "resolve" if s["verification"]["verified"] else "replan"


def replan(s: CaseState) -> Dict[str, Any]:
    res, ver = s["action_result"], s.get("verification") or {}
    kind = "blocked" if res["status"] == "blocked" else "unverified"
    reason = res.get("reason") if kind == "blocked" else ver.get("reason")
    constraints = s["constraints"] + [{"action": s["plan"]["action"], "kind": kind, "reason": reason}]
    attempts = s["attempts"] + 1
    emit(s, "replan", f"{s['plan']['action']} {kind}: {reason}. Attempt {attempts}/{s['max_attempts']}; re-reading live state")
    return {"constraints": constraints, "attempts": attempts, "verification": None}


def route_after_replan(s: CaseState) -> str:
    return "escalate_exhausted" if s["attempts"] >= s["max_attempts"] else "retrieve"


def escalate_exhausted(s: CaseState) -> Dict[str, Any]:
    last = s["constraints"][-1]
    return {"plan": {"action": "escalate", "params": {}, "proposer": "guardrail",
                     "rationale": f"retries exhausted; last problem: {last['action']} {last['kind']}: {last['reason']}"}}


def escalate(s: CaseState) -> Dict[str, Any]:
    v = s["_verdict_obj"]
    reasons = [{"policy": p, "reason": r} for p, r in v.escalate]
    if not reasons:
        reasons = [{"policy": "POL-005", "reason": s["plan"]["rationale"]}]
    brief = comms.handoff_brief(s, reasons)
    emit(s, "escalate", f"HANDOFF to human ({brief['priority']} priority): {reasons[0]['policy']} - {reasons[0]['reason']}")
    return {"outcome": "escalated", "final_action": None, "escalation": brief}


def resolve(s: CaseState) -> Dict[str, Any]:
    emit(s, "resolve", f"case resolved via {s['plan']['action']} (verified against live state)")
    return {"outcome": "resolved", "final_action": s["plan"]["action"]}


def close_duplicate(s: CaseState) -> Dict[str, Any]:
    emit(s, "resolve", "duplicate request: order already handled, no new action taken (POL-010)")
    return {"outcome": "already_resolved", "final_action": None}


def clarify(s: CaseState) -> Dict[str, Any]:
    q = s["_verdict_obj"].clarify
    emit(s, "clarify", f"no action taken yet; asking the customer: \"{q}\"")
    return {"outcome": "needs_clarification", "final_action": None, "question": q}


def respond(s: CaseState) -> Dict[str, Any]:
    reply = comms.customer_reply(s)
    emit(s, "respond", f"reply drafted from verified state: \"{reply}\"")
    return {"customer_reply": reply}


def build_graph(backend: str = "auto"):
    f = Flow(CaseState)
    for name, fn in [("understand", understand), ("retrieve", retrieve), ("assess", assess),
                     ("propose", propose_node), ("guard", guard), ("execute", execute), ("verify", verify),
                     ("replan", replan), ("escalate_exhausted", escalate_exhausted), ("escalate", escalate),
                     ("resolve", resolve), ("close_duplicate", close_duplicate), ("clarify", clarify), ("respond", respond)]:
        f.node(name, fn)
    f.edge("understand", "retrieve").edge("retrieve", "assess").edge("assess", "propose").edge("propose", "guard")
    f.branch("guard", route_after_guard, ["propose", "escalate", "close_duplicate", "clarify", "execute"])
    f.branch("execute", route_after_execute, ["replan", "verify"])
    f.branch("verify", route_after_verify, ["resolve", "replan"])
    f.branch("replan", route_after_replan, ["escalate_exhausted", "retrieve"])
    f.edge("escalate_exhausted", "escalate")
    for t in ("escalate", "resolve", "close_duplicate", "clarify"):
        f.edge(t, "respond")
    f.edge("respond", END)
    return f.compile(backend)
